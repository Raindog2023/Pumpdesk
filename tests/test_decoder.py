import json
import pathlib

from pumpdesk.decoder import b58encode, decode_program_data

S = json.loads((pathlib.Path(__file__).parent / "fixtures" / "samples.json").read_text())


def test_b58_matches_known_system_program():
    assert b58encode(bytes(32)) == "11111111111111111111111111111111"


def test_decodes_sell_trade():
    ev = decode_program_data(S["sell"]["data"])
    assert ev["kind"] == "trade"
    assert ev["mint"] == "83UM2ihe4Xbbsq1HCBWbMpow5YEAoUYaCQ1CnrQ9pump"
    assert ev["is_buy"] is False
    assert ev["sol"] == 256628355
    assert ev["tokens"] == 2635180115086
    assert ev["user"] == "CdzCJ4P4W7kJA2ApCnxSw9aTpUQKfyVH6hNCtG9vQwzf"
    assert ev["vsol"] == 55861470523 and ev["vtok"] == 576246916181323
    assert ev["rtok"] == 296346916181323
    assert ev["fee_bps"] == 95 and ev["creator_fee_bps"] == 30
    assert ev["creator"] == "4X9m2buomxUZJLvE8GgHpke3SPMWDGeR4rLvLr38hw3p"


def test_decodes_buy_trade():
    ev = decode_program_data(S["buy"]["data"])
    assert ev["kind"] == "trade" and ev["is_buy"] is True


def test_decodes_create():
    ev = decode_program_data(S["CreateEvent"]["data"])
    assert ev["kind"] == "create"
    assert ev["symbol"] == "SUPPLY" and ev["name"] == "Supply Sept"
    assert ev["mint"] == "yUH3wBbZXq2sjNPfJwRa4YdegkJm5ERmMCZiDzRYUyR"
    assert ev["creator"] == "bwamJzztZsepfkteWRChggmXuiiCQvpLqPietdNfSXa"


def test_decodes_complete():
    ev = decode_program_data(S["CompleteEvent"]["data"])
    assert ev == {"kind": "complete", "mint": "6xcLnAebDFLgPDBrVrGzHEcMvjVQfMW6yQL4NvECpump"}


def test_unknown_or_garbage_returns_none():
    assert decode_program_data("AAAA") is None
    assert decode_program_data("not base64!!") is None
    assert decode_program_data("") is None
