"""O catálogo de métodos, lido do WSDL da própria instalação.

Não há lista de métodos escrita neste projeto, e isso é deliberado: cada Ahreas
publica os seus, conforme os módulos que a administradora contratou. O relatório
que a casa usa e o que ela nunca ligou saem do mesmo WSDL e chegam aqui
indistinguíveis. Uma lista embutida envelheceria no primeiro cliente e mentiria
sobre o que aquela instalação realmente aceita.

São dois serviços — `administracaoweb` e `Condominioweb` —, cada um com seu
`?WSDL`. O documento inteiro cabe em memória com folga; é lido de uma vez,
cacheado, e servido de lá.
"""

from __future__ import annotations

import asyncio
import time
import unicodedata
from dataclasses import dataclass
from typing import Final

import httpx
from defusedxml import ElementTree

from ahreas_mcp.configuracao import configuracao

_XSD: Final = "{http://www.w3.org/2001/XMLSchema}"
_WSDL: Final = "{http://schemas.xmlsoap.org/wsdl/}"
SERVICOS: Final = ("administracaoweb", "Condominioweb")

# Credenciais entram em todo método; não são parte do contrato que interessa a
# quem chama, então são escondidas do catálogo.
_CREDENCIAIS: Final = frozenset({"usuario", "senha", "chave"})


@dataclass(frozen=True, slots=True)
class Parametro:
    nome: str
    tipo: str


@dataclass(frozen=True, slots=True)
class Metodo:
    nome: str
    servico: str
    parametros: tuple[Parametro, ...]

    @property
    def busca_normalizada(self) -> str:
        return _normalizar(self.nome)


@dataclass(frozen=True, slots=True)
class _Catalogo:
    metodos: list[Metodo]
    por_nome: dict[str, Metodo]
    carregado_em: float


_catalogo: _Catalogo | None = None
_trava = asyncio.Lock()


def _normalizar(texto: str) -> str:
    """Compara como quem digita: sem acento, sem caixa."""
    decomposto = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in decomposto if not unicodedata.combining(c)).casefold()


def _parametros(elemento) -> tuple[Parametro, ...]:
    complexo = elemento.find(f"{_XSD}complexType")
    if complexo is None:
        return ()
    sequencia = complexo.find(f"{_XSD}sequence")
    if sequencia is None:
        return ()
    saida = []
    for p in sequencia.findall(f"{_XSD}element"):
        nome = p.get("name")
        if not nome or nome in _CREDENCIAIS:
            continue
        tipo = (p.get("type") or "complexo").split(":")[-1]
        saida.append(Parametro(nome=nome, tipo=tipo))
    return tuple(saida)


def _metodos_do_wsdl(xml: str, servico: str) -> list[Metodo]:
    raiz = ElementTree.fromstring(xml)
    # Nomes reais das operações: só o que é operação de fato, não todo element.
    operacoes = {o.get("name") for o in raiz.iter(f"{_WSDL}operation")}
    metodos: list[Metodo] = []
    for elemento in raiz.iter(f"{_XSD}element"):
        nome = elemento.get("name")
        # Elemento de request tem complexType inline e nome de operação; o de
        # response termina em Result/Response e é ignorado.
        if not nome or nome not in operacoes or elemento.get("type"):
            continue
        metodos.append(Metodo(nome=nome, servico=servico, parametros=_parametros(elemento)))
    return metodos


async def _baixar_wsdl(cliente: httpx.AsyncClient, servico: str) -> list[Metodo]:
    url = f"{configuracao().url_servico(servico)}?WSDL"
    resposta = await cliente.get(url)
    resposta.raise_for_status()
    return _metodos_do_wsdl(resposta.text, servico)


async def _carregar() -> _Catalogo:
    conf = configuracao()
    async with httpx.AsyncClient(timeout=conf.timeout_segundos) as cliente:
        listas = await asyncio.gather(*(_baixar_wsdl(cliente, s) for s in SERVICOS))
    metodos = [m for lista in listas for m in lista]
    # Dois serviços podem publicar um método de mesmo nome; a chave inclui o
    # serviço para não perder nenhum, mas a busca por nome simples devolve o
    # primeiro — que é o que quase todo mundo quer.
    por_nome: dict[str, Metodo] = {}
    for m in metodos:
        por_nome.setdefault(m.nome, m)
    return _Catalogo(metodos=metodos, por_nome=por_nome, carregado_em=time.monotonic())


async def _obter() -> _Catalogo:
    global _catalogo
    valido = (
        _catalogo is not None
        and time.monotonic() - _catalogo.carregado_em < configuracao().cache_wsdl_segundos
    )
    if valido:
        assert _catalogo is not None
        return _catalogo
    async with _trava:
        if _catalogo is None or time.monotonic() - _catalogo.carregado_em >= (
            configuracao().cache_wsdl_segundos
        ):
            _catalogo = await _carregar()
        return _catalogo


async def todos() -> list[Metodo]:
    return list((await _obter()).metodos)


async def buscar(texto: str | None = None) -> list[Metodo]:
    """Busca por intenção: cada palavra do termo tem de aparecer no nome.

    Quem procura pensa em "segunda via boleto", não em `SegundaViaBoletos`. Por
    isso o termo é quebrado em palavras e todas precisam casar (sem acento, sem
    caixa, ignorando espaços e underscores) — assim "segunda via" acha
    `SegundaViaBoletos` e "boleto aberto" acha o método certo sem ordem fixa.
    """
    metodos = await todos()
    if not texto:
        return metodos
    palavras = [p for p in _normalizar(texto).replace("_", " ").split() if p]
    if not palavras:
        return metodos
    return [m for m in metodos if all(p in m.busca_normalizada for p in palavras)]


async def metodo(nome: str) -> Metodo | None:
    return (await _obter()).por_nome.get(nome)
