"""FastAPI 网页端：聊天页面 + /ask 接口，复用 ReAct Agent 与 MCP 工具链。

启动：
    pdf-web                     # pip install -e . 后可用
    python -m src.web.server

打开 http://127.0.0.1:8000 即可在网页里提问。
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="PDF Research Agent", docs_url=None, redoc_url=None, openapi_url=None)


class AskRequest(BaseModel):
    question: str


@app.get("/")
def index() -> FileResponse:
    """返回聊天页面。"""
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/ask")
async def ask(req: AskRequest) -> JSONResponse:
    """跑一轮完整的 ReAct 循环，返回最终答案和工具调用轨迹。"""
    question = req.question.strip()
    if not question:
        return JSONResponse({"error": "问题不能为空"}, status_code=400)
    if not os.environ.get("OPENAI_API_KEY"):
        return JSONResponse(
            {"error": "缺少 OPENAI_API_KEY，请先在 .env 中填写"}, status_code=401
        )

    from src.agent import PdfResearchAgent

    agent = PdfResearchAgent(
        model=os.environ.get("OPENAI_MODEL") or None,
        api_key=os.environ.get("OPENAI_API_KEY"),
        base_url=os.environ.get("OPENAI_BASE_URL") or None,
        pdf_root=os.environ.get("PDF_ROOT") or None,
    )
    try:
        answer = await agent.ask(question)
    except Exception as exc:
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)
    return JSONResponse({"answer": answer, "trace": agent.trace})


def main() -> None:
    load_dotenv()
    # Windows 下 asyncio 子进程（MCP stdio）要求 Proactor 事件循环
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
