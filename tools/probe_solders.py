from solders.pubkey import Pubkey
import hashlib

PUMP = Pubkey.from_string("6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P")
FEES = Pubkey.from_string("pfeeUxB6jkeY1Hxd7CsFCAjcbHA9rWtchMGdZ6VojVZ")
SYS = Pubkey.from_string("11111111111111111111111111111111")
TOKEN = Pubkey.from_string("TokenkegQfeZyiNwAJybLUhqVgwRMpWhqfHh8B7dNXs")
ATA = Pubkey.from_string("ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL")

mint = Pubkey.default()
wallet = Pubkey.default()
creator = Pubkey.default()

# PDAs
bc, bump = Pubkey.find_program_address([b"bonding-curve", bytes(mint)], PUMP)
print(f"bonding_curve: {bc} bump={bump}")

abc, _ = Pubkey.find_program_address([bytes(bc), bytes(TOKEN), bytes(mint)], ATA)
print(f"associated_bonding_curve: {abc}")

auser, _ = Pubkey.find_program_address([bytes(wallet), bytes(TOKEN), bytes(mint)], ATA)
print(f"associated_user: {auser}")

cv, _ = Pubkey.find_program_address([b"creator-vault", bytes(creator)], PUMP)
print(f"creator_vault: {cv}")

g, _ = Pubkey.find_program_address([b"global"], PUMP)
print(f"global: {g}")

ea, _ = Pubkey.find_program_address([b"__event_authority"], PUMP)
print(f"event_authority: {ea}")

FC_SEED = bytes.fromhex("0156e0f693665acf44db1568bf175baa5189cb97f5d2ff3b655d2bb6fd6d18b0")
fc, _ = Pubkey.find_program_address([b"fee_config", FC_SEED], FEES)
print(f"fee_config: {fc}")

uva, _ = Pubkey.find_program_address([b"user_volume_accumulator", bytes(wallet)], PUMP)
gva, _ = Pubkey.find_program_address([b"global_volume_accumulator"], PUMP)
print(f"user_volume_accumulator: {uva}")
print(f"global_volume_accumulator: {gva}")

# Discriminators
for name in ["buy", "sell"]:
    d1 = hashlib.sha256(f"instruction:{name}".encode()).digest()[:8]
    d2 = hashlib.sha256(f"global:{name}".encode()).digest()[:8]
    print(f"{name}: instruction={d1.hex()} global={d2.hex()}")
    print(f"  IDL says buy=66063d1201daebea sell=33e685a4017f83ad")

# Verify the IDL discriminator matches
BUY_DISC = bytes([102, 6, 61, 18, 1, 218, 235, 234])
SELL_DISC = bytes([51, 230, 133, 164, 1, 127, 131, 173])
print(f"\nBUY_DISC hex: {BUY_DISC.hex()}")
print(f"SELL_DISC hex: {SELL_DISC.hex()}")
