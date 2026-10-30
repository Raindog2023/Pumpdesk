"""PumpDesk entry point: python -m pumpdesk [--no-window]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from .config import load_settings
from .engine import Engine
from .feed import Feed
from .rules import load_rules, parse_rules
from .server import build_app
from .telegram import Telegram

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("pumpdesk")


def already_running(port: int) -> bool:
    import socket
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def open_window(url: str):
    """Open the dashboard as its own app window (Edge/Chrome --app), else a browser tab."""
    candidates = []
    if sys.platform == "win32":
        import os
        for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            candidates += [Path(base) / "Microsoft/Edge/Application/msedge.exe",
                           Path(base) / "Google/Chrome/Application/chrome.exe"]
    candidates += [Path(p) for p in filter(None, (shutil.which("msedge"), shutil.which("google-chrome"),
                                                  shutil.which("chromium")))]
    for exe in candidates:
        if exe.exists():
            profile = ROOT / "data" / "window-profile"
            subprocess.Popen([str(exe), f"--app={url}", "--window-size=1440,900",
                              f"--user-data-dir={profile}", "--no-first-run"])
            return
    webbrowser.open(url)


async def run(no_window: bool):
    s = load_settings(ROOT)
    s.data_dir.mkdir(parents=True, exist_ok=True)
    rules_path = s.data_dir / "rules.yaml"
    engine = Engine(load_rules(rules_path), data_dir=s.data_dir)
    tg = Telegram(s.tg_token, s.tg_chat_id)

    def on_event(slot, ev):
        now = time.time()
        engine.handle(slot, ev, now, datetime.fromtimestamp(now).hour)

    feed = Feed(s.rpc_wss, on_event)
    app = build_app(engine, rules_path, tg, feed)
    app.state.rules_mtime = rules_path.stat().st_mtime

    async def ticker():
        n = 0
        while True:
            await asyncio.sleep(0.5)
            n += 1
            try:
                engine.tick(time.time())
                if n % 10 == 0:
                    engine.save()
                if n % 4 == 0:  # watch rules.yaml for edits made outside the dashboard
                    m = rules_path.stat().st_mtime
                    if m != app.state.rules_mtime:
                        app.state.rules_mtime = m
                        try:
                            engine.set_rules(parse_rules(rules_path.read_text(encoding="utf-8")))
                            log.info("rules.yaml reloaded")
                        except ValueError as e:
                            log.warning("rules.yaml NOT reloaded: %s", e)
            except Exception:  # never let the loop die
                log.exception("tick failed")

    async def alerter():
        while True:
            await asyncio.sleep(1.0)
            msgs = engine.drain_alerts()
            if msgs and engine.rules.alerts.telegram and tg.enabled:
                await tg.send("\n\n".join(msgs[:10]))

    url = f"http://127.0.0.1:{s.port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=s.port, log_level="warning"))
    log.info("PumpDesk %s  |  feed: %s  |  telegram: %s", url, feed.url.split("?")[0],
             "on" if tg.enabled else "off")

    async def opener():
        while not server.started:
            await asyncio.sleep(0.1)
        if not no_window:
            open_window(url)

    tasks = [asyncio.create_task(c) for c in (feed.run(), ticker(), alerter(), opener())]
    try:
        await server.serve()
    finally:
        for t in tasks:
            t.cancel()
        engine.save()
        if tg:
            tg._client and await tg.close()
        log.info("state saved, bye")


def main():
    ap = argparse.ArgumentParser(prog="pumpdesk")
    ap.add_argument("--no-window", action="store_true", help="don't open the dashboard window")
    args = ap.parse_args()
    (ROOT / "data").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(),
                                  RotatingFileHandler(ROOT / "data" / "pumpdesk.log", maxBytes=2_000_000,
                                                      backupCount=3, encoding="utf-8")])
    s = load_settings(ROOT)
    if already_running(s.port):
        log.info("PumpDesk is already running; opening its window")
        if not args.no_window:
            open_window(f"http://127.0.0.1:{s.port}")
        return
    try:
        asyncio.run(run(args.no_window))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
