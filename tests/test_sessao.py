"""A sessão de usuário e como o servidor resolve a credencial de cada chamada."""

from __future__ import annotations

import pytest

from ahreas_mcp.sessao import usuario as sessao
from ahreas_mcp.soap.cliente import Credencial


@pytest.fixture(autouse=True)
def _limpa():
    sessao._limpar_tudo()
    yield
    sessao._limpar_tudo()


def test_abrir_e_obter_sessao():
    cred = Credencial(usuario="FULANO", senha="segredo")
    sessao.abrir("tok1", cred, vida_segundos=60)
    atual = sessao.obter("tok1")
    assert atual is not None
    assert atual.usuario == "FULANO"
    assert atual.credencial.senha == "segredo"


def test_sessao_expirada_e_descartada_na_leitura():
    sessao.abrir("tok2", Credencial("F", "s"), vida_segundos=-1)
    assert sessao.obter("tok2") is None
    # Foi removida do dicionário, não só filtrada.
    assert "tok2" not in sessao._sessoes


def test_encerrar_esquece_a_credencial():
    sessao.abrir("tok3", Credencial("F", "s"), vida_segundos=60)
    sessao.encerrar("tok3")
    assert sessao.obter("tok3") is None


def test_senha_fora_do_repr_da_credencial():
    # A senha não pode vazar num log que imprima a credencial.
    cred = Credencial(usuario="FULANO", senha="nao-deve-aparecer")
    assert "nao-deve-aparecer" not in repr(cred)
    assert "FULANO" in repr(cred)
