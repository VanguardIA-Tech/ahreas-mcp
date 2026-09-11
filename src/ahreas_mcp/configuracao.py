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

    # --- Credencial de web service (a mesma para todo método) ---------------
    usuario: str
    senha: SecretStr
    # A chave de acesso do web service. Longa, cheia de símbolos: é ela que
    # libera a integração, separada da senha do usuário.
    chave: SecretStr

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
    def _sem_barra_final(cls, valor: str) -> str:
        return valor.rstrip("/")

    def url_servico(self, servico: str) -> str:
        """O endpoint .asmx de um dos dois web services."""
        return f"{self.base_url}/{servico}/wsdocumentos.asmx"


@lru_cache
def configuracao() -> Configuracao:
    """Lida uma vez. Credencial ausente falha quando for usada, não no import."""
    return Configuracao()  # type: ignore[call-arg]
