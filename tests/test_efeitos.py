"""A revisão de efeito: o que protege a execução genérica.

O risco que estes testes cobrem é um só e é grave: um método que grava ser
tratado como leitura e rodar sem confirmação. Por isso a heurística de fallback
é conservadora, e isso é verificado explicitamente.
"""

from __future__ import annotations

import pytest

from ahreas_mcp.semantica.efeitos import Efeito, base, efeito, grava


@pytest.mark.parametrize(
    "nome,esperado",
    [
        ("LancamentosContabeis_Inserir", Efeito.ESCRITA),
        ("AprovacaoPagtosProcessar", Efeito.ESCRITA),
        ("RecepcaoContaAPagar_Documentos", Efeito.ESCRITA),
        ("AtualizacaoCadastral_Aut_Dados", Efeito.ESCRITA),
        # Nome sugere aprovação, mas só consulta — verificado na sondagem.
        ("AprovacaoPagtosConsultar", Efeito.LEITURA),
        ("ContasParaAprovacao", Efeito.LEITURA),
        # Consulta simples.
        ("TaxaInadimplencia_XML", Efeito.LEITURA),
        ("ListagemBoletos", Efeito.LEITURA),
        # Efeito não verificado -> incerto.
        ("CartaCobranca", Efeito.INCERTO),
        ("SegundaViaBoletos_Chatbot", Efeito.INCERTO),
        # Família de atualização cadastral: recebe dados como entrada, nunca
        # deve rodar sem confirmação.
        ("AtualizacaoCadastral_Documentos", Efeito.INCERTO),
        ("AtualizacaoCadastral_Dados", Efeito.INCERTO),
        # Consultar continua leitura mesmo com "Aprovacao" no radical.
        ("AprovacaoPagtosConsultarXML", Efeito.LEITURA),
    ],
)
def test_efeito_revisado(nome, esperado):
    assert efeito(nome) is esperado


def test_variantes_de_formato_compartilham_efeito():
    # Sufixo com e sem underscore — o Ahreas usa os dois.
    for sufixo in ("", "_XML", "_Json", "XML"):
        assert efeito(f"LancamentosContabeis_Inserir{sufixo}") is Efeito.ESCRITA


def test_variante_colada_resolve_o_nome_base():
    assert base("AprovacaoPagtosProcessarXML") == "AprovacaoPagtosProcessar"


def test_base_remove_sufixo():
    assert base("PrevisaoOrcamentaria_XML") == "PrevisaoOrcamentaria"
    assert base("RetornaArquivo_Url") == "RetornaArquivo"


def test_metodo_desconhecido_com_cara_de_escrita_nao_e_leitura():
    # Um método novo que o Ahreas adicione: se o nome cheira a escrita, cai em
    # incerto (pede confirmação), nunca em leitura silenciosa.
    assert efeito("GravarAlgumaCoisaNova") is Efeito.INCERTO
    assert grava("GravarAlgumaCoisaNova")


def test_metodo_desconhecido_de_consulta_e_leitura():
    assert efeito("ConsultaAlgoInedito") is Efeito.LEITURA
    assert not grava("ConsultaAlgoInedito")
