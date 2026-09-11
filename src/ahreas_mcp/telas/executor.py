"""Acionar uma tela: preencher os campos e disparar um botão, por HTTP.

Executar uma ação numa tela é reenviar o formulário dela com os campos que a
pessoa informou e o alvo do botão que ela acionaria. O estado escondido (ViewState)
vem do GET imediatamente anterior — o servidor só aceita o postback se ele devolve
o estado que acabou de emitir, então abrir e acionar andam juntos.

Como no lado do web service, uma ação que grava passa por confirmação. Aqui o WSDL
não diz o efeito de nada, então ele é inferido do nome do botão — e, na dúvida,
tratado como escrita: nunca uma gravação roda sem confirmação por engano.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from html.parser import HTMLParser

from ahreas_mcp.telas import formulario as fo
from ahreas_mcp.telas.sessao_web import SessaoWeb


class EfeitoAcao(StrEnum):
    LEITURA = "leitura"
    ESCRITA = "escrita"


# Radicais de escrita no nome de um botão. Conservador: o que não for claramente
# consulta é tratado como escrita.
_ESCRITA = re.compile(
    r"(process|grav|salv|sav|inser|exclu|delet|remov|cancel|confirm|gerar|gera|"
    r"emit|envi|importa|baixa|aprov|efetiv|atualiz|alter|lanc)",
    re.I,
)
_LEITURA = re.compile(
    r"(consult|pesquis|busca|filtr|listar|lista|exib|visualiz|relat|download|"
    r"selec|carreg|abrir|detalh|ver)",
    re.I,
)

# Mensagens que o Ahreas mostra em RadWindow/label ao fim de uma ação.
_MSG = re.compile(
    r"(?:radalert|RadWindowManager|lblMensagem|lblErro|divMensagem)[^>]*>\s*([^<]{4,200})", re.I
)
_TITULO = re.compile(r"<title>([^<]+)</title>", re.I)


def efeito_da_acao(rotulo_ou_alvo: str) -> EfeitoAcao:
    if _LEITURA.search(rotulo_ou_alvo) and not _ESCRITA.search(rotulo_ou_alvo):
        return EfeitoAcao.LEITURA
    if _ESCRITA.search(rotulo_ou_alvo):
        return EfeitoAcao.ESCRITA
    # Sem pista: na dúvida, escrita (pede confirmação).
    return EfeitoAcao.ESCRITA


@dataclass(frozen=True, slots=True)
class Resultado:
    ok: bool
    mensagem: str | None
    titulo: str | None
    tamanho: int
    html: str
    colunas: tuple[str, ...] = field(default_factory=tuple)
    linhas: tuple[tuple[str, ...], ...] = field(default_factory=tuple)
    # Uma célula editável de grid por entrada: o campo (name) a preencher, o valor
    # atual e o texto da linha (bloco/unidade), para a IA saber o que é o quê.
    campos_editaveis: tuple[dict[str, str], ...] = field(default_factory=tuple)


def _extrair_mensagem(html: str) -> str | None:
    m = _MSG.search(html)
    if not m:
        return None
    texto = " ".join(m.group(1).split())
    # Descarta lixo de script que às vezes cai no mesmo trecho.
    if any(s in texto for s in ("function", "prototype", "var ", "=>", "{")):
        return None
    return texto or None


class _Grid(HTMLParser):
    """Extrai a primeira RadGrid da página: cabeçalho, texto e campos editáveis.

    O RadGrid marca cada linha com rgRow/rgAltRow e o cabeçalho com rgHeader.
    Além do texto de cada célula, captura os inputs dentro das linhas (name e
    valor) — é o que uma grid de edição (consumos, lançamentos em lote) usa, e o
    que a IA precisa para preencher sem saber nada sobre a tela específica.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.colunas: list[str] = []
        self.linhas: list[list[str]] = []
        self.campos: list[dict[str, str]] = []
        self._na_celula = False
        self._celula: list[str] = []
        self._linha: list[str] = []
        self._linha_campos: list[tuple[str, str]] = []
        self._tipo: str | None = None  # "header" ou "row"

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        classe = attrs.get("class") or ""
        if tag == "tr":
            if "rgRow" in classe or "rgAltRow" in classe:
                self._tipo, self._linha, self._linha_campos = "row", [], []
        elif tag in ("td", "th") and self._tipo:
            self._na_celula, self._celula = True, []
        elif tag == "th" and "rgHeader" in classe:
            self._tipo, self._na_celula, self._celula = "header", True, []
        elif tag == "input" and self._tipo == "row":
            nome = attrs.get("name", "")
            tipo = (attrs.get("type") or "text").lower()
            # Só campos de dados editáveis (não hidden do estado, não o pager).
            if nome and tipo in ("text", "") and "Filtro" not in nome and "Pager" not in nome:
                self._linha_campos.append((nome, attrs.get("value", "")))

    def handle_data(self, data: str) -> None:
        if self._na_celula:
            self._celula.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._na_celula:
            self._na_celula = False
            texto = " ".join("".join(self._celula).split())
            (self.colunas if self._tipo == "header" else self._linha).append(texto)
        elif tag == "tr" and self._tipo == "row":
            if any(c for c in self._linha):
                self.linhas.append(self._linha)
                rotulo = " ".join(c for c in self._linha[:3] if c)
                for nome, valor in self._linha_campos:
                    self.campos.append({"campo": nome, "valor": valor, "linha": rotulo})
            self._tipo = None


def extrair_tabela(
    html: str, limite: int = 500
) -> tuple[list[str], list[list[str]], list[dict[str, str]]]:
    g = _Grid()
    g.feed(html)
    return g.colunas, g.linhas[:limite], g.campos[:limite]


# O campo de tamanho de página do RadGrid (UcPagerTemplate). Achá-lo é como se
# expande a grid para trazer todas as linhas numa página só — sem isso, uma grid
# de 99 unidades viria de 10 em 10, e preencher/ler em massa exigiria navegar
# página a página.
_RE_PAGESIZE = re.compile(r'name="([^"]+UcPagerTemplate\d*\$tbPageSize)"')
_TODAS = "9999"


def _client_state_pagesize(valor: str) -> str:
    return (
        f'{{"enabled":true,"emptyMessage":"","validationText":"{valor}",'
        f'"valueAsString":"{valor}","valueWithPromptAndLiterals":"{valor}",'
        f'"lastSetTextBoxValue":"{valor}"}}'
    )


async def trazer_todas_as_linhas(sessao: SessaoWeb, caminho: str, html: str) -> str:
    """Expande a grid paginada para uma página só, trazendo todas as linhas.

    Genérico para qualquer RadGrid do Ahreas: acha o tamanho de página, sobe para
    um número alto e refaz o postback. Se a tela não tem grid paginada, devolve o
    mesmo HTML — nada a expandir.
    """
    m = _RE_PAGESIZE.search(html)
    if m is None:
        return html
    campo = m.group(1)
    from ahreas_mcp.telas.sessao_web import campos_ocultos

    corpo = dict(campos_ocultos(html))
    corpo[campo] = _TODAS
    corpo[f"{campo.replace('$', '_')}_ClientState"] = _client_state_pagesize(_TODAS)
    # O RadInput do pager tem AutoPostBack: o alvo do postback é o próprio campo.
    return await sessao.postar(caminho, corpo, campo)


async def executar(
    sessao: SessaoWeb,
    caminho: str,
    valores: dict[str, str],
    alvo: str,
    argumento: str = "",
    continuar: bool = False,
    trazer_tudo: bool = True,
) -> Resultado:
    """Aplica os valores nos campos e dispara o alvo do botão.

    Com `continuar`, reaproveita o estado da última ação nesta tela (mantém o
    ViewState), o que permite encadear passos — filtrar, depois alterar, depois
    gravar — sem reabrir a tela e perder o que o passo anterior carregou. Sem
    ele, abre a tela do zero.

    Com `trazer_tudo` (padrão), se a ação abrir uma grid paginada, ela é expandida
    para trazer todas as linhas de uma vez — para ler ou preencher em massa sem
    navegar página a página.
    """
    guardado = sessao.estado_atual(caminho) if continuar else None
    html_atual, estado = guardado if guardado is not None else await sessao.abrir(caminho)
    corpo = dict(estado)
    for nome, valor in valores.items():
        # checkbox no WebForms: presente = marcado; ausente = desmarcado.
        if valor in ("", "false", "off", "0") and nome in corpo and _parece_checkbox(nome):
            corpo.pop(nome, None)
        else:
            corpo[nome] = valor
            # RadNumericTextBox guarda o valor num campo espelho `_ClientState`
            # (JSON) e o servidor lê de lá, não do texto. Quem usa o MCP não sabe
            # fabricar esse JSON — então, ao setar um campo que tem espelho, o
            # espelho é reescrito aqui com o mesmo valor. Genérico: vale para
            # qualquer RadNumericTextBox de qualquer tela.
            espelho = f"{nome}_ClientState"
            if espelho in corpo:
                corpo[espelho] = _reescrever_clientstate(corpo[espelho], valor)
    html = await sessao.postar(
        caminho, corpo, alvo, argumento, imagem=_e_botao_imagem(html_atual, alvo)
    )
    if trazer_tudo:
        html = await trazer_todas_as_linhas(sessao, caminho, html)
    return analisar_resultado(html)


def _e_botao_imagem(html: str, alvo: str) -> bool:
    """Se o alvo é um `<input type=image>` — que posta por coordenadas, não evento."""
    tag = re.search(rf'<input[^>]*name="{re.escape(alvo)}"[^>]*>', html)
    return bool(tag and 'type="image"' in tag.group(0))


def analisar_resultado(html: str) -> Resultado:
    """Lê a resposta de um postback: sucesso/erro, título, mensagem e grid."""
    titulo = _TITULO.search(html)
    mensagem = _extrair_mensagem(html)
    colunas, linhas, campos = extrair_tabela(html)
    ok = not (mensagem and re.search(r"erro|inválid|falh|não foi", mensagem, re.I))
    return Resultado(
        ok=ok,
        mensagem=mensagem,
        titulo=" ".join(titulo.group(1).split()) if titulo else None,
        tamanho=len(html),
        campos_editaveis=tuple(campos),
        html=html,
        colunas=tuple(colunas),
        linhas=tuple(tuple(linha) for linha in linhas),
    )


def _parece_checkbox(nome: str) -> bool:
    return nome.lower().startswith(("ck", "chk", "cb")) or "check" in nome.lower()


# As chaves do ClientState do RadNumericTextBox que carregam o valor digitado.
# As demais (enabled, minValue, maxValue, formato...) são preservadas como estão.
_CHAVES_VALOR = (
    "validationText",
    "valueAsString",
    "valueWithPromptAndLiterals",
    "lastSetTextBoxValue",
)


def _reescrever_clientstate(atual: str, valor: str) -> str:
    """Reescreve o JSON de ClientState de um RadNumericTextBox com um novo valor.

    Mantém todas as outras chaves do estado (limites, formato, habilitado) e só
    troca as que espelham o texto. Se o estado atual não é um JSON legível, monta
    um mínimo suficiente para o servidor aceitar o valor.
    """
    try:
        estado = json.loads(atual) if atual else {}
        if not isinstance(estado, dict):
            estado = {}
    except (ValueError, TypeError):
        estado = {}
    for chave in _CHAVES_VALOR:
        # Não cria chaves que não existiam (formatos variam entre telas), mas
        # garante o par mínimo que todo RadNumericTextBox usa para ler o valor.
        if chave in estado or chave in ("validationText", "valueAsString"):
            estado[chave] = valor
    estado.setdefault("enabled", True)
    estado.setdefault("emptyMessage", "")
    return json.dumps(estado, separators=(",", ":"), ensure_ascii=False)


def acoes_da_tela(html: str) -> list[fo.Acao]:
    return fo.analisar(html).acoes
