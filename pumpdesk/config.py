"""Settings from .env (secrets) - tiny parser, no extra dependency."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def parse_env(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if v[:1] in "\"'" and v[-1:] == v[:1] and len(v) >= 2:
            v = v[1:-1]
        elif " #" in v:
            v = v.split(" #", 1)[0].strip()
        out[k.strip()] = v
    return out


@dataclass
class Settings:
    rpc_wss: str
    tg_token: str
    tg_chat_id: str
    port: int
    data_dir: Path
    rpc_http: str = ""
    wallet_file: str = ""
    live: bool = False


def load_settings(root: Path) -> Settings:
    env_file = root / ".env"
    env = parse_env(env_file.read_text(encoding="utf-8")) if env_file.exists() else {}
    get = lambda k, d="": os.environ.get(k) or env.get(k) or d
    helius = get("HELIUS_KEY")
    wss = get("RPC_WSS") or (f"wss://mainnet.helius-rpc.com/?api-key={helius}" if helius else "")
    http_rpc = (get("RPC_HTTP")
                or (f"https://mainnet.helius-rpc.com/?api-key={helius}" if helius else "")
                or "https://api.mainnet-beta.solana.com")
    return Settings(rpc_wss=wss, rpc_http=http_rpc,
                    tg_token=get("TG_TOKEN"), tg_chat_id=get("TG_CHAT_ID"),
                    port=int(get("PORT", "8787")), data_dir=root / "data",
                    wallet_file=get("WALLET_FILE", ""),
                    live=get("LIVE", "").lower() in ("1", "true", "yes"))
