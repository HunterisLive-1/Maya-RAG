"""Local HTTP API for the Electron Settings UI (127.0.0.1 only)."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from threading import Thread
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from book_rag import book_rag
from env_io import merge_write_env, parse_env_file
from hud_ws_bridge import push_event
from ingest_books import ingest_pdf
from paths import books_dir, env_local_path

logger = logging.getLogger("boilermind-settings")


def mask_api_key(key: str) -> str:
    k = (key or "").strip()
    if len(k) <= 4:
        return ""
    return f"****{k[-4:]}"


class TestApiBody(BaseModel):
    api_key: str = Field("", description="Gemini API key to validate")


class SaveBody(BaseModel):
    api_key: str | None = None
    top_k: int | None = Field(None, ge=1, le=50)
    hud_port: int | None = Field(None, ge=1024, le=65535)
    voice: str | None = None


class IngestBody(BaseModel):
    pdf_path: str = Field(...)
    book_name: str = Field("", description="Human-readable title")


def test_gemini_key(api_key: str) -> dict[str, Any]:
    ak = api_key.strip()
    if not ak:
        return {"success": False, "error": "Empty API key"}
    try:
        from google.genai import Client

        client = Client(api_key=ak)
        pager = getattr(client.models, "list", None)
        if callable(pager):
            lst = pager()
            if lst is not None and hasattr(lst, "__iter__") and not isinstance(lst, (str, bytes)):
                next(iter(lst), None)
        return {"success": True}
    except StopIteration:
        return {"success": False, "error": "API returned no models"}
    except Exception:
        pass
    try:
        import google.generativeai as genai

        genai.configure(api_key=ak)
        _ = next(iter(genai.list_models()), None)
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)[:400]}


def _allowed_pdf(raw: Path) -> Path:
    resolved = Path(raw).resolve()
    root = books_dir().resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="PDF must be located under the books folder",
        ) from None
    if resolved.suffix.lower() != ".pdf" or not resolved.is_file():
        raise HTTPException(status_code=400, detail="Invalid PDF path")
    return resolved


def create_settings_app() -> FastAPI:
    app = FastAPI(title="BoilerMind Settings API", docs=False, redoc_url=False)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _books_payload() -> dict[str, Any]:
        books: list[dict[str, Any]] = []
        if book_rag.connect():
            raw = book_rag.get_loaded_books()
            for b in raw:
                books.append({**b, "added": None})
        return {"books": books, "count": len(books)}

    def _broadcast_books() -> None:
        books: list[dict[str, Any]] = []
        if book_rag.connect():
            books = book_rag.get_loaded_books()
        push_event(
            {"type": "books_status", "books": books, "count": len(books)},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"ok": "BoilerMind settings"}

    @app.get("/settings/load")
    def load_settings() -> dict[str, Any]:
        envp = env_local_path()
        data = parse_env_file(envp)
        raw_key = (
            data.get("GEMINI_API_KEY", "").strip()
            or data.get("GOOGLE_API_KEY", "").strip()
        )
        tk = data.get("BOILERMIND_TOP_K", "").strip() or "5"
        try:
            top_k = max(1, min(50, int(tk)))
        except ValueError:
            top_k = 5
        port_s = data.get("BOILERMIND_HUD_PORT", "").strip() or "7070"
        try:
            hud_port = int(port_s)
        except ValueError:
            hud_port = 7070
        voice = (
            data.get("BOILERMIND_VOICE", "").strip()
            or data.get("MAYA_GEMINI_TTS_VOICE", "").strip()
            or "Laomedeia"
        )
        return {
            "api_key_masked": mask_api_key(raw_key),
            "has_api_key": bool(raw_key),
            "top_k": top_k,
            "hud_port": hud_port,
            "voice": voice,
            "loaded_books": _books_payload(),
        }

    @app.get("/settings/books")
    def books() -> dict[str, Any]:
        return _books_payload()

    @app.post("/settings/test-api")
    def test_api(body: TestApiBody) -> dict[str, Any]:
        return test_gemini_key(body.api_key)

    @app.post("/settings/save")
    def save(body: SaveBody) -> dict[str, Any]:
        updates: dict[str, str | None] = {}
        if body.api_key is not None and body.api_key.strip():
            ak = body.api_key.strip()
            updates["GEMINI_API_KEY"] = ak
            updates["GOOGLE_API_KEY"] = ak
        if body.top_k is not None:
            updates["BOILERMIND_TOP_K"] = str(body.top_k)
        if body.hud_port is not None:
            updates["BOILERMIND_HUD_PORT"] = str(body.hud_port)
            updates["BOILERMIND_HUD_WS_PORT"] = str(body.hud_port)
        if body.voice is not None and body.voice.strip():
            vn = body.voice.strip()
            updates["BOILERMIND_VOICE"] = vn
            updates["MAYA_GEMINI_TTS_VOICE"] = vn
        if not updates:
            return {"success": True, "message": "Nothing to save"}
        merge_write_env(env_local_path(), updates)
        return {
            "success": True,
            "message": "Settings saved. Restart BoilerMind for API key / HUD port changes.",
        }

    @app.delete("/settings/book/{book_id}")
    def remove(book_id: str) -> dict[str, Any]:
        bid = (book_id or "").strip()
        if not bid:
            raise HTTPException(400, "book_id required")
        if book_rag.connect():
            removed = book_rag.remove_book(bid)
            _broadcast_books()
            return {"success": True, "removed_approx": removed}
        return {"success": False, "error": "RAG not connected"}

    @app.post("/settings/ingest")
    async def ingest_sse(body: IngestBody):
        resolved = _allowed_pdf(Path(body.pdf_path))
        book_name = (body.book_name or "").strip() or resolved.stem
        book_id = resolved.stem.replace(" ", "_").lower()

        async def generator():
            import queue

            sync_q: queue.Queue = queue.Queue()
            result_holder: dict[str, Any] = {"chunks": 0, "err": None}
            loop = asyncio.get_running_loop()

            def runner() -> None:
                def on_pg(cur: int, tot: int) -> None:
                    sync_q.put(
                        {
                            "type": "progress",
                            "current_page": cur,
                            "total_pages": tot,
                            "status": f"reading page {cur}/{tot}",
                        },
                    )

                try:
                    sync_q.put(
                        {
                            "type": "progress",
                            "current_page": 0,
                            "total_pages": 1,
                            "status": "starting",
                        },
                    )
                    n = ingest_pdf(
                        str(resolved),
                        book_name,
                        book_id,
                        on_page=on_pg,
                        print_progress=False,
                    )
                    result_holder["chunks"] = int(n)
                except Exception as e:
                    result_holder["err"] = str(e)
                finally:
                    sync_q.put({"type": "done"})

            Thread(target=runner, daemon=True).start()

            while True:
                item = await loop.run_in_executor(None, sync_q.get)
                payload = dict(item)
                yield f"data: {json.dumps(payload)}\n\n"
                if payload.get("type") == "done":
                    break

            err = result_holder.get("err")
            if err:
                yield f"data: {json.dumps({'type': 'error', 'error': err})}\n\n"
                return

            chunks = int(result_holder.get("chunks") or 0)
            yield f"data: {json.dumps({'type': 'complete', 'chunks_added': chunks})}\n\n"
            _broadcast_books()

        return StreamingResponse(generator(), media_type="text/event-stream")

    return app


async def serve_settings_forever(port: int) -> None:
    """Run uvicorn in-process on the asyncio event loop."""
    import uvicorn

    app = create_settings_app()
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=int(port),
        log_level="warning",
        loop="asyncio",
    )
    server = uvicorn.Server(config)
    logger.info("Settings API listening on http://127.0.0.1:%s", port)
    await server.serve()
