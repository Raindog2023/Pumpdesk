import json, pathlib
from pumpdesk.engine import Engine
from pumpdesk.rules import parse_rules, DEFAULT_YAML
from pumpdesk.decoder import decode_program_data

FIX = pathlib.Path(__file__).parent / "fixtures"


def replay(engine, path):
    rows = [json.loads(l) for l in open(path)]
    t0 = rows[0]["rx"]
    for r in rows:
        ev = decode_program_data(r["data"])
        if ev:
            engine.handle(r["slot"], ev, now=r["rx"] - t0 + 1000.0, local_hour=14)
    return rows


def test_replay_real_capture_tracks_new_tokens_and_logs_decisions(tmp_path):
    e = Engine(parse_rules(DEFAULT_YAML), data_dir=tmp_path)
    replay(e, FIX / "capture.jsonl")
    assert len(e.tracker.tokens) >= 20          # 26 creates in the capture
    assert e.stats["events"] > 2000
    snap = e.snapshot(now=1100.0)
    assert snap["feed"]["events"] == e.stats["events"]
    assert "positions" in snap and "tokens" in snap and "account" in snap


def test_pass_triggers_paper_entry_and_journal(tmp_path):
    rules = parse_rules(DEFAULT_YAML + "")
    rules.entry.curve_min_pct = 0          # make a synthetic token pass immediately
    rules.entry.max_top10_pct = 100
    e = Engine(rules, data_dir=tmp_path)
    e.handle(10, {"kind": "create", "mint": "MX", "symbol": "MX", "name": "", "uri": "",
                  "bonding_curve": "", "user": "D", "creator": "D"}, now=1.0, local_hour=12)
    tr = {"kind": "trade", "mint": "MX", "sol": 10**9, "tokens": 30 * 10**12, "is_buy": True,
          "user": "U", "ts": 0, "vsol": 31 * 10**9, "vtok": 1040 * 10**12, "rsol": 0,
          "rtok": 760 * 10**12, "fee_bps": 95, "creator": "D", "creator_fee_bps": 30}
    e.handle(11, tr, now=2.0, local_hour=12)
    assert "MX" in e.paper.pending
    e.handle(12, tr | {"user": "V"}, now=3.0, local_hour=12)
    assert "MX" in e.paper.positions
    assert (tmp_path / "tokens.csv").read_text().count("PASS") == 1
    alerts = e.drain_alerts()
    assert any("PAPER BUY" in a for a in alerts)
