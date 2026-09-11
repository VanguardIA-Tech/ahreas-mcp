"""Falar com o Ahreas por SOAP.

Monta o envelope SOAP 1.1, faz o POST e devolve o XML interno que chega escapado
dentro de `<NomeResult>`. Tudo que parece estranho aqui é comportamento do
próprio Ahreas, comentado onde acontece.

A distinção que importa: um *fault de negócio* é uma resposta. "Este condomínio
não tem conta bancária" chega como HTTP 500 com um SOAP Fault carregando um
stack trace .NET, e significa "não há o que mostrar". Um *fault de sistema* é o
Ahreas fora do ar ou incompreensível. Confundir os dois faz um condomínio de mês
parado parecer uma queda do ERP.
"""

from __future__ import annotations

import asyncio
import gzip
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from xml.sax.saxutils import escape

import httpx
from defusedxml import ElementTree

from ahreas_mcp.configuracao import configuracao

NAMESPACE: Final = "http://gosati.com.br/webservices/"
ESPERA_ENTRE_TENTATIVAS: Final = 0.6

# Frases que o Ahreas usa quando quer dizer "não há nada aqui", embrulhadas numa
# exceção .NET. Casadas como substring no texto em minúsculas porque chegam com
# pontuação variável. Cada uma foi observada de verdade, não adivinhada.
FALTA_DE_DADOS: Final = (
    "não foi encontrado nenhum condomínio",
    "nenhum condomínio a processar",
    "cotas em aberto",
    "não existe",
    "não cadastrad",
    "nenhum registro",
    "sem movimento",
    "não foi encerrada",
    "não foi encontrado o controle de fechamento",
    "unidade não cadastrada",
)

# Frases de recusa por licença/perfil. O método existe, mas esta credencial não
# tem acesso a ele. Num Ahreas genérico isso é esperado — nem toda administradora
# contrata todo módulo — e vira uma resposta clara, não um erro de sistema.
SEM_LICENCA: Final = (
    "não possui acesso para utilizar esta função",
    "não tem acesso a esta função",
)


class Efeito(StrEnum):
    LEITURA = "leitura"
    ESCRITA = "escrita"


class RespostaVazia(Exception):
    """O Ahreas respondeu, e a resposta é que não há nada a mostrar."""


class SemLicenca(Exception):
    """O método existe, mas a credencial não tem acesso a ele."""


class FaltaParametro(Exception):
    """O Ahreas recusou a chamada por parâmetro faltando ou inválido.

    Carrega a mensagem de validação do próprio ERP, que costuma dizer exatamente
    o que faltou — é a melhor pista que existe sobre o contrato do método.
    """


class AhreasIndisponivel(Exception):
    """O Ahreas não respondeu, ou respondeu algo que não dá para interpretar."""


@dataclass(frozen=True, slots=True)
class Resposta:
    """O que uma chamada SOAP devolve quando dá certo."""

    metodo: str
    servico: str
    conteudo: str  # o XML/JSON/texto interno, já desescapado
    tamanho: int


def _decodificar(corpo: bytes) -> str:
    """O IIS do Ahreas faz gzip de algumas respostas — faults sobretudo — sem
    sempre dizer no header, então o teste honesto são os bytes mágicos."""
    if corpo[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(corpo).decode("utf-8", errors="replace")
        except OSError:
            pass  # Não era gzip de verdade; o texto cru é melhor que falhar.
    return corpo.decode("utf-8", errors="replace")


def _envelope(metodo: str, parametros: dict[str, object]) -> str:
    corpo = "\n".join(
        f'      <{chave} xsi:nil="true"/>'
        if valor is None
        else f"      <{chave}>{escape(str(valor))}</{chave}>"
        for chave, valor in parametros.items()
    )
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<soap:Envelope xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">\n'
        "  <soap:Body>\n"
        f'    <{metodo} xmlns="{NAMESPACE}">\n'
        f"{corpo}\n"
        f"    </{metodo}>\n"
        "  </soap:Body>\n"
        "</soap:Envelope>"
    )


def _sem_prefixo(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _contem(texto: str, marcas: tuple[str, ...]) -> bool:
    minuscula = texto.lower()
    return any(marca in minuscula for marca in marcas)


def mensagem_de_negocio(fault: str) -> str:
    """Extrai a frase legível de um stack trace .NET.

    Um fault chega como `System.Web.Services.Protocols.SoapException: ... --->
    System.Exception: Usuário não tem acesso ao condomínio\\n em Global...`. A
    mensagem da exceção mais interna é a única parte que descreve o que houve; o
    resto é a árvore de código do Ahreas.
    """
    if "Exception:" in fault:
        ultima = fault.rsplit("Exception:", 1)[1]
        # Corta o "em Namespace.Metodo(...)" que segue a mensagem.
        primeira = re.split(r"\s+em\s+\w", ultima, maxsplit=1)[0]
        primeira = primeira.splitlines()[0].strip() if primeira.strip() else ""
        if primeira:
            return primeira
    return fault.splitlines()[0].strip() or fault


# Marcas de "faltou parâmetro / valor inválido". O Ahreas devolve estas quando a
# chamada chegou mas os dados não servem — pista direta sobre o contrato.
_VALIDACAO: Final = (
    "de entrada não estava",
    "não é um valor",
    "necessário informar",
    "não informad",
    "inválid",
    "espera o parâmetro",
    "conversão de um tipo",
    "informe ",
    "não foi informad",
    "referência de objeto não definida",
)


def _classificar_fault(texto: str, metodo: str) -> Exception:
    msg = mensagem_de_negocio(texto)
    if _contem(texto, SEM_LICENCA):
        return SemLicenca(msg)
    if _contem(texto, FALTA_DE_DADOS):
        return RespostaVazia(msg)
    if _contem(msg, _VALIDACAO):
        return FaltaParametro(msg)
    return AhreasIndisponivel(f"{metodo}: {msg}")


def _extrair_resultado(envelope_xml: str, metodo: str) -> str:
    raiz = ElementTree.fromstring(envelope_xml)
    corpo = next((f for f in raiz.iter() if _sem_prefixo(f.tag) == "Body"), None)
    if corpo is None:
        raise AhreasIndisponivel(f"{metodo}: resposta sem Body")

    fault = next((f for f in corpo.iter() if _sem_prefixo(f.tag) == "Fault"), None)
    if fault is not None:
        texto = next(
            (f.text or "" for f in fault.iter() if _sem_prefixo(f.tag) == "faultstring"),
            "Erro SOAP desconhecido",
        )
        raise _classificar_fault(texto, metodo)

    resultado = next(
        (f for f in corpo.iter() if _sem_prefixo(f.tag) == f"{metodo}Result"),
        None,
    )
    if resultado is None:
        # Alguns métodos nomeiam o Result sem repetir o nome do método; pega o
        # primeiro elemento que termina em "Result" dentro do Body.
        resultado = next(
            (f for f in corpo.iter() if _sem_prefixo(f.tag).endswith("Result")),
            None,
        )
    if resultado is None:
        raise RespostaVazia(f"{metodo} não devolveu dados")

    interno = (resultado.text or "").strip()
    if not interno:
        # Result sem texto: pode ser um DataSet .NET embutido como filhos reais.
        filhos = list(resultado)
        if filhos:
            from xml.etree.ElementTree import tostring

            interno = "".join(tostring(f, encoding="unicode") for f in filhos).strip()
    if not interno:
        raise RespostaVazia(f"{metodo} não devolveu dados")

    despido = interno.strip()
    if despido in ("<NewDataSet />", "<NewDataSet/>", "<NewDataSet></NewDataSet>"):
        raise RespostaVazia(f"{metodo} devolveu um conjunto vazio")
    return interno


def _credenciais() -> dict[str, object]:
    conf = configuracao()
    return {
        "usuario": conf.usuario,
        "senha": conf.senha.get_secret_value(),
        "chave": conf.chave.get_secret_value(),
    }


async def chamar(
    servico: str,
    metodo: str,
    parametros: dict[str, object] | None = None,
    *,
    timeout: float | None = None,
) -> Resposta:
    """Chama um método SOAP e devolve o conteúdo interno.

    `parametros` são os do método, sem as credenciais — elas são anexadas aqui,
    então nunca precisam ser passadas de fora nem aparecem no histórico.
    """
    conf = configuracao()
    segundos = timeout if timeout is not None else conf.timeout_segundos
    url = conf.url_servico(servico)
    # Ordem: parâmetros do método primeiro, credenciais depois — é a ordem que o
    # Ahreas espera na maioria dos métodos.
    p: dict[str, object] = dict(parametros or {})
    p.update(_credenciais())
    envelope = _envelope(metodo, p)
    cabecalhos = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": f"{NAMESPACE}{metodo}",
        "Accept-Encoding": "gzip",
    }

    resposta: httpx.Response | None = None
    ultimo_erro: Exception | None = None
    async with httpx.AsyncClient(timeout=segundos) as cliente:
        for tentativa in range(1, conf.tentativas + 1):
            try:
                resposta = await cliente.post(url, content=envelope, headers=cabecalhos)
                break
            except httpx.HTTPError as erro:
                ultimo_erro = erro
                if tentativa < conf.tentativas:
                    await asyncio.sleep(ESPERA_ENTRE_TENTATIVAS * tentativa)

    if resposta is None:
        raise AhreasIndisponivel(f"{metodo}: sem resposta ({ultimo_erro})")

    texto = _decodificar(resposta.content)
    # Fault de negócio vem como HTTP 500 com Fault no corpo. Qualquer outro
    # não-2xx sem Fault é o servidor web falhando.
    if resposta.is_error and "Fault" not in texto:
        raise AhreasIndisponivel(f"{metodo}: HTTP {resposta.status_code}")

    # O XML interno chega escapado dentro do Result, mas `_extrair_resultado` já
    # o devolve desescapado uma vez: `resultado.text` do ElementTree faz a
    # decodificação de entidade. Desescapar de novo aqui corromperia um "&amp;"
    # legítimo (um condomínio "A & B") num "&" solto dentro da marcação.
    conteudo = _extrair_resultado(texto, metodo)
    return Resposta(metodo=metodo, servico=servico, conteudo=conteudo, tamanho=len(conteudo))
