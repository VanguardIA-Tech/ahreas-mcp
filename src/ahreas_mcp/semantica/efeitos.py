"""A única coisa que o WSDL não conta: se um método lê ou grava.

Isto não é regra de negócio, e não há nada da administradora aqui. É o mínimo
que o duplo-check exige: para decidir se uma execução roda direto (consulta) ou
pede confirmação (alteração), é preciso saber o efeito do método — e o WSDL do
Ahreas lista os métodos e parâmetros, mas não marca qual grava.

Só dois efeitos, portanto: LEITURA roda direto; ESCRITA pede confirmação. Onde o
nome do método não deixa claro, o padrão é ESCRITA — na dúvida, confirma. Um
método novo que o Ahreas adicione, com nome que cheira a alteração, também cai em
ESCRITA. Nunca o contrário: nada passa como leitura por engano.

A lista foi montada a partir de sondagem real contra uma instalação (as próprias
mensagens do Ahreas) — não de um palpite.
"""

from __future__ import annotations

import re
from enum import StrEnum


class Efeito(StrEnum):
    LEITURA = "leitura"
    ESCRITA = "escrita"


# Métodos que alteram o ERP, revisados um a um. Chave é o nome-base, sem os
# sufixos _XML/_Json/_PDF/_Url — todas as variantes compartilham o efeito.
# Inclui também os de efeito não confirmado (emitem documento/cobrança ou
# disparam envio): na dúvida, entram aqui para exigir confirmação.
_ESCRITA_BASE: frozenset[str] = frozenset(
    {
        # Gravam, sem ambiguidade.
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
        "AtualizacaoCadastral_Dados",
        "AtualizacaoCadastral_Dados_Cliente",
        "AtualizacaoCadastral_Contatos",
        "AtualizacaoCadastral_Endereco",
        "AtualizacaoCadastral_Documentos",
        # Efeito não confirmado: emitem boleto/certidão/carta ou disparam envio.
        # Ficam em escrita por segurança até verificar contra a instalação.
        "CartaCobranca",
        "ProcessoCobranca",
        "SegundaViaBoletos",
        "SegundaViaBoletos_Chatbot",
        "SegundaViaBoletos_Parcela_Chatbot",
        "SegundaViaBoletos_PDF_Personalizado",
        "CertidaoNegativaDebitos",
    }
)

# Métodos que a heurística marcaria como escrita mas são, comprovadamente,
# consulta. A sondagem devolveu dados de leitura para todos eles.
_LEITURA_APESAR_DO_NOME: frozenset[str] = frozenset(
    {
        "AprovacaoPagtosConsultar",
        "ContasParaAprovacao",
        "Recibos_Cancelados",
    }
)

# Radicais que denunciam alteração num nome não catalogado. Conservador de
# propósito: preferir uma confirmação a mais a uma escrita sem confirmação.
_RADICAIS_ESCRITA = re.compile(
    r"(inserir|processar|geracao|recepcao|confirmacao|remessa|_aut_|inserirrateio|"
    r"_operacao|excluir|cancelar|gravar|atualizar|atualizacaocadastral|aprovacao|"
    r"baixa|emiss|enviar)",
    re.IGNORECASE,
)


def base(nome: str) -> str:
    """O nome-base do método, sem sufixo de formato ou de variante.

    O Ahreas usa tanto `_XML` quanto `XML` colado (`AprovacaoPagtosProcessarXML`),
    então ambos são removidos — senão uma variante de escrita seria classificada
    como leitura por não casar com a lista revisada.
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
    # Não catalogado: na dúvida, escrita.
    if _RADICAIS_ESCRITA.search(b):
        return Efeito.ESCRITA
    return Efeito.LEITURA


def grava(nome: str) -> bool:
    """Se executar este método exige confirmação e escrita liberada."""
    return efeito(nome) is Efeito.ESCRITA
