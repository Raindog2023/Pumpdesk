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

    def snap():
        s = engine.snapshot(time.time())
        s["feed"]["connected"] = bool(feed and feed.connected)
        s["feed"]["reconnects"] = feed.reconnects if feed else 0
        s["feed"]["lag_s"] = round(time.time() - feed.last_msg, 1) if feed and feed.last_msg else None
        s["feed"]["error"] = feed.last_error if feed else ""
        s["feed"]["source"] = (feed.url.split("?")[0] if feed else "")
        s["telegram"] = {"enabled": bool(telegram and telegram.enabled),
                         "error": getattr(telegram, "last_error", "")}
        return s

    @app.get("/", response_class=HTMLResponse)
    def index():
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/icon.png")
    def icon():
        return FileResponse(STATIC / "icon.png", media_type="image/png")

    @app.get("/api/snapshot")
    def snapshot():
        return snap()

    @app.get("/api/rules", response_class=PlainTextResponse)
    def get_rules():
        return rules_path.read_text(encoding="utf-8")

    @app.put("/api/rules")
    async def put_rules(req: Request):
        text = (await req.body()).decode("utf-8")
        try:
            rules = parse_rules(text)
        except ValueError as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=400)
        rules_path.write_text(text, encoding="utf-8")
        engine.set_rules(rules)
        app.state.rules_mtime = rules_path.stat().st_mtime
        return {"ok": True}

    @app.post("/api/pause")
    def pause():
        engine.paused = True
        return {"ok": True}

    @app.post("/api/resume")
    def resume():
        engine.paused = False
        return {"ok": True}

    @app.post("/api/reset")
    def reset():
        engine.reset_account()
        return {"ok": True}

    @app.post("/api/close/{mint}")
    def close(mint: str):
        if not engine.paper.request_exit(mint, time.time()):
            return JSONResponse({"ok": False, "error": "no such open position"}, status_code=404)
        return {"ok": True}

    @app.post("/api/telegram/test")
    async def tg_test():
        ok = bool(telegram) and await telegram.send("✅ PumpDesk connected. Paper-trading alerts will arrive here.")
        return {"ok": ok, "error": "" if ok else (getattr(telegram, "last_error", "") or "TG_TOKEN / TG_CHAT_ID not set in .env")}

    @app.get("/api/export/{name}")
    def export(name: str):
        p = engine.dir / name
        if name not in EXPORTS or not p.exists():
            return JSONResponse({"error": "not found"}, status_code=404)
        return FileResponse(p, filename=name)

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        await sock.accept()
        try:
            while True:
                await sock.send_json(snap())
                await asyncio.sleep(1.0)
        except (WebSocketDisconnect, RuntimeError):
            return

    return app
