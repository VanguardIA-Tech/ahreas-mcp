"""O catálogo de telas do Ahreas web, lido do próprio menu da instalação.

Como no lado do web service, nada aqui é escrito à mão: as telas e os seus nomes
saem do menu que o Ahreas monta para a pessoa depois do login. São centenas, e a
tela que a administradora usa todo dia aparece igual à que ela nunca abriu. Uma
lista embutida seria impossível de manter e mentiria sobre o que aquela instalação
realmente tem.

O menu é um Telerik RadMenu — cada item é `<span class="rmLink" target="...aspx">
Nome</span>` — então nome e caminho saem daí, com o nome que a própria empresa vê.
"""

from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass

from ahreas_mcp.configuracao import configuracao
from ahreas_mcp.telas.sessao_web import SessaoWeb

# Item do RadMenu: o nome legível e o caminho da tela.
_ITEM = re.compile(r'<span[^>]*class="rmLink"[^>]*target="([^"]+\.aspx)"[^>]*>([^<]+)</span>', re.I)


@dataclass(frozen=True, slots=True)
class Tela:
    nome: str
    caminho: str  # relativo ao módulo, ex. /receber/rateio/Importa_LeituraConsumo.aspx

    @property
    def busca_normalizada(self) -> str:
        return _normalizar(f"{self.nome} {self.caminho}")


@dataclass
class _Cache:
    telas: list[Tela]
    por_caminho: dict[str, Tela]
    carregado_em: float


_cache: _Cache | None = None


def _normalizar(texto: str) -> str:
    d = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in d if not unicodedata.combining(c)).casefold()


def _extrair(html: str, prefixo_modulo: str) -> list[Tela]:
    vistos: dict[str, Tela] = {}
    for caminho_abs, nome in _ITEM.findall(html):
        # O target vem absoluto (/condominioweb/...); guardamos relativo ao módulo.
        caminho = caminho_abs
        marca = f"/{prefixo_modulo}/"
        if marca in caminho:
            caminho = caminho[caminho.index(marca) + len(marca) - 1 :]
        nome_limpo = " ".join(nome.split())
        if caminho not in vistos:
            vistos[caminho] = Tela(nome=nome_limpo, caminho=caminho)
    return list(vistos.values())


async def carregar(sessao: SessaoWeb, *, validade_segundos: int = 3600) -> list[Tela]:
    """As telas do menu, lidas uma vez por sessão e servidas do cache."""
    global _cache
    agora = time.monotonic()
    if _cache is not None and agora - _cache.carregado_em < validade_segundos:
        return _cache.telas
    html, _ = await sessao.abrir("/default.aspx")
    telas = _extrair(html, configuracao().modulo_web)
    _cache = _Cache(telas=telas, por_caminho={t.caminho: t for t in telas}, carregado_em=agora)
    return telas


async def buscar(sessao: SessaoWeb, texto: str | None = None) -> list[Tela]:
    """Telas cujo nome ou caminho casam com todas as palavras do termo."""
    telas = await carregar(sessao)
    if not texto:
        return telas
    palavras = [p for p in _normalizar(texto).split() if p]
    return [t for t in telas if all(p in t.busca_normalizada for p in palavras)]


async def tela(sessao: SessaoWeb, caminho: str) -> Tela | None:
    await carregar(sessao)
    assert _cache is not None
    # aceita caminho com ou sem a barra inicial
    return _cache.por_caminho.get(caminho) or _cache.por_caminho.get(f"/{caminho.lstrip('/')}")


def _limpar_cache() -> None:
    global _cache
    _cache = None
