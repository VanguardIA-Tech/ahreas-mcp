# syntax=docker/dockerfile:1
#
# Imagem do ahreas-mcp. Construa a partir da RAIZ do repositório:
#
#   docker build -f docker/Dockerfile -t ahreas-mcp .
#
# O servidor só fala SOAP por HTTP com o Ahreas; a imagem não precisa de nada
# além do Python e das dependências.

# --- build -------------------------------------------------------------------
FROM python:3.12-slim AS build

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# hatchling lê o README declarado em pyproject.toml, então ele precisa vir junto.
COPY pyproject.toml README.md ./
COPY src ./src

RUN uv venv /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

# --- runtime -----------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN useradd --create-home --uid 10001 ahreas

COPY --from=build --chown=ahreas:ahreas /opt/venv /opt/venv

USER ahreas
WORKDIR /home/ahreas

# Para servir por rede num único processo, roda em HTTP na porta 8000. Para uso
# local stdio (Claude Desktop/Code, Cursor), sobrescreva o comando: `... ahreas-mcp`.
EXPOSE 8000
ENTRYPOINT ["ahreas-mcp"]
CMD ["--transporte", "http", "--host", "0.0.0.0", "--porta", "8000"]
