"""O servidor pelo protocolo MCP: as tools existem e a proteção de escrita vale.

Usa o cliente in-memory do FastMCP — exercita o mesmo caminho que um cliente MCP
de verdade percorre, sem rede. O WSDL e as chamadas SOAP são mockados, então o
que se testa é a lógica do servidor: catálogo, confirmação e trava de escrita.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastmcp import Client

from ahreas_mcp import servidor
from ahreas_mcp.catalogo import wsdl

_WSDL = """<?xml version="1.0"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
  xmlns:s="http://www.w3.org/2001/XMLSchema">
  <wsdl:types><s:schema>
    <s:element name="LancamentosContabeis_Inserir">
      <s:complexType><s:sequence>
        <s:element name="condominio" type="s:int"/>
        <s:element name="valor" type="s:decimal"/>
        <s:element name="usuario" type="s:string"/>
        <s:element name="senha" type="s:string"/>
        <s:element name="chave" type="s:string"/>
      </s:sequence></s:complexType>
    </s:element>
    <s:element name="TaxaInadimplencia_XML">
      <s:complexType><s:sequence>
        <s:element name="condominio" type="s:int"/>
        <s:element name="usuario" type="s:string"/>
        <s:element name="senha" type="s:string"/>
        <s:element name="chave" type="s:string"/>
      </s:sequence></s:complexType>
    </s:element>
  </s:schema></wsdl:types>
  <wsdl:portType>
    <wsdl:operation name="LancamentosContabeis_Inserir"/>
    <wsdl:operation name="TaxaInadimplencia_XML"/>
  </wsdl:portType>
</wsdl:definitions>
"""


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("AHREAS_BASE_URL", "https://ahreas.teste.local")
    monkeypatch.setenv("AHREAS_USUARIO", "u")
    monkeypatch.setenv("AHREAS_SENHA", "s")
    monkeypatch.setenv("AHREAS_CHAVE", "k")
    monkeypatch.delenv("AHREAS_PERMITIR_ESCRITA", raising=False)
    wsdl.configuracao.cache_clear()
    servidor.configuracao.cache_clear()
    wsdl._catalogo = None


def _mock_wsdl():
    for servico in wsdl.SERVICOS:
        respx.get(f"https://ahreas.teste.local/{servico}/wsdocumentos.asmx?WSDL").mock(
            return_value=httpx.Response(200, text=_WSDL)
        )


@respx.mock
async def test_lista_as_quatro_ferramentas():
    _mock_wsdl()
    async with Client(servidor.mcp) as c:
        nomes = {t.name for t in await c.list_tools()}
    assert nomes == {
        "listar_funcionalidades",
        "descrever_metodo",
        "executar_metodo",
        "diagnostico",
    }


@respx.mock
async def test_escrita_sem_confirmar_nao_chega_ao_erp():
    _mock_wsdl()
    rota = respx.post("https://ahreas.teste.local/Condominioweb/wsdocumentos.asmx")
    async with Client(servidor.mcp) as c:
        r = await c.call_tool(
            "executar_metodo",
            {"nome": "LancamentosContabeis_Inserir", "parametros": {"condominio": 2}},
        )
    assert r.data["status"] == "precisa_confirmar"
    assert not rota.called  # nada saiu para o Ahreas


@respx.mock
async def test_escrita_confirmada_bloqueada_quando_desligada():
    _mock_wsdl()
    rota = respx.post("https://ahreas.teste.local/Condominioweb/wsdocumentos.asmx")
    async with Client(servidor.mcp) as c:
        r = await c.call_tool(
            "executar_metodo",
            {
                "nome": "LancamentosContabeis_Inserir",
                "parametros": {"condominio": 2},
                "confirmar": True,
            },
        )
    assert r.data["erro"]["codigo"] == "ahreas.escrita.desligada"
    assert not rota.called


@respx.mock
async def test_leitura_executa_e_devolve_conteudo():
    _mock_wsdl()
    corpo = (
        '<?xml version="1.0"?><soap:Envelope '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/"><soap:Body>'
        '<TaxaInadimplencia_XMLResponse xmlns="http://gosati.com.br/webservices/">'
        "<TaxaInadimplencia_XMLResult>&lt;ok/&gt;</TaxaInadimplencia_XMLResult>"
        "</TaxaInadimplencia_XMLResponse></soap:Body></soap:Envelope>"
    )
    for servico in wsdl.SERVICOS:
        respx.post(f"https://ahreas.teste.local/{servico}/wsdocumentos.asmx").mock(
            return_value=httpx.Response(200, text=corpo)
        )
    async with Client(servidor.mcp) as c:
        r = await c.call_tool(
            "executar_metodo",
            {"nome": "TaxaInadimplencia_XML", "parametros": {"condominio": 2}},
        )
    assert r.data["ok"] is True
    assert r.data["conteudo"] == "<ok/>"
