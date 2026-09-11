"""O catálogo lido do WSDL: parse de operações, parâmetros e busca.

O WSDL é servido por um mock com a forma real (element de request com
complexType inline, response ignorado, credenciais escondidas), então o parser
é exercido pela mesma estrutura que o Ahreas publica.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ahreas_mcp.catalogo import wsdl

_WSDL = """<?xml version="1.0"?>
<wsdl:definitions xmlns:wsdl="http://schemas.xmlsoap.org/wsdl/"
  xmlns:s="http://www.w3.org/2001/XMLSchema">
  <wsdl:types>
    <s:schema>
      <s:element name="TaxaInadimplencia_XML">
        <s:complexType><s:sequence>
          <s:element name="condominio" type="s:int"/>
          <s:element name="mes" type="s:int"/>
          <s:element name="usuario" type="s:string"/>
          <s:element name="senha" type="s:string"/>
          <s:element name="chave" type="s:string"/>
        </s:sequence></s:complexType>
      </s:element>
      <s:element name="TaxaInadimplencia_XMLResponse">
        <s:complexType><s:sequence>
          <s:element name="TaxaInadimplencia_XMLResult" type="s:string"/>
        </s:sequence></s:complexType>
      </s:element>
    </s:schema>
  </wsdl:types>
  <wsdl:portType>
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
    wsdl.configuracao.cache_clear()
    wsdl._catalogo = None  # zera o cache do módulo entre testes


def _mock_dois_servicos():
    for servico in wsdl.SERVICOS:
        url = f"https://ahreas.teste.local/{servico}/wsdocumentos.asmx?WSDL"
        respx.get(url).mock(return_value=httpx.Response(200, text=_WSDL))


@respx.mock
async def test_le_metodos_dos_dois_servicos():
    _mock_dois_servicos()
    metodos = await wsdl.todos()
    # Um método por serviço; o Response não vira método.
    assert {m.servico for m in metodos} == set(wsdl.SERVICOS)
    assert all(m.nome == "TaxaInadimplencia_XML" for m in metodos)


@respx.mock
async def test_credenciais_ficam_de_fora_dos_parametros():
    _mock_dois_servicos()
    m = await wsdl.metodo("TaxaInadimplencia_XML")
    nomes = {p.nome for p in m.parametros}
    assert nomes == {"condominio", "mes"}
    assert "usuario" not in nomes and "chave" not in nomes


@respx.mock
async def test_busca_ignora_acento_e_caixa():
    _mock_dois_servicos()
    assert await wsdl.buscar("TAXAINADIMPLENCIA")
    assert await wsdl.buscar("inadimplencia")
    assert not await wsdl.buscar("inexistente")


@respx.mock
async def test_busca_por_intencao_multipalavra_sem_ordem():
    _mock_dois_servicos()
    # "taxa inadimplencia" e a ordem trocada acham SegundaViaBoletos-style names.
    assert await wsdl.buscar("taxa inadimplencia")
    assert await wsdl.buscar("inadimplencia taxa")
    # Uma palavra que não casa derruba o resultado inteiro (AND, não OR).
    assert not await wsdl.buscar("taxa inexistente")
