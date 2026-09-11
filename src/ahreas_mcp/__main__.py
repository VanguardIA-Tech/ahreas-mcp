"""Entrada do servidor.

stdio para uso local (Claude Desktop, Claude Code, Cursor); http para servir a
rede com um único processo. O Ahreas fica na rede da administradora e este
servidor precisa alcançá-lo, então o lugar natural dele é lá dentro.
"""

from __future__ import annotations

import argparse

from ahreas_mcp.servidor import mcp


def main() -> None:
    argumentos = argparse.ArgumentParser(
        prog="ahreas-mcp",
        description="Servidor MCP para o ERP Ahreas (condomínios).",
    )
    argumentos.add_argument(
        "--transporte",
        choices=("stdio", "http"),
        default="stdio",
        help="stdio para uso local; http para servir por rede.",
    )
    argumentos.add_argument("--host", default="0.0.0.0")
    argumentos.add_argument("--porta", type=int, default=8000)
    lidos = argumentos.parse_args()
    if lidos.transporte == "stdio":
        mcp.run()
        return
    mcp.run(transport="http", host=lidos.host, port=lidos.porta)


if __name__ == "__main__":
    main()
