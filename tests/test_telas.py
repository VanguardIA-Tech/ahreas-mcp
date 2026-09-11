"""O modo telas: parse de formulário e catálogo, efeito de ação, login e upload.

Os parsers são exercitados com HTML na forma que o Ahreas emite (RadMenu, campos
WebForms, RadGrid). O login e o upload usam respx com a mesma coreografia do ERP —
GET pega o estado, POST o devolve — sem tocar num Ahreas real.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ahreas_mcp.telas import catalogo, executor, formulario
from ahreas_mcp.telas import sessao_web as sw

# --- parsers (puros, sem rede) ----------------------------------------------


def test_formulario_separa_campos_e_acoes():
    html = """
    <form>
      <input type="hidden" name="__VIEWSTATE" value="x"/>
      <input type="text" name="txtCondominio" title="Condomínio"/>
      <select name="cboTipo"><option value="1">Um</option><option value="2">Dois</option></select>
      <input type="checkbox" name="ckSepara"/>
      <input type="submit" name="btnFiltrar" value="Filtrar"/>
      <a href="javascript:__doPostBack('lnkExportar','')">Exportar</a>
    </form>
    """
    form = formulario.analisar(html)
    nomes = {c.nome for c in form.campos}
    assert nomes == {"txtCondominio", "cboTipo", "ckSepara"}  # __VIEWSTATE fica de fora
    tipos = {c.nome: c.tipo for c in form.campos}
    assert tipos["cboTipo"] == "select"
    alvos = {a.alvo for a in form.acoes}
    assert {"btnFiltrar", "lnkExportar"} <= alvos


def test_catalogo_extrai_telas_do_radmenu():
    html = (
        '<span class="rmLink" target="/condominioweb/receber/rateio/Tarifa.aspx">'
        "Tarifas de Consumo</span>"
        '<span class="rmLink" target="/condominioweb/receber/recibos/boletos.aspx">'
        "Boletos Bancários</span>"
    )
    telas = catalogo._extrair(html, "condominioweb")
    caminhos = {t.caminho for t in telas}
    assert "/receber/rateio/Tarifa.aspx" in caminhos
    nomes = {t.nome for t in telas}
    assert "Tarifas de Consumo" in nomes


def test_efeito_da_acao():
    assert executor.efeito_da_acao("btnProcessar") is executor.EfeitoAcao.ESCRITA
    assert executor.efeito_da_acao("btnFiltrar") is executor.EfeitoAcao.LEITURA
    assert executor.efeito_da_acao("btnConsultar") is executor.EfeitoAcao.LEITURA
    # Desconhecido, sem pista -> escrita (pede confirmação).
    assert executor.efeito_da_acao("btnXPTO") is executor.EfeitoAcao.ESCRITA


def test_extrai_tabela_de_radgrid():
    html = """
    <table>
      <tr class="rgRow"><td>Apto 101</td><td>R$ 150,00</td></tr>
      <tr class="rgAltRow"><td>Apto 102</td><td>R$ 220,00</td></tr>
    </table>
    """
    _, linhas = executor.extrair_tabela(html)
    assert linhas == [["Apto 101", "R$ 150,00"], ["Apto 102", "R$ 220,00"]]


def test_mensagem_ignora_lixo_de_script():
    # O regex de mensagem não pode devolver JavaScript.
    html = '<span id="lblMensagem">function(x){return x}</span>'
    assert executor._extrair_mensagem(html) is None
    html_ok = '<span id="lblMensagem">Importação concluída com sucesso.</span>'
    assert "sucesso" in (executor._extrair_mensagem(html_ok) or "")


# --- login e upload (respx) --------------------------------------------------

_BASE = "https://ahreas.teste.local/condominioweb"


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("AHREAS_BASE_URL", "https://ahreas.teste.local")
    monkeypatch.setenv("AHREAS_CHAVE", "k")
    sw.configuracao.cache_clear()


def _pagina_login() -> str:
    return (
        '<form><input type="hidden" name="__VIEWSTATE" value="v"/>'
        '<input type="hidden" name="__EVENTVALIDATION" value="e"/>'
        '<input name="goLogin$idusuario" id="goLogin_idusuario"/>'
        '<input name="goLogin$idpassword" id="goLogin_idpassword" type="password"/></form>'
    )


def _home_logada() -> str:
    return "<div>Olá, Fulano</div>"  # sem goLogin_idusuario => logado


@respx.mock
async def test_login_web_bem_sucedido():
    respx.get(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_pagina_login()))
    respx.post(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_home_logada()))
    sessao = await sw.entrar("fulano@teste.com", "senha")
    assert isinstance(sessao, sw.SessaoWeb)


@respx.mock
async def test_login_web_recusado():
    respx.get(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_pagina_login()))
    # Resposta ainda com o campo de login => recusado.
    respx.post(f"{_BASE}/").mock(return_value=httpx.Response(200, text=_pagina_login()))
    with pytest.raises(sw.LoginWebRecusado):
        await sw.entrar("fulano@teste.com", "errada")


@respx.mock
async def test_upload_monta_client_state():
    html_tela = (
        '$create(Telerik.Web.UI.RadAsyncUpload, {"_serializedConfiguration":"CFG",'
        '"_serializedConfigurationType":"TIPO"}, null, null, $get("RdUpArquivo"));'
    )
    respx.post(f"{_BASE}/Telerik.Web.UI.WebResource.axd").mock(
        return_value=httpx.Response(
            200,
            json={
                "fileInfo": {"FileName": "a.txt", "ContentLength": 3},
                "metaData": "TOKEN123",
            },
        )
    )
    sessao = sw.SessaoWeb(base_web=_BASE, cookies={})
    estado = await sessao.enviar_arquivo(html_tela, "/tela.aspx", "a.txt", b"abc", "text/plain")
    assert "TOKEN123" in estado
    assert "uploadedFiles" in estado
