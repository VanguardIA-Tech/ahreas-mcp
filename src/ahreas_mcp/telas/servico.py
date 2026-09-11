"""De onde as ferramentas de tela tiram a sessão web.

No modo local (stdio) há uma pessoa só: a sessão é aberta uma vez com o e-mail e
a senha do ambiente e reaproveitada. No modo remoto, cada pessoa tem a sua, aberta
no login e guardada com o token — e nesse caso a sessão vem de fora, não daqui.
"""

from __future__ import annotations

from ahreas_mcp.configuracao import configuracao
from ahreas_mcp.telas.sessao_web import SessaoWeb, entrar


class SemLoginWeb(Exception):
    """Não há e-mail/senha web configurados para o modo local."""


_sessao_stdio: SessaoWeb | None = None


async def sessao_stdio() -> SessaoWeb:
    """A sessão web do modo local, aberta uma vez a partir do ambiente."""
    global _sessao_stdio
    if _sessao_stdio is not None:
        return _sessao_stdio
    conf = configuracao()
    email = conf.web_email
    senha = conf.web_senha.get_secret_value() if conf.web_senha else None
    if senha is None and conf.senha is not None:
        senha = conf.senha.get_secret_value()
    if not email or not senha:
        raise SemLoginWeb(
            "Sem login web. Configure AHREAS_WEB_EMAIL (e AHREAS_WEB_SENHA, ou reaproveite "
            "AHREAS_SENHA) com o e-mail e a senha de operador do Ahreas."
        )
    _sessao_stdio = await entrar(email, senha)
    return _sessao_stdio


def _limpar() -> None:
    global _sessao_stdio
    _sessao_stdio = None
