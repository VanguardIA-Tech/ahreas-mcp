"""A sessão web do Ahreas, replicando o navegador por HTTP puro.

O Ahreas web é ASP.NET WebForms: tudo é postback de formulário com `__VIEWSTATE`
e a autenticação vive num cookie. Não há API REST por trás das telas, então a
única forma de fazer pela integração o que uma pessoa faz na tela é falar o mesmo
protocolo que o navegador dela fala — sem navegador.

Isto é o oposto de dirigir um browser: não há Playwright, não há página renderizada.
É um cliente HTTP com cookie que faz login como a pessoa, lê o estado escondido de
cada tela e reenvia o postback que o botão dispararia. Frágil pelo mesmo motivo
que qualquer integração com tela é — se o Ahreas muda a tela, muda o formulário —
e essa fragilidade é assumida: é o preço de alcançar o que o web service não expõe.
"""

from __future__ import annotations

import html as htmlmod
import json
import re
from dataclasses import dataclass, field

import httpx

from ahreas_mcp.configuracao import configuracao

# O controle de upload do Ahreas é o Telerik RadAsyncUpload: o arquivo sobe
# assíncrono para este handler e volta um token (metaData) que representa o
# arquivo já guardado; o postback seguinte processa a partir dele.
_HANDLER_UPLOAD = "/Telerik.Web.UI.WebResource.axd?type=rau"
# O rauPostData é a junção destes dois campos do $create do controle, na ordem.
_RE_CONFIG = re.compile(r'"_serializedConfiguration":"([^"]+)"')
_RE_CONFIG_TIPO = re.compile(r'"_serializedConfigurationType":"([^"]+)"')
# O id do controle de upload, para descobrir o campo `<id>_ClientState` de cada
# tela — não presumir um nome fixo, que só valeria para uma tela.
_RE_CONTROLE_UPLOAD = re.compile(r'<div[^>]*id="([^"]+)"[^>]*class="[^"]*RadAsyncUpload', re.I)

# Campos de estado do WebForms que todo postback precisa devolver intactos.
_ESTADO = ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION", "__VIEWSTATEENCRYPTED")
# O botão de login não é o `btnEntrar` visível: ele dispara este alvo de postback.
_ALVO_LOGIN = "goLogin$hbLogin"

_INPUT = re.compile(r"<input\b[^>]*>", re.I)
_NAME = re.compile(r'name="([^"]+)"')
_VALUE = re.compile(r'value="([^"]*)"')


class LoginWebRecusado(Exception):
    """O Ahreas web recusou o e-mail e a senha de operador."""


class TelaIndisponivel(Exception):
    """A tela não respondeu, ou respondeu algo incompreensível."""


def _campos_ocultos(html: str) -> dict[str, str]:
    """name -> value de todos os inputs de uma página.

    Preserva o estado do WebForms (ViewState e afins) e os valores atuais dos
    campos, que o postback tem de reenviar por inteiro.
    """
    campos: dict[str, str] = {}
    for m in _INPUT.finditer(html):
        tag = m.group(0)
        nome = _NAME.search(tag)
        if not nome:
            continue
        valor = _VALUE.search(tag)
        campos[nome.group(1)] = htmlmod.unescape(valor.group(1)) if valor else ""
    return campos


def _logado(html: str) -> bool:
    """A presença do campo de usuário do login é como o Ahreas diz 'deslogado'."""
    return "goLogin_idusuario" not in html


@dataclass
class SessaoWeb:
    """Uma sessão autenticada no Ahreas web, viva enquanto o cookie valer.

    Guarda o cookie de autenticação (nunca a senha) e o base do módulo. Cada
    chamada abre um cliente curto com esse cookie — o estado que importa é o
    cookie, e mantê-lo é o suficiente para o servidor reconhecer a pessoa.
    """

    base_web: str
    cookies: dict[str, str] = field(default_factory=dict)

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_web,
            cookies=self.cookies,
            follow_redirects=True,
            timeout=configuracao().timeout_segundos,
        )

    async def abrir(self, caminho: str) -> tuple[str, dict[str, str]]:
        """GET numa tela: devolve o HTML e o estado oculto para o próximo postback."""
        async with self._cliente() as c:
            r = await c.get(caminho)
            self.cookies.update({k: v for k, v in r.cookies.items()})
        if not _logado(r.text):
            raise LoginWebRecusado("A sessão web expirou. Faça login novamente.")
        return r.text, _campos_ocultos(r.text)

    async def postar(
        self, caminho: str, campos: dict[str, str], alvo: str, argumento: str = ""
    ) -> str:
        """Replica um postback: reenvia o formulário com o alvo do botão acionado."""
        corpo = dict(campos)
        corpo["__EVENTTARGET"] = alvo
        corpo["__EVENTARGUMENT"] = argumento
        async with self._cliente() as c:
            r = await c.post(caminho, data=corpo, headers={"Referer": f"{self.base_web}{caminho}"})
            self.cookies.update({k: v for k, v in r.cookies.items()})
        return r.text

    async def enviar_arquivo(
        self, html_tela: str, caminho: str, nome: str, conteudo: bytes, content_type: str
    ) -> tuple[str, str]:
        """Sobe um arquivo pelo RadAsyncUpload e devolve o ClientState e o campo.

        O upload é assíncrono e independente do postback: manda o arquivo para o
        handler, recebe o token que o representa no servidor, e devolve o
        `ClientState` que o postback seguinte precisa carregar para que a tela
        reconheça o arquivo — é o que o navegador põe no campo escondido do
        controle antes de acionar o botão.

        Devolve também o NOME desse campo (`<id do controle>_ClientState`), lido
        do HTML da tela — cada tela nomeia o seu, e presumir um nome fixo só
        funcionaria numa.
        """
        config = _RE_CONFIG.search(html_tela)
        config_tipo = _RE_CONFIG_TIPO.search(html_tela)
        controle = _RE_CONTROLE_UPLOAD.search(html_tela)
        if config is None or config_tipo is None or controle is None:
            raise TelaIndisponivel("Esta tela não tem um upload RadAsyncUpload reconhecível.")
        campo_client_state = f"{controle.group(1)}_ClientState"
        rau = f"{config.group(1)}&{config_tipo.group(1)}"
        metadata = {
            "TotalChunks": 1,
            "ChunkIndex": 0,
            "TotalFileSize": len(conteudo),
            "UploadID": f"0{nome}",
            "IsSingleChunkUpload": False,
        }
        async with self._cliente() as c:
            r = await c.post(
                _HANDLER_UPLOAD,
                data={
                    "rauPostData": rau,
                    "fileName": nome,
                    "contentType": content_type,
                    "lastModifiedDate": "2026-01-01T00:00:00.000Z",
                    "metadata": json.dumps(metadata),
                },
                files={"file": ("blob", conteudo, "application/octet-stream")},
                headers={"Referer": f"{self.base_web}{caminho}"},
            )
        if r.status_code != 200 or "metaData" not in r.text:
            raise TelaIndisponivel(f"O upload não foi aceito (HTTP {r.status_code}).")
        resposta = r.json()
        # O ClientState que a tela espera: a lista de arquivos enviados, com o
        # fileInfo e o token, exatamente como o controle monta no navegador.
        estado = {
            "isEnabled": "true",
            "uploadedFiles": [{"fileInfo": resposta["fileInfo"], "metaData": resposta["metaData"]}],
        }
        return json.dumps(estado), campo_client_state


def _base_web() -> str:
    """O módulo web de operação, sob a mesma base do web service."""
    conf = configuracao()
    return f"{conf.base_url}/{conf.modulo_web}"


async def entrar(email: str, senha: str) -> SessaoWeb:
    """Faz login no Ahreas web como operador e devolve a sessão autenticada.

    O login é um postback: pega o estado da tela de login, devolve tudo com o
    e-mail e a senha nos campos certos e o alvo do botão. Sucesso é o servidor
    trocar a página de login pela home e mandar o cookie de autenticação.
    """
    base = _base_web()
    async with httpx.AsyncClient(
        base_url=base, follow_redirects=True, timeout=configuracao().timeout_segundos
    ) as c:
        inicial = await c.get("/")
        campos = _campos_ocultos(inicial.text)
        campos.update(
            {
                "__EVENTTARGET": _ALVO_LOGIN,
                "__EVENTARGUMENT": "",
                "goLogin$idusuario": email,
                "goLogin$idpassword": senha,
            }
        )
        resposta = await c.post("/", data=campos, headers={"Referer": f"{base}/"})
        cookies = {k: v for k, v in c.cookies.items()}

    if not _logado(resposta.text):
        raise LoginWebRecusado("E-mail ou senha de operador incorretos.")
    return SessaoWeb(base_web=base, cookies=cookies)


# Reexporta para quem só precisa do parse.
def campos_ocultos(html: str) -> dict[str, str]:
    return _campos_ocultos(html)


ESTADO_WEBFORMS = _ESTADO
