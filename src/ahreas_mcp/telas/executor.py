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
    """Extrai a primeira RadGrid da página: cabeçalho e linhas de dados.

    O RadGrid marca cada linha com rgRow/rgAltRow e o cabeçalho com rgHeader,
    então dá para tirar a tabela sem saber nada sobre a tela — é o resultado que
    interessa a quem consulta, sem o HTML em volta.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.colunas: list[str] = []
        self.linhas: list[list[str]] = []
        self._na_celula = False
        self._celula: list[str] = []
        self._linha: list[str] = []
        self._tipo: str | None = None  # "header" ou "row"

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        classe = dict(attrs_list).get("class") or ""
        if tag == "tr":
            if "rgRow" in classe or "rgAltRow" in classe:
                self._tipo, self._linha = "row", []
        elif tag in ("td", "th") and self._tipo:
            self._na_celula, self._celula = True, []
        elif tag == "th" and "rgHeader" in classe:
            self._tipo, self._na_celula, self._celula = "header", True, []

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
            self._tipo = None


def extrair_tabela(html: str, limite_linhas: int = 100) -> tuple[list[str], list[list[str]]]:
    g = _Grid()
    g.feed(html)
    return g.colunas, g.linhas[:limite_linhas]


async def executar(
    sessao: SessaoWeb,
    caminho: str,
    valores: dict[str, str],
    alvo: str,
    argumento: str = "",
) -> Resultado:
    """Abre a tela, aplica os valores nos campos e dispara o alvo do botão."""
    _, estado = await sessao.abrir(caminho)
    corpo = dict(estado)
    for nome, valor in valores.items():
        # checkbox no WebForms: presente = marcado; ausente = desmarcado.
        if valor in ("", "false", "off", "0") and nome in corpo and _parece_checkbox(nome):
            corpo.pop(nome, None)
        else:
            corpo[nome] = valor
    html = await sessao.postar(caminho, corpo, alvo, argumento)
    return analisar_resultado(html)


def analisar_resultado(html: str) -> Resultado:
    """Lê a resposta de um postback: sucesso/erro, título, mensagem e tabela."""
    titulo = _TITULO.search(html)
    mensagem = _extrair_mensagem(html)
    colunas, linhas = extrair_tabela(html)
    ok = not (mensagem and re.search(r"erro|inválid|falh|não foi", mensagem, re.I))
    return Resultado(
        ok=ok,
        mensagem=mensagem,
        titulo=" ".join(titulo.group(1).split()) if titulo else None,
        tamanho=len(html),
        html=html,
        colunas=tuple(colunas),
        linhas=tuple(tuple(linha) for linha in linhas),
    )


def _parece_checkbox(nome: str) -> bool:
    return nome.lower().startswith(("ck", "chk", "cb")) or "check" in nome.lower()


def acoes_da_tela(html: str) -> list[fo.Acao]:
    return fo.analisar(html).acoes
