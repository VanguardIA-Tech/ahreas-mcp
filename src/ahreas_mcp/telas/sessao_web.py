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

# O ASP.NET decide se libera o postback assíncrono (partial rendering) olhando o
# User-Agent: um agente desconhecido é tratado como navegador incapaz, e o
# RadScriptManager recusa o AJAX com "SupportsPartialRendering=false". Um UA de
# navegador real destrava as telas que carregam a grid por RadAjax (consumos).
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

_INPUT = re.compile(r"<input\b[^>]*>", re.I)
_NAME = re.compile(r'name="([^"]+)"')
_VALUE = re.compile(r'value="([^"]*)"')

# As telas com grid usam ASP.NET UpdatePanel + RadAjax: o postback é parcial, o
# ScriptManager diz qual painel e qual controle disparou, e a resposta é um
# "delta" em vez da página inteira. Reconhecer isso é o que faz filtrar/paginar
# funcionar por HTTP — sem, a grid nunca chega.
# O ScriptManager não aparece como input próprio — só o campo `_TSM` dele. O
# name que o postback AJAX usa é o id dele (sem o `_TSM`), com `_` virando `$`.
_RE_SCRIPTMANAGER = re.compile(r'name="([^"]*RadScriptManager\d+)_TSM"')
_RE_UPDATEPANEL = re.compile(r'id="([^"]*pnlGeralPanel)"')


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


def _delta_para_html(delta: str) -> str:
    """Reconstrói uma página a partir de um delta de UpdatePanel do ASP.NET AJAX.

    O delta é uma sequência `tamanho|tipo|id|conteúdo` repetida. Interessam dois
    tipos: `updatePanel` (o HTML novo da parte da tela que mudou — as grids) e
    `hiddenField` (o novo __VIEWSTATE e afins, que o próximo postback precisa).
    Junta os painéis e devolve os campos escondidos como inputs, para o resto do
    pipeline tratar a resposta como se fosse uma página inteira.
    """
    partes: list[str] = []
    ocultos: list[str] = []
    pos = 0
    n = len(delta)
    while pos < n:
        try:
            i1 = delta.index("|", pos)
            tamanho = int(delta[pos:i1])
            i2 = delta.index("|", i1 + 1)
            tipo = delta[i1 + 1 : i2]
            i3 = delta.index("|", i2 + 1)
            ident = delta[i2 + 1 : i3]
            conteudo = delta[i3 + 1 : i3 + 1 + tamanho]
            pos = i3 + 1 + tamanho + 1
        except (ValueError, IndexError):
            break
        if tipo == "updatePanel":
            partes.append(conteudo)
        elif tipo == "hiddenField":
            ocultos.append(f'<input type="hidden" name="{ident}" value="{conteudo}"/>')
    return "".join(ocultos) + "".join(partes)


@dataclass
class SessaoWeb:
    """Uma sessão autenticada no Ahreas web, viva enquanto o cookie valer.

    Guarda o cookie de autenticação (nunca a senha) e o base do módulo. Cada
    chamada abre um cliente curto com esse cookie — o estado que importa é o
    cookie, e mantê-lo é o suficiente para o servidor reconhecer a pessoa.
    """

    base_web: str
    cookies: dict[str, str] = field(default_factory=dict)
    # O último HTML de cada tela aberta/acionada, por caminho. É o que permite
    # encadear postbacks (filtrar → alterar → gravar) sem reabrir a tela e perder
    # o resultado do passo anterior — cada tela WebForms depende do estado que
    # ela mesma acabou de emitir.
    _ultimo_html: dict[str, str] = field(default_factory=dict)

    def _cliente(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_web,
            cookies=self.cookies,
            follow_redirects=True,
            timeout=configuracao().timeout_segundos,
            headers={"User-Agent": _UA},
        )

    async def abrir(self, caminho: str) -> tuple[str, dict[str, str]]:
        """GET numa tela: devolve o HTML e o estado oculto para o próximo postback."""
        async with self._cliente() as c:
            r = await c.get(caminho)
            self.cookies.update({k: v for k, v in r.cookies.items()})
        if not _logado(r.text):
            raise LoginWebRecusado("A sessão web expirou. Faça login novamente.")
        self._ultimo_html[caminho] = r.text
        return r.text, _campos_ocultos(r.text)

    def estado_atual(self, caminho: str) -> tuple[str, dict[str, str]] | None:
        """O último HTML e o estado oculto desta tela, se já foi tocada nesta sessão."""
        html = self._ultimo_html.get(caminho)
        return (html, _campos_ocultos(html)) if html is not None else None

    async def postar(
        self,
        caminho: str,
        campos: dict[str, str],
        alvo: str,
        argumento: str = "",
        imagem: bool = False,
    ) -> str:
        """Replica um postback: reenvia o formulário com o alvo do botão acionado.

        Detecta sozinho as telas com grid (ASP.NET UpdatePanel + RadAjax): nelas
        o postback é parcial — o ScriptManager identifica o painel e o controle
        que disparou, um cabeçalho marca o modo delta, e a resposta é um delta
        que é reconstruído em página. Nas demais telas, é um postback comum.

        Um botão de imagem (`<input type=image>`) posta pelas coordenadas do
        clique (`alvo.x`/`alvo.y`), não pelo `__EVENTTARGET`.
        """
        anterior = self._ultimo_html.get(caminho, "")
        sm = _RE_SCRIPTMANAGER.search(anterior)
        painel = _RE_UPDATEPANEL.search(anterior)
        ajax = sm is not None and painel is not None

        corpo = dict(campos)
        cabecalhos = {"Referer": f"{self.base_web}{caminho}"}
        if ajax:
            # O ScriptManager carrega "painel|controle": é assim que o servidor
            # sabe o que atualizar e quem disparou. O name do campo é o id dele
            # com `_`→`$` (ASP.NET troca `$` do name por `_` no id).
            painel_nome = painel.group(1).replace("_", "$")  # type: ignore[union-attr]
            sm_campo = sm.group(1).replace("_", "$")  # type: ignore[union-attr]
            corpo[sm_campo] = f"{painel_nome}|{alvo}"
            cabecalhos["X-MicrosoftAjax"] = "Delta=true"
            cabecalhos["X-Requested-With"] = "XMLHttpRequest"
        # Um <input type=image> registra o clique pelas coordenadas `.x`/`.y` — o
        # ASP.NET identifica o ImageButton por elas, não pelo __EVENTTARGET. Sem
        # elas o servidor só reecoa o painel, sem disparar o botão. Isso vale
        # inclusive no postback assíncrono, onde o campo do ScriptManager (que
        # diz qual painel atualizar) convive com o `.x`/`.y` (que diz quem
        # disparou). Um controle comum em AJAX (o pager) manda o alvo no
        # __EVENTTARGET normalmente.
        if imagem:
            corpo[f"{alvo}.x"] = "1"
            corpo[f"{alvo}.y"] = "1"
            corpo.setdefault("__EVENTTARGET", "")
            corpo.setdefault("__EVENTARGUMENT", "")
        else:
            corpo["__EVENTTARGET"] = alvo
            corpo["__EVENTARGUMENT"] = argumento

        async with self._cliente() as c:
            r = await c.post(caminho, data=corpo, headers=cabecalhos)
            self.cookies.update({k: v for k, v in r.cookies.items()})

        texto = r.text
        # Uma resposta delta começa com "tamanho|tipo|...". Reconstrói juntando o
        # estado anterior (campos fora do painel) com o painel novo e o ViewState
        # atualizado, para o próximo passo ver a tela inteira.
        if ajax and re.match(r"^\d+\|", texto):
            reconstruido = _delta_para_html(texto)
            campos_novos = _campos_ocultos(anterior)
            campos_novos.update(_campos_ocultos(reconstruido))
            ocultos = "".join(
                f'<input type="hidden" name="{n}" value="{htmlmod.escape(v, quote=True)}"/>'
                for n, v in campos_novos.items()
            )
            texto = reconstruido + ocultos
        self._ultimo_html[caminho] = texto
        return texto

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
        base_url=base,
        follow_redirects=True,
        timeout=configuracao().timeout_segundos,
        headers={"User-Agent": _UA},
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
