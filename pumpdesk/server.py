"""Local web server for the dashboard. Binds to 127.0.0.1 only."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
)

from .engine import Engine
from .rules import parse_rules

STATIC = Path(__file__).parent / "static"
EXPORTS = {"trades.csv", "tokens.csv", "rules.yaml"}


def build_app(engine: Engine, rules_path: Path, telegram, feed) -> FastAPI:
    app = FastAPI(title="PumpDesk", docs_url=None, redoc_url=None)

    index_file = STATIC / "index.html"

    def snap():
        now = time.time()
        snapshot = engine.snapshot(now)
        snapshot["feed"].update({
            "connected": bool(feed and getattr(feed, "connected", False)),
            "reconnects": getattr(feed, "reconnects", 0) if feed else 0,
            "lag_s": round(now - feed.last_msg, 1) if (feed and getattr(feed, "last_msg", None)) else None,
            "error": getattr(feed, "last_error", "") if feed else "",
            "source": (feed.url.split("?")[0]) if (feed and getattr(feed, "url", None)) else "",
            "tracked": snapshot["feed"].get("tracked", 0),
        })
        snapshot["telegram"] = {
            "enabled": bool(telegram and getattr(telegram, "enabled", False)),
            "error": getattr(telegram, "last_error", "") if telegram else "",
        }
        return snapshot

    @app.get("/", response_class=HTMLResponse)
    async def index():
        if index_file.exists():
            return HTMLResponse(index_file.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>index.html not found in static/</h1>", status_code=404)

    @app.get("/icon.png")
    async def icon():
        icon_path = STATIC / "icon.png"
        if icon_path.exists():
            return FileResponse(icon_path, media_type="image/png")
        return JSONResponse({"error": "icon not found"}, status_code=404)

    @app.get("/api/snapshot")
    async def snapshot():
        return snap()

    @app.get("/api/rules", response_class=PlainTextResponse)
    async def get_rules():
        if rules_path.exists():
            return rules_path.read_text(encoding="utf-8")
        return ""

    @app.put("/api/rules")
    async def put_rules(req: Request):
        text = (await req.body()).decode("utf-8")
        try:
            rules = parse_rules(text)
        except (TypeError, ValueError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)

        rules_path.write_text(text, encoding="utf-8")
        engine.set_rules(rules)
        app.state.rules_mtime = rules_path.stat().st_mtime
        return {"ok": True}

    @app.post("/api/pause")
    async def pause():
        engine.paused = True
        return {"ok": True, "paused": True}

    @app.post("/api/resume")
    async def resume():
        engine.paused = False
        return {"ok": True, "paused": engine.paused}

    @app.post("/api/reset")
    async def reset():
        engine.reset_account()
        return {"ok": True}

    @app.post("/api/close/{mint}")
    async def close(mint: str):
        trader = getattr(engine, "trader", getattr(engine, "paper", None))
        if trader and trader.request_exit(mint, time.time()):
            return {"ok": True}
        return JSONResponse({"ok": False, "error": "no such open position"}, status_code=404)

    @app.post("/api/telegram/test")
    async def tg_test():
        ok = bool(telegram) and await telegram.send("🔔 PumpDesk connected. Alerts will arrive here.")
        err_msg = getattr(telegram, "last_error", "") or "TG_TOKEN / TG_CHAT_ID not set in .env"
        return {"ok": ok, "error": "" if ok else err_msg}

    @app.get("/api/export/{name}")
    async def export(name: str):
        if name not in EXPORTS:
            return JSONResponse({"error": "not found"}, status_code=404)
        p = engine.dir / name
        if not p.exists():
            return JSONResponse({"error": "file not found"}, status_code=404)
        return FileResponse(p, filename=name)

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        try:
            while True:
                await sock.send_json(snap())
                await asyncio.sleep(1.0)
        except (WebSocketDisconnect, RuntimeError):
            pass

    return app
