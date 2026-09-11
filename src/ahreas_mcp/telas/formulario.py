"""O que uma tela pede e o que dá para fazer nela.

Uma tela WebForms é um formulário grande: dezenas de campos de estado que não
interessam a ninguém, um punhado de campos que a pessoa preenche, e alguns botões
que disparam postbacks. Este módulo separa o joio do trigo — lê o HTML de uma tela
e devolve só os campos editáveis (com tipo e opções) e as ações possíveis (com o
alvo de postback que cada botão dispara), para que quem chama saiba o que informar
e o que pode acionar sem ler HTML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Campos internos do WebForms e do Telerik que nunca são preenchidos pela pessoa.
_IGNORAR_PREFIXO = ("__", "goHeader1", "goFooter1", "RadScriptManager", "RadStyleManager")
# Alvo de postback embutido no onclick/href: __doPostBack('alvo','arg').
_POSTBACK = re.compile(r"__doPostBack\('([^']+)'(?:,'([^']*)')?\)")


@dataclass(frozen=True, slots=True)
class Campo:
    nome: str  # o name do input, que vai no postback
    tipo: str  # text, checkbox, select, textarea, radio, password, hidden...
    rotulo: str | None = None
    opcoes: tuple[str, ...] = field(default_factory=tuple)  # para select


@dataclass(frozen=True, slots=True)
class Acao:
    alvo: str  # o __EVENTTARGET que o postback dispara
    rotulo: str
    argumento: str = ""


def _relevante(nome: str) -> bool:
    return bool(nome) and not nome.startswith(_IGNORAR_PREFIXO)


class _Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.campos: list[Campo] = []
        self.acoes: list[Acao] = []
        self._vistos_campo: set[str] = set()
        self._vistos_acao: set[str] = set()
        self._select: dict[str, str] | None = None
        self._opcoes: list[str] = []

    def _acao_do_atributo(self, attrs: dict[str, str], rotulo: str) -> None:
        for chave in ("onclick", "href"):
            m = _POSTBACK.search(attrs.get(chave, ""))
            if m and m.group(1) not in self._vistos_acao:
                self._vistos_acao.add(m.group(1))
                self.acoes.append(
                    Acao(alvo=m.group(1), rotulo=rotulo or m.group(1), argumento=m.group(2) or "")
                )

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {k: (v or "") for k, v in attrs_list}
        nome = attrs.get("name", "")
        tipo = (attrs.get("type") or "").lower()

        if tag == "input":
            if tipo in ("submit", "image", "button") and nome and nome not in self._vistos_acao:
                self._vistos_acao.add(nome)
                self.acoes.append(
                    Acao(alvo=nome, rotulo=attrs.get("value") or attrs.get("alt") or nome)
                )
            elif _relevante(nome) and tipo not in ("hidden",) and nome not in self._vistos_campo:
                self._vistos_campo.add(nome)
                self.campos.append(
                    Campo(nome=nome, tipo=tipo or "text", rotulo=attrs.get("title") or None)
                )
            self._acao_do_atributo(attrs, attrs.get("value") or nome)
        elif tag == "select" and _relevante(nome):
            self._select = {"nome": nome, "rotulo": attrs.get("title") or ""}
            self._opcoes = []
        elif tag == "option" and self._select is not None:
            self._opcoes.append(attrs.get("value", ""))
        elif tag == "textarea" and _relevante(nome) and nome not in self._vistos_campo:
            self._vistos_campo.add(nome)
            self.campos.append(Campo(nome=nome, tipo="textarea", rotulo=attrs.get("title") or None))
        elif tag in ("a", "span", "button"):
            self._acao_do_atributo(attrs, "")  # rótulo resolvido no texto, se houver

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self._select is not None:
            nome = self._select["nome"]
            if nome not in self._vistos_campo:
                self._vistos_campo.add(nome)
                self.campos.append(
                    Campo(
                        nome=nome,
                        tipo="select",
                        rotulo=self._select["rotulo"] or None,
                        opcoes=tuple(o for o in self._opcoes if o),
                    )
                )
            self._select = None
            self._opcoes = []


@dataclass(frozen=True, slots=True)
class Formulario:
    campos: list[Campo]
    acoes: list[Acao]


def analisar(html: str) -> Formulario:
    p = _Parser()
    p.feed(html)
    return Formulario(campos=p.campos, acoes=p.acoes)
