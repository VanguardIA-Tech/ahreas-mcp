"""O servidor de autorização OAuth 2.1 com login no Ahreas.

O cliente MCP (Claude, GPT, Cursor) fala OAuth padrão: descobre os metadados, se
registra (DCR), manda a pessoa autorizar e troca o código por um token. A única
parte própria é a tela de autorização: em vez de aprovar sozinha, ela pede
usuário e senha do Ahreas e valida com `validar_credencial` — a mesma checagem
que o resto do servidor faz por SOAP.

O token de acesso é opaco e É a chave da sessão: enquanto ele vale, existe uma
`SessaoUsuario` com a credencial daquela pessoa. Assim cada execução no Ahreas
sai no nome de quem pediu, com as permissões dele — não um usuário técnico
compartilhado.

Escrito contra a API do fastmcp 4.x: o método a implementar é `load_access_token`
(o `verify_token` da base já delega para ele).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

# AccessToken vem do fastmcp (subclasse do mcp): é o tipo que a base
# OAuthProvider declara em load_access_token/verify_token, então usá-lo mantém a
# assinatura compatível. O resto do contrato OAuth vem do SDK mcp.
from fastmcp.server.auth.auth import AccessToken, ClientRegistrationOptions, OAuthProvider
from mcp.server.auth.provider import (
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from ahreas_mcp.auth.paginas import pagina_login
from ahreas_mcp.configuracao import configuracao
from ahreas_mcp.sessao import usuario as sessao
from ahreas_mcp.soap.cliente import Credencial
from ahreas_mcp.telas.sessao_web import LoginWebRecusado, entrar

_VALIDADE_CODE_SEGUNDOS = 5 * 60


class _Pendencia:
    """Uma autorização que começou e espera a pessoa fazer login."""

    __slots__ = ("client", "params", "criada_em")

    def __init__(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> None:
        self.client = client
        self.params = params
        self.criada_em = time.time()


class _CodeComCredencial:
    """Um código de autorização já ligado a uma credencial e à sessão web."""

    __slots__ = ("code", "credencial", "web")

    def __init__(self, code: AuthorizationCode, credencial: Credencial, web: Any) -> None:
        self.code = code
        self.credencial = credencial
        self.web = web


class AhreasAuthProvider(OAuthProvider):
    def __init__(self) -> None:
        conf = configuracao()
        if conf.public_url is None:
            raise ValueError("AHREAS_PUBLIC_URL é obrigatório para o modo remoto (OAuth).")
        super().__init__(
            base_url=conf.public_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
        )
        self._clientes: dict[str, OAuthClientInformationFull] = {}
        self._pendencias: dict[str, _Pendencia] = {}
        self._codes: dict[str, _CodeComCredencial] = {}
        self._tokens: dict[str, AccessToken] = {}

    # --- registro de cliente (DCR) ----------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self._clientes.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if client_info.client_id is None:
            raise ValueError("client_id é obrigatório no registro do cliente.")
        self._clientes[client_info.client_id] = client_info

    # --- autorização: manda para o login ----------------------------------
    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        if client.client_id not in self._clientes:
            raise AuthorizeError(
                error="unauthorized_client", error_description="Cliente não registrado."
            )
        pedido = secrets.token_urlsafe(24)
        self._pendencias[pedido] = _Pendencia(client, params)
        # O navegador vai para a tela de login própria; o código só nasce depois
        # que a pessoa autentica com sucesso no Ahreas.
        return f"{configuracao().public_url}/ahreas/login?pedido={pedido}"

    async def _emitir_code(self, pendencia: _Pendencia, credencial: Credencial, web: Any) -> str:
        params = pendencia.params
        valor = f"ac_{secrets.token_hex(24)}"
        code = AuthorizationCode(
            code=valor,
            client_id=pendencia.client.client_id or "",
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            scopes=params.scopes or [],
            expires_at=time.time() + _VALIDADE_CODE_SEGUNDOS,
            code_challenge=params.code_challenge,
            resource=params.resource,
        )
        self._codes[valor] = _CodeComCredencial(code, credencial, web)
        return construct_redirect_uri(str(params.redirect_uri), code=valor, state=params.state)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        entrada = self._codes.get(authorization_code)
        if entrada is None or entrada.code.client_id != client.client_id:
            return None
        if entrada.code.expires_at < time.time():
            self._codes.pop(authorization_code, None)
            return None
        return entrada.code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        entrada = self._codes.pop(authorization_code.code, None)
        if entrada is None:
            raise TokenError("invalid_grant", "Código inválido ou já usado.")

        token = f"at_{secrets.token_hex(32)}"
        vida = configuracao().sessao_horas * 3600
        expira = int(time.time() + vida)
        self._tokens[token] = AccessToken(
            token=token,
            client_id=client.client_id or "",
            scopes=authorization_code.scopes,
            expires_at=expira,
            subject=entrada.credencial.usuario,
        )
        # O token de acesso é a chave da sessão: a credencial do Ahreas e a
        # sessão web vivem aqui, em memória, e somem quando o token expira ou é
        # revogado.
        sessao.abrir(token, entrada.credencial, vida, web=entrada.web)
        return OAuthToken(
            access_token=token,
            token_type="Bearer",
            expires_in=vida,
            scope=" ".join(authorization_code.scopes),
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        guardado = self._tokens.get(token)
        if guardado is None:
            return None
        if guardado.expires_at is not None and guardado.expires_at < time.time():
            self._tokens.pop(token, None)
            sessao.encerrar(token)
            return None
        return guardado

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        self._tokens.pop(token.token, None)
        sessao.encerrar(token.token)

    # OAuth 2.1 sem refresh: quando a sessão expira, a pessoa faz login de novo.
    async def load_refresh_token(self, client: Any, refresh_token: str) -> None:
        return None

    async def exchange_refresh_token(self, *args: Any, **kwargs: Any) -> OAuthToken:
        raise TokenError("unsupported_grant_type", "Este servidor não usa refresh token.")

    # --- rotas próprias: a tela de login ----------------------------------
    def get_routes(self, mcp_path: str | None = None) -> list[Route]:
        rotas = super().get_routes(mcp_path)
        rotas.append(Route("/ahreas/login", self._get_login, methods=["GET"]))
        rotas.append(Route("/ahreas/login", self._post_login, methods=["POST"]))
        return rotas

    async def _get_login(self, request: Request) -> Response:
        pedido = request.query_params.get("pedido", "")
        if pedido not in self._pendencias:
            return HTMLResponse("Pedido de login inválido ou expirado.", status_code=400)
        return HTMLResponse(pagina_login(pedido))

    async def _post_login(self, request: Request) -> Response:
        form = await request.form()
        pedido = str(form.get("pedido", ""))
        email = str(form.get("usuario", "")).strip()
        senha = str(form.get("senha", ""))
        pendencia = self._pendencias.get(pedido)
        if pendencia is None:
            return HTMLResponse("Pedido de login inválido ou expirado.", status_code=400)

        # O login web valida a credencial e abre a sessão do painel numa tacada.
        # A mesma credencial serve para o web service (o Ahreas aceita o e-mail
        # como usuário), então uma sessão cobre os dois modos.
        credencial = Credencial(usuario=email, senha=senha)
        try:
            web = await entrar(email, senha)
        except LoginWebRecusado:
            return HTMLResponse(
                pagina_login(pedido, "E-mail ou senha incorretos."), status_code=200
            )
        except Exception:  # noqa: BLE001 - Ahreas fora do ar vira mensagem
            return HTMLResponse(
                pagina_login(pedido, "O Ahreas não respondeu. Tente de novo em instantes."),
                status_code=200,
            )

        self._pendencias.pop(pedido, None)
        destino = await self._emitir_code(pendencia, credencial, web)
        return RedirectResponse(destino, status_code=303)
