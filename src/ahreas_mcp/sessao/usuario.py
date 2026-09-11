"""A sessão de uma pessoa conectada ao servidor remoto.

Cada pessoa que faz login por OAuth vira uma sessão com a credencial dela do
Ahreas — usuário e senha. É isso que faz a execução cair na conta certa: cada
chamada SOAP vai com o usuário daquela pessoa, e o Ahreas aplica as permissões
dele. Não há usuário técnico compartilhado.

O Ahreas autentica por chamada, com usuário e senha no corpo de cada requisição
— não existe token de sessão do lado dele para guardar no lugar. Então a sessão
guarda a própria senha, em claro, na memória do processo. É o compromisso
inevitável de qualquer proxy de credencial para um sistema que autentica assim;
o alcance é limitado pela janela da sessão. A senha some no logout e na
expiração, fica fora do `repr` da credencial e nunca é registrada em log — nem
o envelope SOAP que a carrega.

As sessões vivem num dicionário do processo: isto pressupõe um processo só. Um
deploy com réplicas precisaria de um armazenamento compartilhado no lugar.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from ahreas_mcp.soap.cliente import Credencial


@dataclass
class SessaoUsuario:
    credencial: Credencial
    expira_em: float
    # A sessão web da pessoa (cookie do painel), quando o login web deu certo.
    # É por ela que as ferramentas de tela agem no nome de quem está conectado.
    web: Any = None

    @property
    def usuario(self) -> str:
        return self.credencial.usuario

    def expirada(self, agora: float) -> bool:
        return agora >= self.expira_em


_sessoes: dict[str, SessaoUsuario] = {}


def abrir(
    id_sessao: str, credencial: Credencial, vida_segundos: float, web: Any = None
) -> SessaoUsuario:
    """Registra a sessão de quem acabou de logar. `id_sessao` é o identificador
    opaco que o token de acesso carrega (nunca o usuário em claro)."""
    sessao = SessaoUsuario(credencial=credencial, expira_em=time.time() + vida_segundos, web=web)
    _sessoes[id_sessao] = sessao
    return sessao


def obter(id_sessao: str) -> SessaoUsuario | None:
    """A sessão viva de um identificador, ou `None` se não existe ou expirou.
    Uma sessão expirada é descartada na hora em que é procurada."""
    sessao = _sessoes.get(id_sessao)
    if sessao is None:
        return None
    if sessao.expirada(time.time()):
        _sessoes.pop(id_sessao, None)
        return None
    return sessao


def encerrar(id_sessao: str) -> None:
    """Esquece a sessão e, com ela, a credencial em memória."""
    _sessoes.pop(id_sessao, None)


def _limpar_tudo() -> None:
    """Usado por teste."""
    _sessoes.clear()
