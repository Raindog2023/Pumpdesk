import json

from fastapi.testclient import TestClient

from pumpdesk.engine import Engine
from pumpdesk.rules import DEFAULT_YAML, load_rules, parse_rules
from pumpdesk.server import build_app
from pumpdesk.tracker import Token


def passing_engine(tmp_path):
    rules = parse_rules(DEFAULT_YAML)
    rules.entry.curve_min_pct = 0
    rules.entry.max_top10_pct = 100
    engine = Engine(rules, data_dir=tmp_path)
    engine.handle(10, {"kind": "create", "mint": "MX", "symbol": "MX", "name": "",
                       "uri": "", "bonding_curve": "", "user": "D", "creator": "D"},
                  now=1.0, local_hour=12)
    engine.handle(11, {"kind": "trade", "mint": "MX", "sol": 10**9,
                       "tokens": 30 * 10**12, "is_buy": True, "user": "U", "ts": 0,
                       "vsol": 30_951_923_076, "vtok": 1040 * 10**12, "rsol": 0,
                       "rtok": 760_100_000_000_000, "fee_bps": 95,
                       "creator": "D", "creator_fee_bps": 30}, now=2.0, local_hour=12)
    return engine


def test_engine_tick_fills_pending_order_and_snapshot_works(tmp_path):
    engine = passing_engine(tmp_path)
    assert isinstance(engine.paper.pending["MX"].info, dict)
    engine.tick(3.0)
    assert "MX" in engine.paper.positions
    snapshot = engine.snapshot(3.0)
    assert snapshot["account"]["equity"] > 0
    assert snapshot["positions"][0]["value"] > 0
    json.dumps(snapshot)


def test_close_via_engine_tick_builds_frontend_journal(tmp_path):
    engine = passing_engine(tmp_path)
    engine.tick(3.0)
    assert engine.paper.request_exit("MX", now=4.0)
    engine.tick(5.0)
    assert not engine.paper.positions
    snapshot = engine.snapshot(5.0)
    assert len(snapshot["closed"]) == 1
    assert snapshot["by_hour"][0]["trades"] == 1
    assert snapshot["by_exit"]
    assert snapshot["account"]["trades"] == 1
    json.dumps(snapshot)


def test_watching_snapshot_uses_token_metrics_correctly(tmp_path):
    engine = Engine(parse_rules(DEFAULT_YAML), data_dir=tmp_path)
    token = Token("WAIT", "W", "Waiting", "D", 1, 0.0, trades=4,
                  status="WAIT", reasons=["curve too low"], last_trade=1.0)
    engine.tracker.tokens[token.mint] = token
    snapshot = engine.snapshot(60.0)
    assert snapshot["watching"][0]["top10_pct"] == 0
    assert snapshot["watching"][0]["reasons"] == ["curve too low"]


class FakeTG:
    enabled = True
    last_error = ""

    async def send(self, text):
        return True


def test_http_and_websocket_expose_the_frontend_schema(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    engine = Engine(load_rules(rules_path), data_dir=tmp_path)
    app = build_app(engine, rules_path, FakeTG(), None)
    client = TestClient(app)
    expected = {"account", "feed", "telegram", "positions", "watching", "tokens",
                "closed", "by_hour", "by_exit", "fail_reasons", "paused", "now"}
    response = client.get("/api/snapshot")
    assert response.status_code == 200
    assert expected <= response.json().keys()
    assert response.json()["telegram"] == {"enabled": True, "error": ""}
    with client.websocket_connect("/ws") as ws:
        assert expected <= ws.receive_json().keys()


def test_resume_clears_paused_state(tmp_path):
    rules_path = tmp_path / "rules.yaml"
    engine = Engine(load_rules(rules_path), data_dir=tmp_path)
    client = TestClient(build_app(engine, rules_path, FakeTG(), None))
    client.post("/api/pause")
    client.post("/api/resume")
    assert engine.paused is False
