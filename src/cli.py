"""CLI 入口：pdf-agent ask "你的问题" """

from __future__ import annotations

import asyncio
import os

import typer
from dotenv import load_dotenv

app = typer.Typer(
    help="PDF Research Agent — 用自然语言检索和阅读本地 PDF",
    add_completion=False,
)


@app.callback()
def _root() -> None:
    """PDF Research Agent — 用自然语言检索和阅读本地 PDF。"""


@app.command()
def ask(
    question: str = typer.Argument(..., help="自然语言问题，例如：哪几篇 PDF 讲了强化学习？"),
) -> None:
    """向 Agent 提问，它会自动检索本地 PDF 并给出带引用的回答。"""
    load_dotenv()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        typer.secho(
            "错误：缺少 OPENAI_API_KEY。请复制 .env.example 为 .env 并填写后重试。",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    from src.agent import PdfResearchAgent

    agent = PdfResearchAgent(
        model=os.environ.get("OPENAI_MODEL") or None,
        api_key=api_key,
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        pdf_root=os.environ.get("PDF_ROOT") or None,
        verbose=True,
    )
    answer = asyncio.run(agent.ask(question))
    typer.echo(answer)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
