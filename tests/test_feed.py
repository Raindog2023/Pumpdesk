import json, pathlib
from pumpdesk.feed import events_from_notification, Deduper

FIX = pathlib.Path(__file__).parent / "fixtures"


def notif(sig, logs, slot=5, err=None):
    return {"jsonrpc": "2.0", "method": "logsNotification",
            "params": {"result": {"context": {"slot": slot},
                                  "value": {"signature": sig, "err": err, "logs": logs}}}}


S = json.loads((FIX / "samples.json").read_text())


def test_extracts_events_with_slot_and_signature():
    msg = notif("sigA", ["Program log: x", "Program data: " + S["buy"]["data"]], slot=77)
    evs = events_from_notification(msg)
    assert len(evs) == 1
    slot, key, ev = evs[0]
    assert slot == 77 and key == ("sigA", 1) and ev["kind"] == "trade"


def test_failed_transactions_are_skipped():
    assert events_from_notification(notif("s", ["Program data: " + S["buy"]["data"]], err={"x": 1})) == []


def test_non_notifications_are_ignored():
    assert events_from_notification({"jsonrpc": "2.0", "result": 1, "id": 1}) == []


def test_deduper_drops_repeats_but_not_placeholder_signatures():
    d = Deduper(maxlen=3)
    assert d.fresh(("a", 0)) and not d.fresh(("a", 0))
    ph = ("1" * 64, 0)
    assert d.fresh(ph) and d.fresh(ph)
    for k in "bcd":
        d.fresh((k, 0))
    assert d.fresh(("a", 0))  # evicted after maxlen
