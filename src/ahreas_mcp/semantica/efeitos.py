"""O que o WSDL não conta: se um método lê ou grava, e em que fluxo ele vive.

O WSDL da instalação dá o catálogo e as assinaturas, mas não diz se um método é
uma consulta ou uma operação que altera o ERP — e o nome engana (`ContasPara
Aprovacao` só consulta; `AprovacaoPagtosProcessar` grava). Errar isso é grave:
um método de escrita tratado como leitura roda sem confirmação.

Por isso o efeito de cada método foi revisado à mão, a partir de sondagem real
contra uma instalação (mensagens de validação do próprio ERP) e da documentação
oficial. O que não estiver mapeado aqui cai numa heurística **conservadora**:
qualquer nome que cheire a escrita vira `INCERTO`, nunca `LEITURA` — assim um
método novo que o Ahreas adicione não escapa como consulta.
"""

from __future__ import annotations

import re
from enum import StrEnum


class Efeito(StrEnum):
    LEITURA = "leitura"
    ESCRITA = "escrita"
    # Existe, mas não foi possível confirmar que é só leitura. Tratado como
    # escrita para efeito de confirmação: pode ter efeito colateral (emitir
    # boleto, enviar e-mail/SMS, iniciar processo).
    INCERTO = "incerto"


# Efeito revisado à mão. Chave é o nome-base, sem os sufixos _XML/_Json/_PDF/_Url
# — todas as variantes de um método compartilham o efeito. Só entram aqui os
# métodos que NÃO são leitura simples; o resto é leitura por padrão.
_ESCRITA_BASE: frozenset[str] = frozenset(
    {
        "AprovacaoContasPagar",
        "AprovacaoPagtosProcessar",
        "BoletoAtualizadoConfirmacao",
        "CobrancaGeracaoAcordo",
        "CobrancaRecebimentos",
        "ContaDigital",
        "GeracaoRemessaCotasCondominiais",
        "GeracaoRemessaProcessoCobranca",
        "InadimplenciaZero",
        "InadimplenciaZero_EmissoesGarantidasOperacao",
        "LancamentosContabeis_Inserir",
        "RecepcaoContaAPagar",
        "RecepcaoContaAPagar_Documentos",
        "ReciboAvulso",
        "ReservaEspaco_InserirRateio",
        "AtualizacaoCadastral_Aut_Contatos",
        "AtualizacaoCadastral_Aut_Dados",
        "AtualizacaoCadastral_Aut_Dados_Cliente",
        "AtualizacaoCadastral_Aut_Endereco",
    }
)

# Efeito não confirmado: emitem documento/cobrança ou disparam envio, e a
# sondagem não descartou efeito colateral. Tratados como escrita na execução.
_INCERTO_BASE: frozenset[str] = frozenset(
    {
        "CartaCobranca",  # processou 45s+ na sondagem; pode gerar cartas em lote
        "ProcessoCobranca",  # "Processo de Cobrança": consulta pesada ou início de processo
        "SegundaViaBoletos",  # emissão de 2ª via pode gerar registro bancário
        "SegundaViaBoletos_Chatbot",  # tem enviar_email/enviar_sms
        "SegundaViaBoletos_Parcela_Chatbot",
        "SegundaViaBoletos_PDF_Personalizado",
        "CertidaoNegativaDebitos",  # gera certidão; efeito não verificado
        # A família de atualização cadastral recebe dados/URLs como entrada. As
        # variantes `_Aut_` gravam (já em _ESCRITA_BASE); estas outras submetem
        # ou consultam um protocolo — não confirmado, então tratadas como escrita.
        "AtualizacaoCadastral_Dados",
        "AtualizacaoCadastral_Dados_Cliente",
        "AtualizacaoCadastral_Contatos",
        "AtualizacaoCadastral_Endereco",
        "AtualizacaoCadastral_Documentos",
    }
)

# Métodos que a heurística marcaria como escrita mas são, comprovadamente,
# leitura. A sondagem devolveu dados de consulta para todos eles.
_LEITURA_APESAR_DO_NOME: frozenset[str] = frozenset(
    {
        "AprovacaoPagtosConsultar",
        "ContasParaAprovacao",
        "Recibos_Cancelados",
    }
)

# Radicais que denunciam escrita num nome não catalogado. Conservador de
# propósito: preferir um falso "INCERTO" (que só pede confirmação) a um falso
# "LEITURA" (que rodaria sem ela).
_RADICAIS_ESCRITA = re.compile(
    r"(inserir|processar|geracao|recepcao|confirmacao|remessa|_aut_|inserirrateio|"
    r"_operacao|excluir|cancelar|gravar|atualizar|atualizacaocadastral|aprovacao|"
    r"baixa|emiss|enviar)",
    re.IGNORECASE,
)


def base(nome: str) -> str:
    """O nome-base do método, sem sufixo de formato ou de variante.

    O Ahreas usa tanto `_XML` quanto `XML` colado (`AprovacaoPagtosProcessarXML`),
    então ambos são removidos — senão uma variante de escrita seria rotulada como
    incerta por não casar com a lista revisada.
    """
    for sufixo in ("_XML", "_Json", "_PDF", "_Url", "XML", "Json"):
        if nome.endswith(sufixo):
            return nome[: -len(sufixo)]
    return nome


def efeito(nome: str) -> Efeito:
    b = base(nome)
    if b in _LEITURA_APESAR_DO_NOME:
        return Efeito.LEITURA
    if b in _ESCRITA_BASE:
        return Efeito.ESCRITA
    if b in _INCERTO_BASE:
        return Efeito.INCERTO
    # Não catalogado: heurística conservadora.
    if _RADICAIS_ESCRITA.search(b):
        return Efeito.INCERTO
    return Efeito.LEITURA


def grava(nome: str) -> bool:
    """Se executar este método exige confirmação e escrita liberada."""
    return efeito(nome) in (Efeito.ESCRITA, Efeito.INCERTO)
