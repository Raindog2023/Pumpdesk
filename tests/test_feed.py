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
    assert slot == 77 and key == ("sigA", S["buy"]["data"]) and ev["kind"] == "trade"


def test_same_event_logged_twice_in_one_tx_is_deduped():
    line = "Program data: " + S["buy"]["data"]
    d = Deduper()
    keys = [k for _, k, _ in events_from_notification(notif("sigB", [line, "Program log: y", line]))]
    assert [d.fresh(k) for k in keys] == [True, False]


def test_failed_transactions_are_skipped():
    assert events_from_notification(notif("s", ["Program data: " + S["buy"]["data"]], err={"x": 1})) == []


def test_non_notifications_are_ignored():
    assert events_from_notification({"jsonrpc": "2.0", "result": 1, "id": 1}) == []


def test_deduper_drops_repeats_and_evicts_old_keys():
    d = Deduper(maxlen=3)
    assert d.fresh(("a", 0)) and not d.fresh(("a", 0))
    for k in "bcd":
        d.fresh((k, 0))
    assert d.fresh(("a", 0))  # evicted after maxlen
