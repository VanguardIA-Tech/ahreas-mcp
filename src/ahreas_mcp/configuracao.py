"""Tudo que o servidor precisa saber para falar com um Ahreas.

O Ahreas expõe duas superfícies SOAP num único host: `administracaoweb` e
`Condominioweb`, ambas em `/{servico}/wsdocumentos.asmx`. A autenticação é por
chamada — cada método leva `usuario`, `senha` e `chave` no corpo, não há sessão
a manter. Essas três credenciais nunca viajam como parâmetro de tool: entram por
variável de ambiente e ficam no processo, então o modelo não as vê.

O catálogo de métodos não é escrito neste projeto: é lido do WSDL da própria
instalação, então a lista reflete exatamente o que aquele Ahreas publica.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Configuracao(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AHREAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Endereço do Ahreas -------------------------------------------------
    # A base do sistema, ex. https://sistema.suaadministradora.com.br. Os dois
    # web services penduram embaixo dela.
    base_url: str

    # A chave de acesso do web service. Longa, cheia de símbolos: é o passe da
    # administradora para usar a integração, separada do login de cada pessoa.
    # Fica no servidor e vale para todas as sessões — o Ahreas recusa a chamada
    # inteira se ela estiver errada.
    chave: SecretStr

    # --- Identidade do modo local (stdio) ----------------------------------
    # Usuário e senha do Ahreas de quem instalou, para uso pessoal por stdio. No
    # modo remoto (HTTP + OAuth) cada pessoa entra com a sua na tela de login, e
    # estes ficam vazios. O Ahreas valida usuário e senha individualmente, então
    # é daqui (ou da sessão) que saem as permissões aplicadas.
    usuario: str | None = None
    senha: SecretStr | None = None

    # --- Login do modo telas (Web) no stdio --------------------------------
    # O painel web do Ahreas loga por e-mail, com a mesma senha do usuário. É
    # esse login que dá acesso às telas (o "modo telas"). No stdio, informe o
    # e-mail aqui; a senha, se omitida, reaproveita AHREAS_SENHA.
    web_email: str | None = None
    web_senha: SecretStr | None = None
    # O módulo web onde ficam as telas de operação. Na prática é o condomínios;
    # fica configurável para não presumir a estrutura de uma instalação.
    modulo_web: str = "condominioweb"

    # --- Servidor remoto e OAuth (modo multiusuário) -----------------------
    # A URL pública por onde os clientes MCP chegam. É a âncora do OAuth: entra
    # no issuer, no audience dos tokens e nos metadados de descoberta. Sem ela,
    # o servidor só serve stdio local.
    public_url: str | None = None
    # Quanto tempo a sessão do navegador vale antes de exigir novo login.
    sessao_horas: int = 12

    # --- Comportamento da chamada ------------------------------------------
    # O Ahreas é lento e sua disponibilidade oscila; um relatório grande passa
    # de 20s. Leitura é repetida uma vez num soluço de rede.
    timeout_segundos: float = 30.0
    tentativas: int = 2

    # O WSDL de cada serviço tem centenas de KB e quase nunca muda entre
    # deploys do Ahreas. Cache longo evita rebaixá-lo a cada busca de método.
    cache_wsdl_segundos: int = 3600

    # Escrita desligada por padrão: quem liga é o administrador, sabendo que a
    # partir daí uma tool pode lançar contábil, aprovar pagamento ou emitir
    # recibo no ERP de verdade.
    permitir_escrita: bool = False

    @field_validator("base_url")
    @classmethod
    def _base_sem_barra(cls, valor: str) -> str:
        return valor.rstrip("/")

    @field_validator("public_url")
    @classmethod
    def _public_sem_barra(cls, valor: str | None) -> str | None:
        return valor.rstrip("/") if valor else None

    def url_servico(self, servico: str) -> str:
        """O endpoint .asmx de um dos dois web services."""
        return f"{self.base_url}/{servico}/wsdocumentos.asmx"

    @property
    def tem_identidade(self) -> bool:
        """Se há usuário e senha do Ahreas configurados para o modo stdio."""
        return bool(self.usuario and self.senha)

    @property
    def modo_remoto(self) -> bool:
        """Servir por HTTP com OAuth exige saber a própria URL pública."""
        return self.public_url is not None


@lru_cache
def configuracao() -> Configuracao:
    """Lida uma vez. Credencial ausente falha quando for usada, não no import."""
    return Configuracao()  # type: ignore[call-arg]
