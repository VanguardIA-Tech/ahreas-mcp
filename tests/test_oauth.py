"""O fluxo OAuth do modo remoto, ponta a ponta pelas rotas HTTP reais.

Exercita a dança inteira contra o app ASGI — metadata, DCR, authorize, a tela de
login própria e a troca de código por token —, com o SOAP `ValidaCredencial`
mockado. É assim que se garante que uma pessoa entra com o usuário e senha dela e
sai com uma sessão ligada a essa credencial, sem tocar num Ahreas de verdade.

O e2e contra o ERP real (login da pessoa → leitura chegando ao SOAP como ela) é
feito à mão com credencial de verdade; aqui fica a parte que a CI roda sempre.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from ahreas_mcp import servidor
from ahreas_mcp.catalogo import wsdl
from ahreas_mcp.sessao import usuario as sessao



_WEB = "https://ahreas.teste.local/condominioweb"


def _pagina_login_web() -> str:
    return (
        '<form><input type="hidden" name="__VIEWSTATE" value="v"/>'
        '<input type="hidden" name="__EVENTVALIDATION" value="e"/>'
        '<input name="goLogin$idusuario" id="goLogin_idusuario"/>'
        '<input name="goLogin$idpassword" id="goLogin_idpassword" type="password"/></form>'
    )


def _mock_login_web(*, sucesso: bool) -> None:
    # O login web: GET pega o estado, POST autentica. Sucesso = home sem o campo
    # de login; recusa = a própria tela de login de volta.
    respx.get(f"{_WEB}/").mock(return_value=httpx.Response(200, text=_pagina_login_web()))
    home = "<div>Olá</div>" if sucesso else _pagina_login_web()
    respx.post(f"{_WEB}/").mock(return_value=httpx.Response(200, text=home))


@pytest.fixture(autouse=True)
def _config(monkeypatch):
    monkeypatch.setenv("AHREAS_BASE_URL", "https://ahreas.teste.local")
    monkeypatch.setenv("AHREAS_CHAVE", "chave-da-adm")
    monkeypatch.setenv("AHREAS_PUBLIC_URL", "http://localhost")
    monkeypatch.delenv("AHREAS_USUARIO", raising=False)
    monkeypatch.delenv("AHREAS_SENHA", raising=False)
    servidor.configuracao.cache_clear()
    wsdl.configuracao.cache_clear()
    wsdl._catalogo = None
    sessao._limpar_tudo()
    # Reconstrói o app com o auth ligado (o provider lê a config no import do mcp).
    import importlib

    importlib.reload(servidor)
    yield
    sessao._limpar_tudo()


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge.rstrip(b"=").decode()


async def _cliente(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport, base_url="http://localhost", follow_redirects=False
    )


async def _ate_o_login(c) -> str:
    """Faz metadata + DCR + authorize e devolve o `pedido` da tela de login."""
    await c.get("/.well-known/oauth-authorization-server")
    reg = await c.post(
        "/register",
        json={
            "client_name": "teste",
            "redirect_uris": ["http://cliente/cb"],
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
    )
    cid = reg.json()["client_id"]
    _, challenge = _pkce()
    r = await c.get(
        "/authorize",
        params={
            "response_type": "code",
            "client_id": cid,
            "redirect_uri": "http://cliente/cb",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "xyz",
        },
    )
    assert r.status_code in (302, 307)
    return parse_qs(urlparse(r.headers["location"]).query)["pedido"][0]


@respx.mock
async def test_login_valido_emite_code_e_abre_sessao():
    _mock_login_web(sucesso=True)
    app = servidor.mcp.http_app(path="/mcp")
    async with await _cliente(app) as c, app.router.lifespan_context(app):
        pedido = await _ate_o_login(c)
        r = await c.post(
            "/ahreas/login", data={"pedido": pedido, "usuario": "FULANO", "senha": "boa"}
        )
        assert r.status_code == 303
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        assert code.startswith("ac_")


@respx.mock
async def test_login_recusado_nao_emite_code():
    _mock_login_web(sucesso=False)
    app = servidor.mcp.http_app(path="/mcp")
    async with await _cliente(app) as c, app.router.lifespan_context(app):
        pedido = await _ate_o_login(c)
        r = await c.post(
            "/ahreas/login", data={"pedido": pedido, "usuario": "FULANO", "senha": "errada"}
        )
        # Fica na tela de login (200), sem redirecionar com código.
        assert r.status_code == 200
        assert "incorreto" in r.text.lower()


@respx.mock
async def test_dança_completa_ate_o_token():
    _mock_login_web(sucesso=True)
    app = servidor.mcp.http_app(path="/mcp")
    async with await _cliente(app) as c, app.router.lifespan_context(app):
        # DCR
        cid = (
            await c.post(
                "/register",
                json={
                    "client_name": "teste",
                    "redirect_uris": ["http://cliente/cb"],
                    "grant_types": ["authorization_code"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                },
            )
        ).json()["client_id"]
        verifier, challenge = _pkce()
        r = await c.get(
            "/authorize",
            params={
                "response_type": "code",
                "client_id": cid,
                "redirect_uri": "http://cliente/cb",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "xyz",
            },
        )
        pedido = parse_qs(urlparse(r.headers["location"]).query)["pedido"][0]
        r = await c.post(
            "/ahreas/login", data={"pedido": pedido, "usuario": "FULANO", "senha": "boa"}
        )
        code = parse_qs(urlparse(r.headers["location"]).query)["code"][0]
        r = await c.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": "http://cliente/cb",
                "client_id": cid,
                "code_verifier": verifier,
            },
        )
        assert r.status_code == 200
        tok = r.json()
        assert tok["access_token"].startswith("at_")
        # A sessão foi aberta e carrega a credencial de quem logou.
        atual = sessao.obter(tok["access_token"])
        assert atual is not None
        assert atual.usuario == "FULANO"
