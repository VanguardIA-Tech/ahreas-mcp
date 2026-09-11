"""As ferramentas que um cliente MCP enxerga.

Quatro, no modelo buscar → descrever → executar: com centenas de métodos, não
faz sentido uma tool por método. `listar_metodos` acha o método pela
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
        "ERP Ahreas, para administração de condomínios. Este servidor faz o que "
        "uma pessoa faz no Ahreas — consultar relatórios financeiros e operar as "
        "telas do painel (importar consumo de gás/água, emitir boletos, lançar, "
        "etc.). Tudo é lido da própria instalação; nada é fixo no código.\n"
        "\n"
        "COMO AGIR, SEMPRE: primeiro DESCUBRA, depois DESCREVA, depois EXECUTE. "
        "Nunca invente um nome de método ou caminho de tela — descubra pela "
        "intenção da pessoa.\n"
        "\n"
        "HÁ DUAS SUPERFÍCIES, escolha pela tarefa:\n"
        "1) MÉTODOS (web service, dados prontos e rápidos): relatórios e consultas "
        "financeiras — inadimplência, contas a pagar, fluxo de caixa, boletos, "
        "vencimentos. Fluxo: listar_metodos(busca) → descrever_metodo(nome) "
        "→ executar_metodo(nome, parametros).\n"
        "2) TELAS (o painel web, faz tudo que o operador faz): quando não há método, "
        "ou quando a pessoa quer OPERAR (importar, emitir, cadastrar, processar). "
        "Fluxo: listar_telas(busca) → descrever_tela(caminho) → executar_acao_tela "
        "(caminho, acao, campos). Para subir arquivo (ex.: leitura de gás/água), "
        "use importar_arquivo_em_tela.\n"
        "\n"
        "EXEMPLO — 'quero individualizar o consumo de gás e água': isto é operar "
        "uma tela. Chame listar_telas('consumo'), ache 'Importação de Controle de "
        "Consumo', e use importar_arquivo_em_tela com o arquivo de leituras.\n"
        "\n"
        "REGRAS: consulta roda direto; qualquer coisa que ALTERE o ERP (executar "
        "método de escrita, acionar uma tela que grava, processar uma importação) "
        "exige confirmar=true, e antes disso mostre à pessoa exatamente o que vai "
        "rodar. 'Sem dados no período', 'sem acesso a esta função' (módulo não "
        "contratado) e 'precisa_confirmar' são respostas legítimas, não erros a "
        "contornar. Datas de método vão como AAAA-MM-DDT00:00:00; muitos relatórios "
        "aceitam mês e ano como números."
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
async def listar_metodos(
    busca: Annotated[
        str | None, "Texto no nome do método (ex.: 'boleto', 'inadimplencia')."
    ] = None,
    apenas_leitura: Annotated[bool, "Só métodos de consulta, sem os que gravam."] = False,
    limite: Annotated[int, "Quantos devolver."] = 40,
) -> dict[str, Any]:
    """Passo 1 para RELATÓRIOS e CONSULTAS financeiras rápidas (web service).

    Use quando a pessoa quer DADOS: inadimplência, contas a pagar, fluxo de caixa,
    boletos, vencimentos, lançamentos. Busque pela intenção dela (ex.: 'boleto',
    'inadimplencia') e depois chame descrever_metodo no que achar. Para OPERAR o
    Ahreas como no painel (importar consumo, emitir, cadastrar), use listar_telas.
    """
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
    nome: Annotated[str, "Nome do método, como aparece em listar_metodos."],
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
                    "listar_metodos para ver os nomes desta instalação."
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
            "Pode alterar o ERP. Exige confirmar=true e AHREAS_PERMITIR_ESCRITA "
            "ligado. Mostre à pessoa o que vai rodar antes de confirmar."
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
                    "listar_metodos para ver os nomes desta instalação."
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

    if grava(nome):
        if not confirmar:
            return {
                "ok": False,
                "status": "precisa_confirmar",
                "resumo": (
                    f"O método {nome} pode alterar o ERP, com "
                    f"{parametros or 'nenhum parâmetro'}. Mostre isso para a pessoa "
                    "e só chame de novo com confirmar=true depois do sim."
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


# --- Modo telas: o que o operador faz no Web, por HTTP -----------------------
#
# O web service não expõe tudo — a maior parte das operações vive só nas telas
# ASP.NET. Estas três ferramentas dão acesso a elas com a mesma forma buscar →
# descrever → executar: o catálogo de telas vem do menu da instalação, nada é
# escrito à mão, e uma tela que grava passa pela mesma confirmação.


async def _sessao_web() -> Any:
    """A sessão web de quem está chamando.

    No remoto, é a sessão do painel aberta no login OAuth desta pessoa — assim as
    telas agem no nome dela. No local (stdio), é a única sessão, aberta do
    ambiente. Sessão remota vencida vira pedido de novo login.
    """
    from fastmcp.server.dependencies import get_access_token

    try:
        token = get_access_token()
    except Exception:
        token = None
    if token is not None:
        atual = sessao.obter(token.token)
        if atual is None:
            raise SessaoExpirada("Sua sessão do Ahreas expirou. Faça login novamente.")
        if atual.web is None:
            raise SessaoExpirada("Sua sessão do painel não está ativa. Faça login novamente.")
        return atual.web

    from ahreas_mcp.telas.servico import sessao_stdio

    return await sessao_stdio()


@mcp.tool
async def listar_telas(
    busca: Annotated[str | None, "Texto no nome da tela (ex.: 'consumo', 'boleto')."] = None,
    limite: Annotated[int, "Quantas devolver."] = 40,
) -> dict[str, Any]:
    """Passo 1 para OPERAR o Ahreas como no painel web (383 telas da instalação).

    Use quando a pessoa quer FAZER algo que se faz na tela: importar leitura de
    consumo (gás/água), emitir boleto, lançar, cadastrar, processar emissão,
    gerar remessa — ou quando listar_metodos não achou um método para a
    consulta. Busque pela intenção (ex.: 'consumo', 'boleto', 'emissão') e chame
    descrever_tela no caminho que achar para ver os campos e as ações.
    """
    from ahreas_mcp.telas import catalogo

    sessao = await _sessao_web()
    telas = await catalogo.buscar(sessao, busca)
    return {
        "total": len(telas),
        "mostrando": min(limite, len(telas)),
        "telas": [{"nome": t.nome, "caminho": t.caminho} for t in telas[:limite]],
    }


@mcp.tool
async def descrever_tela(
    caminho: Annotated[str, "Caminho da tela, como em listar_telas."],
) -> dict[str, Any]:
    """Detalha uma tela: os campos que ela pede e as ações (botões) disponíveis."""
    from ahreas_mcp.telas import executor, formulario

    sessao = await _sessao_web()
    html, _ = await sessao.abrir(caminho)
    form = formulario.analisar(html)
    return {
        "ok": True,
        "caminho": caminho,
        "campos": [
            {"nome": c.nome, "tipo": c.tipo, **({"opcoes": list(c.opcoes)} if c.opcoes else {})}
            for c in form.campos
        ],
        "acoes": [
            {
                "acao": a.alvo,
                "rotulo": a.rotulo,
                "efeito": executor.efeito_da_acao(a.rotulo + a.alvo).value,
            }
            for a in form.acoes
        ],
    }


def _resultado_tela(r: Any) -> dict[str, Any]:
    saida: dict[str, Any] = {"ok": r.ok, "titulo": r.titulo}
    if r.mensagem:
        saida["mensagem"] = r.mensagem
    if r.colunas or r.linhas:
        saida["colunas"] = list(r.colunas)
        saida["linhas"] = [list(linha) for linha in r.linhas]
    if r.campos_editaveis:
        # Os campos preenchíveis da grid (uma grade de edição): o nome a informar
        # em `campos`, o valor atual e a que linha pertence (bloco/unidade).
        saida["campos_editaveis"] = [dict(c) for c in r.campos_editaveis]
        saida["total_campos_editaveis"] = len(r.campos_editaveis)
    return saida


@mcp.tool
async def executar_acao_tela(
    caminho: Annotated[str, "Caminho da tela."],
    acao: Annotated[str, "A ação/botão a acionar (ver descrever_tela)."],
    campos: Annotated[
        dict[str, str] | None, "Valores dos campos a preencher (aceita muitos)."
    ] = None,
    continuar: Annotated[
        bool, "true para agir sobre o estado da ação anterior (encadear passos na mesma tela)."
    ] = False,
    confirmar: Annotated[bool, "Obrigatório quando a ação altera o ERP."] = False,
) -> dict[str, Any]:
    """Aciona uma tela: preenche campos e dispara um botão, por HTTP.

    Encadeia passos com `continuar=true`: filtrar → alterar → gravar mantêm o
    estado da tela entre as chamadas, sem reabrir. Se a ação abre uma grade de
    linhas (ex.: consumos, lançamentos em lote), TODAS as linhas vêm de uma vez
    (a paginação é expandida automaticamente) e os campos preenchíveis de cada
    linha voltam em `campos_editaveis` — informe-os todos de uma vez em `campos`
    para gravar em massa. Consulta roda direto; ação que altera o ERP exige
    confirmar=true e AHREAS_PERMITIR_ESCRITA.
    """
    from ahreas_mcp.telas import executor
    from ahreas_mcp.telas.sessao_web import LoginWebRecusado, TelaIndisponivel

    ef = executor.efeito_da_acao(acao)
    if ef is executor.EfeitoAcao.ESCRITA:
        if not confirmar:
            return {
                "ok": False,
                "status": "precisa_confirmar",
                "resumo": (
                    f"A ação {acao} na tela {caminho} pode alterar o ERP, com "
                    f"{len(campos) if campos else 'os'} campo(s). Mostre isso para a pessoa "
                    "e só chame de novo com confirmar=true depois do sim."
                ),
            }
        if not configuracao().permitir_escrita:
            return {
                "ok": False,
                "erro": {
                    "codigo": "ahreas.escrita.desligada",
                    "mensagem": (
                        "Ações que alteram o ERP estão desligadas neste servidor. O "
                        "administrador liga em AHREAS_PERMITIR_ESCRITA."
                    ),
                },
            }
    try:
        sessao = await _sessao_web()
        resultado = await executor.executar(
            sessao, caminho, campos or {}, acao, continuar=continuar
        )
    except (LoginWebRecusado, TelaIndisponivel) as erro:
        return {"ok": False, "erro": {"codigo": "ahreas.tela.indisponivel", "mensagem": str(erro)}}
    return _resultado_tela(resultado)


@mcp.tool
async def importar_arquivo_em_tela(
    caminho: Annotated[str, "Caminho de qualquer tela de importação (ver descrever_tela)."],
    nome_arquivo: Annotated[str, "Nome do arquivo, ex. 'leituras.txt'."],
    conteudo: Annotated[str, "Conteúdo do arquivo em texto."],
    acao_processar: Annotated[str, "Botão que processa/grava (ver ações em descrever_tela)."],
    acao_reconhecer: Annotated[
        str | None, "Botão que a tela dispara ao aceitar o arquivo, se houver (ver descrever_tela)."
    ] = None,
    campos: Annotated[dict[str, str] | None, "Campos extras da tela a preencher."] = None,
    confirmar: Annotated[bool, "Obrigatório: a importação grava no ERP."] = False,
) -> dict[str, Any]:
    """Sobe um arquivo em QUALQUER tela de importação e, com confirmação, processa.

    Serve para toda tela que recebe arquivo (leitura de consumo, retorno bancário,
    lote de lançamentos, etc.) — nada é específico de um fluxo. Descubra a tela em
    listar_telas e os botões em descrever_tela (`acao_processar` é o que grava;
    `acao_reconhecer`, se existir, é o que a tela dispara ao aceitar o arquivo).

    Primeiro chame SEM confirmar: o arquivo é enviado e a tela o reconhece — o
    próprio Ahreas pode preencher campos a partir dele — sem gravar; mostre o
    resultado à pessoa. Só depois do sim dela, chame de novo com confirmar=true.
    """
    from ahreas_mcp.telas import executor, formulario
    from ahreas_mcp.telas.sessao_web import (
        LoginWebRecusado,
        TelaIndisponivel,
        campos_ocultos,
    )

    try:
        sessao = await _sessao_web()
        html, estado = await sessao.abrir(caminho)
        client_state, campo_cs = await sessao.enviar_arquivo(
            html, caminho, nome_arquivo, conteudo.encode("utf-8"), "text/plain"
        )
        corpo = dict(estado)
        corpo[campo_cs] = client_state
        # Se a tela reconhece o arquivo por um postback próprio, aciona-o (não grava).
        html_reconhecido = (
            await sessao.postar(caminho, corpo, acao_reconhecer) if acao_reconhecer else html
        )
    except (LoginWebRecusado, TelaIndisponivel) as erro:
        return {"ok": False, "erro": {"codigo": "ahreas.tela.indisponivel", "mensagem": str(erro)}}

    if not confirmar:
        form = formulario.analisar(html_reconhecido)
        return {
            "ok": True,
            "status": "arquivo_enviado_sem_processar",
            "arquivo_reconhecido": nome_arquivo in html_reconhecido,
            "resumo": (
                f"Arquivo {nome_arquivo} enviado à tela {caminho}. Processar ({acao_processar}) "
                "vai gravar no ERP — chame de novo com confirmar=true depois de a pessoa revisar."
            ),
            "campos_da_tela": [c.nome for c in form.campos],
        }
    if not configuracao().permitir_escrita:
        return {
            "ok": False,
            "erro": {
                "codigo": "ahreas.escrita.desligada",
                "mensagem": "A importação grava no ERP; ligue AHREAS_PERMITIR_ESCRITA.",
            },
        }
    # Reenvia com o arquivo já reconhecido e aciona o processamento.
    corpo = dict(campos_ocultos(html_reconhecido))
    corpo[campo_cs] = client_state
    for nome, valor in (campos or {}).items():
        corpo[nome] = valor
    html_final = await sessao.postar(caminho, corpo, acao_processar)
    return _resultado_tela(executor.analisar_resultado(html_final))


@mcp.tool
async def diagnostico_web(
    caminho: Annotated[str | None, "Tela específica a conferir; senão, uma amostra."] = None,
) -> dict[str, Any]:
    """Confere se o modo telas está de pé: login web, catálogo e forma das telas.

    Por ser replay de tela, o modo telas depende da forma do painel. Esta
    ferramenta faz login, conta as telas do menu e abre uma amostra delas para
    confirmar que ainda têm forma reconhecível (campos e ações) — um alarme caso
    a Ahreas mude o painel. Passe `caminho` para checar uma tela específica antes
    de uma operação importante nela.
    """
    from ahreas_mcp.telas import catalogo, formulario
    from ahreas_mcp.telas.servico import SemLoginWeb
    from ahreas_mcp.telas.sessao_web import LoginWebRecusado

    resultado: dict[str, Any] = {}
    try:
        sessao = await _sessao_web()
    except (SemLoginWeb, LoginWebRecusado) as erro:
        return {"ok": False, "login_web": f"falhou: {erro}"}
    resultado["login_web"] = "ok"
    try:
        telas = await catalogo.carregar(sessao)
        resultado["telas_no_menu"] = len(telas)
        # Amostra: as telas pedidas, ou as três primeiras do catálogo.
        alvos = [caminho] if caminho else [t.caminho for t in telas[:3]]
        conferidas = []
        for alvo in alvos:
            html, _ = await sessao.abrir(alvo)
            form = formulario.analisar(html)
            conferidas.append(
                {"caminho": alvo, "campos": len(form.campos), "acoes": len(form.acoes)}
            )
        resultado["telas_conferidas"] = conferidas
        resultado["ok"] = bool(telas) and all(c["campos"] or c["acoes"] for c in conferidas)
    except Exception as erro:  # noqa: BLE001 - diagnóstico nunca derruba
        resultado["ok"] = False
        resultado["erro"] = f"{type(erro).__name__}: {erro}"
    return resultado
