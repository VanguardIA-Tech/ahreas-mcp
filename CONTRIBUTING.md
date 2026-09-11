# Contribuindo

Obrigado pelo interesse. Este documento é curto de propósito.

## Rodando localmente

O projeto usa [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/VanguardIA-Tech/ahreas-mcp
cd ahreas-mcp
uv sync
```

Copie o exemplo de configuração e preencha com os dados do seu ambiente de
testes:

```bash
cp .env.example .env
```

Para rodar o servidor:

```bash
uv run ahreas-mcp
```

## Antes de abrir um PR

Rode os quatro comandos. São exatamente os mesmos que a CI executa, em Python
3.12 e 3.13.

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Testes não podem depender de um Ahreas de verdade. Se o seu teste precisa de uma
resposta do ERP, use um duplo (o `respx` já é dependência de dev) — a CI não tem
acesso à rede de nenhuma administradora.

## A regra de ouro

Duas coisas não entram neste projeto, em hipótese nenhuma:

**1. Catálogo de método hardcoded.** Nenhuma lista de métodos, nomes ou
assinaturas escrita à mão no código. O catálogo vem do WSDL da própria
instalação. É isso que faz o método específico de uma administradora aparecer
igual aos demais, e é isso que impede o projeto de envelhecer a cada mudança do
Ahreas. A camada `semantica/` é a única exceção, e só acrescenta o que o WSDL
não conta (se um método lê ou grava) — nunca a lista de métodos.

**2. Regra de negócio emulada.** O servidor não reproduz um método por conta
própria. Se o Ahreas não expõe algo, a resposta correta é dizer que não expõe.
Se a licença da administradora recusa um método ("não possui acesso"), isso é
repassado como resposta, não contornado.

## Efeito de método

Todo método que grava — ou cujo efeito não foi confirmado — tem de estar
marcado em `semantica/efeitos.py` como `ESCRITA` ou `INCERTO`, para exigir
confirmação antes de rodar. A dúvida sempre pende para o lado seguro: um método
novo com nome que cheira a escrita cai em `INCERTO` pela heurística, nunca em
`LEITURA`. Rebaixar um método para leitura exige tê-lo verificado.

## Pull requests

- Um assunto por PR.
- Português do Brasil em nomes de identificadores, docstrings e mensagens
  voltadas a pessoas, seguindo o que já existe no código.
- Nunca inclua credencial, URL, nome de condomínio ou de administradora real em
  código, teste, issue ou commit.
