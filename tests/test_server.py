import pytest
from fastapi.testclient import TestClient

from pumpdesk.config import parse_env
from pumpdesk.engine import Engine
from pumpdesk.rules import load_rules
from pumpdesk.server import build_app


class FakeTG:
    def __init__(self): self.sent = []
    enabled = True
    async def send(self, text): self.sent.append(text); return True


@pytest.fixture
def client(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    eng = Engine(load_rules(rules_path), data_dir=tmp_path)
    tg = FakeTG()
    app = build_app(eng, rules_path=rules_path, telegram=tg, feed=None)
    c = TestClient(app)
    c.tg, c.eng, c.rules_path = tg, eng, rules_path
    return c


def test_parse_env_handles_comments_quotes_and_blanks():
    env = parse_env('# c\nTG_TOKEN="123:abc"\n\nTG_CHAT_ID=42 # inline\nRPC_WSS=\n')
    assert env == {"TG_TOKEN": "123:abc", "TG_CHAT_ID": "42", "RPC_WSS": ""}


def test_dashboard_page_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "PumpDesk" in r.text


def test_snapshot_endpoint(client):
    j = client.get("/api/snapshot").json()
    assert j["account"]["equity"] == 5.0 and j["feed"]["events"] == 0


def test_rules_roundtrip_and_hot_apply(client):
    txt = client.get("/api/rules").text
    assert "max_top10_pct" in txt
    new = txt.replace("max_dev_pct: 5.0", "max_dev_pct: 3.0")
    r = client.put("/api/rules", content=new)
    assert r.status_code == 200
    assert client.eng.rules.entry.max_dev_pct == 3.0
    assert "max_dev_pct: 3.0" in client.rules_path.read_text()


def test_bad_rules_rejected_and_file_untouched(client):
    before = client.rules_path.read_text()
    r = client.put("/api/rules", content="entry:\n  curve_min_pct: 99\n  curve_max_pct: 1\n")
    assert r.status_code == 400 and "curve_min_pct" in r.json()["error"]
    assert client.rules_path.read_text() == before


def test_pause_resume(client):
    client.post("/api/pause"); assert client.eng.paused
    client.post("/api/resume"); assert not client.eng.paused


def test_telegram_test_message(client):
    r = client.post("/api/telegram/test")
    assert r.json()["ok"] and client.tg.sent


def test_close_unknown_position_404(client):
    assert client.post("/api/close/nope").status_code == 404


def test_export_csv_404_when_empty_then_ok(client, tmp_path):
    assert client.get("/api/export/trades.csv").status_code == 404
    (tmp_path / "trades.csv").write_text("a,b\n1,2\n")
    assert client.get("/api/export/trades.csv").text.startswith("a,b")
    assert client.get("/api/export/../../etc/passwd").status_code == 404


def test_websocket_pushes_snapshots(client):
    with client.websocket_connect("/ws") as ws:
        j = ws.receive_json()
        assert "account" in j
