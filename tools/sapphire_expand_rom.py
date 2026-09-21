#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

JP_SIZE = 8 * 1024 * 1024
WESTERN_SIZE = 16 * 1024 * 1024
COMMON_BASE = 16 * 1024 * 1024
OUTPUT_SIZE = 32 * 1024 * 1024
HEADER_SIZE = 0x100
MAGIC = b"SAPPX10\0"
VERSION = 1
HEADER = struct.Struct("<8sHHII4sB3x20s32s")

KNOWN_ROMS = {
    "3233342c2f3087e6ffe6c1791cd5867db07df842": ("JP AXPJ rev0", JP_SIZE),
    "3ccbbd45f8553c36463f13b938e833f652b793e4": ("EN AXPE rev0", WESTERN_SIZE),
    "4722efb8cd45772ca32555b98fd3b9719f8e60a9": ("EN AXPE rev1", WESTERN_SIZE),
    "89b45fb172e6b55d51fc0e61989775187f6fe63c": ("EN AXPE rev2", WESTERN_SIZE),
    "5a087835009d552d4c5c1f96be3be3206e378153": ("DE AXPD rev0", WESTERN_SIZE),
    "7e6e034f9cdca6d2c4a270fdb50a94def5883d17": ("DE AXPD rev1", WESTERN_SIZE),
    "c269b5692b2d0e5800ba1ddf117fda95ac648634": ("FR AXPF rev0", WESTERN_SIZE),
    "860e93f5ea44f4278132f6c1ee5650d07b852fd8": ("FR AXPF rev1", WESTERN_SIZE),
    "f729dd571fb2c09e72c5c1d68fe0a21e72713d34": ("IT AXPI rev0", WESTERN_SIZE),
    "73edf67b9b82ff12795622dca412733755d2c0fe": ("IT AXPI rev1", WESTERN_SIZE),
    "3a6489189e581c4b29914071b79207883b8c16d8": ("ES AXPS rev0", WESTERN_SIZE),
    "0fe9ad1e602e2fafa090aee25e43d6980625173c": ("ES AXPS rev1", WESTERN_SIZE),
}


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def analyze(data: bytes) -> dict:
    if len(data) not in (JP_SIZE, WESTERN_SIZE):
        raise ValueError(f"unsupported Sapphire ROM size: {len(data)}")
    digest = sha1(data)
    if digest not in KNOWN_ROMS:
        raise ValueError(f"unknown Sapphire SHA-1: {digest}")
    label, expected_size = KNOWN_ROMS[digest]
    if len(data) != expected_size:
        raise ValueError(f"known ROM size mismatch for {label}: {len(data)} != {expected_size}")
    game_code = data[0xAC:0xB0].decode("ascii", "strict")
    revision = data[0xBC]
    return {
        "known_rom": label,
        "input_size": len(data),
        "input_sha1": digest,
        "input_sha256": sha256(data),
        "game_code": game_code,
        "revision": revision,
        "common_expansion_base": COMMON_BASE,
        "expanded_size": OUTPUT_SIZE,
        "common_expansion_capacity": OUTPUT_SIZE - COMMON_BASE,
    }


def build_header(data: bytes, info: dict) -> bytes:
    header = bytearray(b"\xFF" * HEADER_SIZE)
    HEADER.pack_into(
        header,
        0,
        MAGIC,
        VERSION,
        HEADER_SIZE,
        info["input_size"],
        OUTPUT_SIZE,
        info["game_code"].encode("ascii"),
        info["revision"],
        bytes.fromhex(info["input_sha1"]),
        bytes.fromhex(info["input_sha256"]),
    )
    return bytes(header)


def expand(data: bytes) -> tuple[bytes, dict]:
    info = analyze(data)
    out = bytearray(b"\xFF" * OUTPUT_SIZE)
    out[:len(data)] = data
    out[COMMON_BASE:COMMON_BASE + HEADER_SIZE] = build_header(data, info)
    if out[:len(data)] != data:
        raise AssertionError("input prefix changed during expansion")
    if len(data) < COMMON_BASE and out[len(data):COMMON_BASE] != b"\xFF" * (COMMON_BASE - len(data)):
        raise AssertionError("pre-common-base padding was modified")
    report = dict(info)
    report.update({
        "header_offset": COMMON_BASE,
        "header_size": HEADER_SIZE,
        "allocator_start": COMMON_BASE + HEADER_SIZE,
        "allocator_end_exclusive": OUTPUT_SIZE,
        "output_sha1": sha1(out),
        "output_sha256": sha256(out),
        "input_prefix_preserved": True,
    })
    return bytes(out), report


def verify_expanded(data: bytes) -> dict:
    if len(data) != OUTPUT_SIZE:
        raise ValueError(f"expanded ROM must be {OUTPUT_SIZE} bytes")
    fields = HEADER.unpack_from(data, COMMON_BASE)
    magic, version, header_size, input_size, output_size, game_code, revision, raw_sha1, raw_sha256 = fields
    if magic != MAGIC:
        raise ValueError(f"bad expanded header magic: {magic!r}")
    if version != VERSION or header_size != HEADER_SIZE or output_size != OUTPUT_SIZE:
        raise ValueError("expanded header version/size mismatch")
    if input_size not in (JP_SIZE, WESTERN_SIZE):
        raise ValueError(f"invalid preserved input size: {input_size}")
    prefix = data[:input_size]
    got_sha1 = sha1(prefix)
    got_sha256 = sha256(prefix)
    if got_sha1 != raw_sha1.hex() or got_sha256 != raw_sha256.hex():
        raise ValueError("preserved input prefix hash mismatch")
    if got_sha1 not in KNOWN_ROMS:
        raise ValueError(f"unknown preserved Sapphire SHA-1: {got_sha1}")
    label, expected_size = KNOWN_ROMS[got_sha1]
    if input_size != expected_size:
        raise ValueError("preserved input size does not match known ROM")
    if input_size < COMMON_BASE and data[input_size:COMMON_BASE] != b"\xFF" * (COMMON_BASE - input_size):
        raise ValueError("Japanese compatibility padding is not blank")
    return {
        "result": "pass",
        "known_rom": label,
        "input_size": input_size,
        "game_code": game_code.decode("ascii"),
        "revision": revision,
        "input_sha1": got_sha1,
        "input_sha256": got_sha256,
        "header_offset": COMMON_BASE,
        "expanded_size": OUTPUT_SIZE,
        "allocator_start": COMMON_BASE + HEADER_SIZE,
        "allocator_end_exclusive": OUTPUT_SIZE,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Create/verify the SAPPHIRE 32 MiB expanded ROM container")
    sub = ap.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("rom", type=Path)
    e = sub.add_parser("expand")
    e.add_argument("rom", type=Path)
    e.add_argument("output", type=Path)
    v = sub.add_parser("verify")
    v.add_argument("rom", type=Path)
    args = ap.parse_args()

    if args.command == "verify":
        print(json.dumps(verify_expanded(args.rom.read_bytes()), indent=2, ensure_ascii=False))
        return 0

    data = args.rom.read_bytes()
    if args.command == "analyze":
        print(json.dumps(analyze(data), indent=2, ensure_ascii=False))
        return 0

    expanded, report = expand(data)
    args.output.write_bytes(expanded)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
