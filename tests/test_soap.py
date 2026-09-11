"""O cliente SOAP: envelope, extração de resultado e classificação de fault.

Testado no comportamento observável — o XML de entrada é o que o Ahreas devolve
de verdade (fault de negócio, sem-licença, DataSet embutido, gzip escondido),
e o que se verifica é como cada forma vira resposta ou exceção.
"""

from __future__ import annotations

import gzip

import httpx
import pytest
import respx

from ahreas_mcp.soap import cliente


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("AHREAS_BASE_URL", "https://ahreas.teste.local")
    monkeypatch.setenv("AHREAS_USUARIO", "u")
    monkeypatch.setenv("AHREAS_SENHA", "s")
    monkeypatch.setenv("AHREAS_CHAVE", "k")
    cliente.configuracao.cache_clear()


def _envelope_result(metodo: str, interno_escapado: str) -> str:
    return (
        '<?xml version="1.0"?><soap:Envelope '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<soap:Body><{metodo}Response xmlns="http://gosati.com.br/webservices/">'
        f"<{metodo}Result>{interno_escapado}</{metodo}Result>"
        f"</{metodo}Response></soap:Body></soap:Envelope>"
    )


def _fault(faultstring: str) -> str:
    return (
        '<?xml version="1.0"?><soap:Envelope '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">'
        f"<soap:Body><soap:Fault><faultcode>soap:Server</faultcode>"
        f"<faultstring>{faultstring}</faultstring></soap:Fault>"
        "</soap:Body></soap:Envelope>"
    )


_URL = "https://ahreas.teste.local/Condominioweb/wsdocumentos.asmx"


@respx.mock
async def test_leitura_desescapa_o_xml_interno():
    corpo = _envelope_result("Metodo", "&lt;raiz&gt;&lt;x&gt;1&lt;/x&gt;&lt;/raiz&gt;")
    respx.post(_URL).mock(return_value=httpx.Response(200, text=corpo))
    r = await cliente.chamar("Condominioweb", "Metodo", {"a": 1})
    assert r.conteudo == "<raiz><x>1</x></raiz>"
    assert r.tamanho == len(r.conteudo)


@respx.mock
async def test_ampersand_no_conteudo_nao_e_desescapado_duas_vezes():
    # Um condomínio "A & B": no XML interno é `A &amp; B`, e esse XML interno
    # chega no Result escapado mais uma vez (`&amp;amp;`). Uma decodificação só é
    # o correto — o resultado tem de manter o `&amp;` de marcação intacto.
    interno = "&lt;nome&gt;A &amp;amp; B&lt;/nome&gt;"
    respx.post(_URL).mock(
        return_value=httpx.Response(200, text=_envelope_result("Metodo", interno))
    )
    r = await cliente.chamar("Condominioweb", "Metodo", {})
    assert r.conteudo == "<nome>A &amp; B</nome>"


@respx.mock
async def test_fault_de_negocio_vira_resposta_vazia():
    texto = (
        "System.Web.Services.Protocols.SoapException: ... ---> "
        "System.Exception: Não foi encontrado nenhum condomínio a processar\n em Global"
    )
    respx.post(_URL).mock(return_value=httpx.Response(500, text=_fault(texto)))
    with pytest.raises(cliente.RespostaVazia):
        await cliente.chamar("Condominioweb", "Metodo", {})


@respx.mock
async def test_fault_de_licenca_vira_sem_licenca():
    texto = "System.Exception: Você não possui acesso para utilizar esta função.\n em Global"
    respx.post(_URL).mock(return_value=httpx.Response(500, text=_fault(texto)))
    with pytest.raises(cliente.SemLicenca):
        await cliente.chamar("Condominioweb", "Metodo", {})


@respx.mock
async def test_fault_de_validacao_vira_falta_parametro():
    texto = "System.Exception: É necessário informar a inscrição do cliente\n em Global"
    respx.post(_URL).mock(return_value=httpx.Response(500, text=_fault(texto)))
    with pytest.raises(cliente.FaltaParametro) as erro:
        await cliente.chamar("Condominioweb", "Metodo", {})
    assert "inscrição" in str(erro.value)


@respx.mock
async def test_newdataset_vazio_vira_resposta_vazia():
    corpo = _envelope_result("Metodo", "&lt;NewDataSet /&gt;")
    respx.post(_URL).mock(return_value=httpx.Response(200, text=corpo))
    with pytest.raises(cliente.RespostaVazia):
        await cliente.chamar("Condominioweb", "Metodo", {})


@respx.mock
async def test_gzip_sem_header_e_decodificado():
    corpo = _envelope_result("Metodo", "&lt;ok/&gt;")
    comprimido = gzip.compress(corpo.encode("utf-8"))
    respx.post(_URL).mock(return_value=httpx.Response(200, content=comprimido))
    r = await cliente.chamar("Condominioweb", "Metodo", {})
    assert r.conteudo == "<ok/>"


@respx.mock
async def test_credenciais_entram_no_corpo_e_nao_sao_parametro():
    capturado = {}

    def responder(request):
        capturado["corpo"] = request.content.decode("utf-8")
        return httpx.Response(200, text=_envelope_result("Metodo", "&lt;ok/&gt;"))

    respx.post(_URL).mock(side_effect=responder)
    await cliente.chamar("Condominioweb", "Metodo", {"condominio": 2})
    assert "<usuario>u</usuario>" in capturado["corpo"]
    assert "<chave>k</chave>" in capturado["corpo"]
    assert "<condominio>2</condominio>" in capturado["corpo"]
