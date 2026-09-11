# ahreas-mcp

Servidor MCP para o ERP **Ahreas** (Ahreas / Superlógica), o sistema de gestão de
condomínios. Ele lê o catálogo de métodos direto do WSDL da sua própria
instalação, descreve o que cada um faz e executa consultas e operações via SOAP
com a credencial de web service da administradora. É o primeiro MCP de Ahreas —
não existe hoje nenhum outro, oficial ou de comunidade.

A ideia é a mesma de manter a verdade na fonte: em vez de uma lista de métodos
escrita à mão dentro do projeto, o servidor pergunta ao próprio Ahreas o que ele
publica. Uma instalação real expõe cerca de 139 métodos entre os dois web
services, e os módulos que a sua administradora não contratou aparecem como
"sem acesso" — a mesma resposta que o ERP dá, não um erro.

## O que ele consegue

- Listar o catálogo de métodos lido dos WSDL de `administracaoweb` e
  `Condominioweb`, com busca por texto.
- Descrever um método: parâmetros, formato esperado, se lê ou escreve.
- Executar um método com a credencial de web service, devolvendo o conteúdo
  normalizado.
- Diagnosticar a instalação: se os dois serviços respondem e se a credencial é
  aceita.

## O que ele não consegue

- **Login de operador (painel web) não é usado.** A integração é toda por web
  service; o servidor autentica por `usuario` + `senha` + `chave` em cada
  chamada, não por sessão de navegador.
- **Módulo não contratado não executa.** Se a licença não cobre um método, o
  Ahreas recusa com "não possui acesso" e o servidor repassa isso como resposta.
- **Ele não emula regra de negócio.** Nunca reproduz um método por conta própria;
  se o Ahreas não expõe, o servidor diz que não expõe.

## Segurança

- **A credencial nunca é parâmetro de ferramenta.** Usuário, senha e chave entram
  por variável de ambiente ou `.env` e ficam no processo; nenhuma tool as recebe,
  então o modelo não as vê e elas não aparecem no histórico da conversa.
- **Escrita vem desligada.** `AHREAS_PERMITIR_ESCRITA` é `false` por padrão. Toda
  operação com efeito exige, além disso, uma confirmação explícita.
- **Não versione o `.env`.** Ele já está no `.gitignore`.

## Configuração

Todas as variáveis usam o prefixo `AHREAS_`. Copie o `.env.example` como ponto de
partida.

| Variável | Obrigatória | Padrão | Para que serve |
| --- | --- | --- | --- |
| `AHREAS_BASE_URL` | sim | — | Endereço do Ahreas, ex. `https://sistema.suaadm.com.br`. |
| `AHREAS_USUARIO` | sim | — | Usuário do web service. |
| `AHREAS_SENHA` | sim | — | Senha desse usuário. |
| `AHREAS_CHAVE` | sim | — | Chave de acesso da integração. |
| `AHREAS_TIMEOUT_SEGUNDOS` | não | `30` | Tempo máximo por chamada SOAP. |
| `AHREAS_TENTATIVAS` | não | `2` | Tentativas num soluço de rede. |
| `AHREAS_CACHE_WSDL_SEGUNDOS` | não | `3600` | Validade do catálogo lido do WSDL. |
| `AHREAS_PERMITIR_ESCRITA` | não | `false` | Libera operações que gravam no ERP. |

## Licença

MIT.

## Aviso

Projeto independente, **sem qualquer vínculo com a Ahreas ou a Superlógica**.
Ahreas é marca de seus detentores, citada aqui apenas para identificar o sistema
com o qual este servidor se comunica.
