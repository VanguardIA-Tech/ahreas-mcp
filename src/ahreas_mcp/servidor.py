"""As ferramentas que um cliente MCP enxerga.

Quatro, no modelo buscar → descrever → executar: com centenas de métodos, não
faz sentido uma tool por método. `listar_funcionalidades` acha o método pela
intenção, `descrever_metodo` mostra o contrato, `executar_metodo` chama. Nenhuma
recebe credencial — usuário, senha e chave vivem no processo, vindos do ambiente.

Toda execução com efeito no ERP passa por confirmação explícita, e escrita ainda
depende de o administrador ter ligado `AHREAS_PERMITIR_ESCRITA`.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastmcp import FastMCP

from ahreas_mcp.catalogo import wsdl
from ahreas_mcp.configuracao import configuracao
from ahreas_mcp.semantica.efeitos import Efeito, efeito, grava
from ahreas_mcp.sessao import usuario as sessao
from ahreas_mcp.soap import cliente

# Acima disto, uma resposta é grande demais para uma conversa e quase sempre é
# um método classe-ficha (a base inteira do condomínio). Trunca com aviso em vez
# de despejar 1 MB no contexto.
_LIMITE_CONTEUDO = 60_000


def _auth() -> Any:
    """O servidor de autorização OAuth, só quando o modo remoto está ligado.

    Montado tardiamente: instanciar o provider exige AHREAS_PUBLIC_URL, que no
    uso local (stdio) nem existe.
    """
    if not configuracao().modo_remoto:
        return None
    from ahreas_mcp.auth.provedor import AhreasAuthProvider

    return AhreasAuthProvider()


mcp: FastMCP[Any] = FastMCP(
    name="Ahreas",
    auth=_auth(),
    instructions=(
        "ERP Ahreas (condomínios). O catálogo de métodos vem do WSDL da própria "
        "instalação, então os nomes são os que este Ahreas publica. Comece por "
        "listar_funcionalidades para achar o método pela intenção (ex.: "
        "'inadimplência', 'boleto', 'lançamento'). Chame descrever_metodo antes "
        "de executar_metodo: é ela que diz os parâmetros, o formato de data e se "
        "o método lê ou grava. Datas vão como AAAA-MM-DDT00:00:00; vários "
        "relatórios aceitam mes e ano como números no lugar. 'Sem dados no "
        "período' e 'sem acesso a esta função' (módulo não contratado) são "
        "respostas legítimas, não erros a contornar. Execução que grava no ERP "
        "exige confirmar=true, e antes disso mostre à pessoa o que vai rodar."
    ),
)


class SessaoExpirada(Exception):
    """O token chegou, mas a sessão dele não existe mais — refazer login."""


def _credencial() -> cliente.Credencial | None:
    """A credencial de quem está chamando.

    No remoto, sai da sessão que o token de acesso OAuth abriu — cada pessoa
    fala pelo próprio usuário do Ahreas. No local (stdio) não há request, então
    devolve None e a chamada usa a identidade do ambiente.
    """
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except Exception:
        return None
    if token is None:
        return None
    atual = sessao.obter(token.token)
    if atual is None:
        raise SessaoExpirada("Sua sessão do Ahreas expirou. Faça login novamente.")
    return atual.credencial


def _dica_formato(tipo: str) -> str | None:
    if tipo == "dateTime":
        return "data no formato AAAA-MM-DDT00:00:00"
    if tipo == "base64Binary":
        return "conteúdo do arquivo em base64"
    if tipo == "boolean":
        return "true ou false"
    return None


def _descrever_parametros(metodo: wsdl.Metodo) -> list[dict[str, Any]]:
    saida = []
    for p in metodo.parametros:
        item: dict[str, Any] = {"nome": p.nome, "tipo": p.tipo}
        dica = _dica_formato(p.tipo)
        if dica:
            item["formato"] = dica
        saida.append(item)
    return saida


@mcp.tool
async def listar_funcionalidades(
    busca: Annotated[
        str | None, "Texto no nome do método (ex.: 'boleto', 'inadimplencia')."
    ] = None,
    apenas_leitura: Annotated[bool, "Só métodos de consulta, sem os que gravam."] = False,
    limite: Annotated[int, "Quantos devolver."] = 40,
) -> dict[str, Any]:
    """Acha métodos do Ahreas pela intenção, lidos do WSDL desta instalação."""
    encontrados = await wsdl.buscar(busca)
    if apenas_leitura:
        encontrados = [m for m in encontrados if efeito(m.nome) is Efeito.LEITURA]
    return {
        "total": len(encontrados),
        "mostrando": min(limite, len(encontrados)),
        "metodos": [
            {"nome": m.nome, "servico": m.servico, "efeito": efeito(m.nome).value}
            for m in encontrados[:limite]
        ],
    }


@mcp.tool
async def descrever_metodo(
    nome: Annotated[str, "Nome do método, como aparece em listar_funcionalidades."],
) -> dict[str, Any]:
    """Detalha um método: serviço, parâmetros, formato e se lê ou grava."""
    metodo = await wsdl.metodo(nome)
    if metodo is None:
        return {
            "ok": False,
            "erro": {
                "codigo": "ahreas.metodo.inexistente",
                "mensagem": (
                    f"Não há método {nome!r} no catálogo deste Ahreas. Use "
                    "listar_funcionalidades para ver os nomes desta instalação."
                ),
            },
        }
    ef = efeito(nome)
    resposta: dict[str, Any] = {
        "ok": True,
        "metodo": {
            "nome": metodo.nome,
            "servico": metodo.servico,
            "efeito": ef.value,
            "parametros": _descrever_parametros(metodo),
        },
    }
    if ef is Efeito.ESCRITA:
        resposta["metodo"]["observacao"] = (
            "Grava no ERP. Exige confirmar=true e AHREAS_PERMITIR_ESCRITA ligado. "
            "Mostre à pessoa o que vai rodar antes de confirmar."
        )
    elif ef is Efeito.INCERTO:
        resposta["metodo"]["observacao"] = (
            "Efeito não verificado: pode ter efeito colateral (emitir boleto, "
            "enviar e-mail/SMS, iniciar processo). Tratado como escrita — exige "
            "confirmar=true."
        )
    return resposta


def _resultado(resposta: cliente.Resposta) -> dict[str, Any]:
    conteudo = resposta.conteudo
    truncado = len(conteudo) > _LIMITE_CONTEUDO
    if truncado:
        conteudo = conteudo[:_LIMITE_CONTEUDO]
    saida: dict[str, Any] = {
        "ok": True,
        "metodo": resposta.metodo,
        "tamanho": resposta.tamanho,
        "conteudo": conteudo,
    }
    if truncado:
        saida["truncado"] = (
            f"Resposta de {resposta.tamanho} caracteres cortada em {_LIMITE_CONTEUDO}. "
            "Restrinja o período ou os filtros para uma resposta menor."
        )
    return saida


@mcp.tool
async def executar_metodo(
    nome: Annotated[str, "Nome do método a executar."],
    parametros: Annotated[dict[str, Any] | None, "Parâmetros do método (sem credenciais)."] = None,
    confirmar: Annotated[bool, "Obrigatório quando o método grava no ERP."] = False,
) -> dict[str, Any]:
    """Executa um método do Ahreas com a credencial da instalação.

    Leitura roda direto. Método que grava (ou de efeito incerto) exige
    confirmar=true, e escrita ainda depende de AHREAS_PERMITIR_ESCRITA.
    """
    metodo = await wsdl.metodo(nome)
    if metodo is None:
        return {
            "ok": False,
            "erro": {
                "codigo": "ahreas.metodo.inexistente",
                "mensagem": (
                    f"Não há método {nome!r} no catálogo deste Ahreas. Use "
                    "listar_funcionalidades para ver os nomes desta instalação."
                ),
            },
        }

    try:
        credencial = _credencial()
    except SessaoExpirada as erro:
        return {
            "ok": False,
            "erro": {"codigo": "ahreas.sessao_expirada", "mensagem": str(erro)},
        }

    ef = efeito(nome)
    if grava(nome):
        if not confirmar:
            return {
                "ok": False,
                "status": "precisa_confirmar",
                "efeito": ef.value,
                "resumo": (
                    f"O método {nome} "
                    + ("grava no ERP" if ef is Efeito.ESCRITA else "pode ter efeito no ERP")
                    + f", com {parametros or 'nenhum parâmetro'}. Mostre isso para a "
                    "pessoa e só chame de novo com confirmar=true depois do sim."
                ),
            }
        if not configuracao().permitir_escrita:
            return {
                "ok": False,
                "erro": {
                    "codigo": "ahreas.escrita.desligada",
                    "mensagem": (
                        "Operações que gravam no ERP estão desligadas neste servidor. "
                        "O administrador liga em AHREAS_PERMITIR_ESCRITA."
                    ),
                },
            }

    try:
        resposta = await cliente.chamar(
            metodo.servico, nome, parametros or {}, credencial=credencial
        )
    except cliente.SemIdentidade as erro:
        return {
            "ok": False,
            "erro": {"codigo": "ahreas.sem_identidade", "mensagem": str(erro)},
        }
    except cliente.CredencialRecusada as erro:
        return {
            "ok": False,
            "erro": {
                "codigo": "ahreas.credencial_recusada",
                "mensagem": f"O Ahreas recusou a credencial ({erro}). Faça login novamente.",
            },
        }
    except cliente.SemLicenca as erro:
        return {
            "ok": False,
            "erro": {
                "codigo": "ahreas.sem_licenca",
                "mensagem": (
                    f"Este Ahreas não dá acesso ao método {nome} para esta credencial "
                    f"— provavelmente um módulo não contratado. ({erro})"
                ),
            },
        }
    except cliente.RespostaVazia as erro:
        return {"ok": True, "vazio": True, "mensagem": str(erro)}
    except cliente.FaltaParametro as erro:
        return {
            "ok": False,
            "erro": {
                "codigo": "ahreas.parametro_invalido",
                "mensagem": str(erro),
                "dica": "Confira os parâmetros com descrever_metodo.",
            },
        }
    except cliente.AhreasIndisponivel as erro:
        return {
            "ok": False,
            "erro": {"codigo": "ahreas.indisponivel", "mensagem": str(erro)},
        }
    return _resultado(resposta)


@mcp.tool
async def diagnostico() -> dict[str, Any]:
    """O que está de pé: os dois web services, o catálogo e a credencial."""
    conf = configuracao()
    resultado: dict[str, Any] = {
        "base_url": conf.base_url,
        "modo": "remoto (OAuth)" if conf.modo_remoto else "local (stdio)",
        "escrita_liberada": conf.permitir_escrita,
    }
    try:
        metodos = await wsdl.todos()
        resultado["catalogo"] = {
            "total": len(metodos),
            "administracaoweb": sum(1 for m in metodos if m.servico == "administracaoweb"),
            "Condominioweb": sum(1 for m in metodos if m.servico == "Condominioweb"),
        }
    except Exception as erro:  # noqa: BLE001 - diagnóstico nunca derruba
        resultado["catalogo"] = {"erro": f"{type(erro).__name__}: {erro}"}
        resultado["proximo_passo"] = (
            "Não consegui ler o WSDL. Confira AHREAS_BASE_URL e se este servidor alcança o Ahreas."
        )
        return resultado

    try:
        credencial = _credencial()
    except SessaoExpirada:
        resultado["credencial"] = "sessão expirada — faça login novamente"
        return resultado
    try:
        resposta = await cliente.chamar(
            "administracaoweb", "ValidaCredencial", {}, credencial=credencial
        )
        resultado["credencial"] = (
            "ok" if "sucesso" in resposta.conteudo.lower() else resposta.conteudo[:120]
        )
    except cliente.SemIdentidade:
        resultado["credencial"] = (
            "sem identidade nesta chamada (no stdio, configure AHREAS_USUARIO/AHREAS_SENHA)"
        )
    except cliente.AhreasIndisponivel as erro:
        resultado["credencial"] = f"falhou: {erro}"
    except Exception as erro:  # noqa: BLE001
        resultado["credencial"] = f"recusada: {erro}"
    return resultado
