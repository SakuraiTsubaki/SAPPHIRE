#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

SAVE_SIZE = 128 * 1024
SECTOR_SIZE = 0x1000
SAVE_SIGNATURE = 0x08012025
EXT_SECTORS = (30, 31)

SAVE_CHUNK_SIZES = [
    0x890,
    0xF80, 0xF80, 0xF80, 0xC40,
    0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0x7D0,
]

HEADER_SIZE = 0x40
PAYLOAD_SIZE = SECTOR_SIZE - HEADER_SIZE
MAGIC = b"SAPPXSV1"
SCHEMA = 1
HEADER = struct.Struct("<8sHHIIHHII4sB27x")

SPECIES_CAPACITY = 4096
SPECIES_BITSET_BYTES = SPECIES_CAPACITY // 8
MON_SLOTS = 14 * 30 + 6
ABILITY_SLOT_BYTES = (MON_SLOTS * 2 + 7) // 8

PAYLOAD_SEEN = 0x000
PAYLOAD_CAUGHT = 0x200
PAYLOAD_ABILITY_SLOT = 0x400
PAYLOAD_RESERVED = 0x480

KNOWN_ROMS = {
    "3233342c2f3087e6ffe6c1791cd5867db07df842": ("JP AXPJ rev0", 8 * 1024 * 1024),
    "3ccbbd45f8553c36463f13b938e833f652b793e4": ("EN AXPE rev0", 16 * 1024 * 1024),
    "4722efb8cd45772ca32555b98fd3b9719f8e60a9": ("EN AXPE rev1", 16 * 1024 * 1024),
    "89b45fb172e6b55d51fc0e61989775187f6fe63c": ("EN AXPE rev2", 16 * 1024 * 1024),
    "5a087835009d552d4c5c1f96be3be3206e378153": ("DE AXPD rev0", 16 * 1024 * 1024),
    "7e6e034f9cdca6d2c4a270fdb50a94def5883d17": ("DE AXPD rev1", 16 * 1024 * 1024),
    "c269b5692b2d0e5800ba1ddf117fda95ac648634": ("FR AXPF rev0", 16 * 1024 * 1024),
    "860e93f5ea44f4278132f6c1ee5650d07b852fd8": ("FR AXPF rev1", 16 * 1024 * 1024),
    "f729dd571fb2c09e72c5c1d68fe0a21e72713d34": ("IT AXPI rev0", 16 * 1024 * 1024),
    "73edf67b9b82ff12795622dca412733755d2c0fe": ("IT AXPI rev1", 16 * 1024 * 1024),
    "3a6489189e581c4b29914071b79207883b8c16d8": ("ES AXPS rev0", 16 * 1024 * 1024),
    "0fe9ad1e602e2fafa090aee25e43d6980625173c": ("ES AXPS rev1", 16 * 1024 * 1024),
}


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def calculate_save_checksum(data: bytes, size: int) -> int:
    total = 0
    for offset in range(0, size, 4):
        total = (total + struct.unpack_from("<I", data, offset)[0]) & 0xFFFFFFFF
    return ((total >> 16) + total) & 0xFFFF


def is_blank_sector(data: bytes) -> bool:
    return not any(data) or all(b == 0xFF for b in data)


def is_newer_u32(candidate: int, current: int) -> bool:
    delta = (candidate - current) & 0xFFFFFFFF
    return 0 < delta < 0x80000000


def latest_u32(values) -> int:
    values = list(values)
    if not values:
        raise ValueError("no counters available")
    latest = values[0]
    for value in values[1:]:
        if is_newer_u32(value, latest):
            latest = value
    return latest


def analyze_rom(data: bytes) -> dict:
    digest = sha1(data)
    known = KNOWN_ROMS.get(digest)
    if known is None:
        raise ValueError(f"unknown Sapphire SHA-1: {digest}")
    label, expected_size = known
    if len(data) != expected_size:
        raise ValueError(f"ROM size mismatch for {label}: {len(data)} != {expected_size}")
    return {
        "known_rom": label,
        "sha1": digest,
        "size": len(data),
        "game_code": data[0xAC:0xB0].decode("ascii", "strict"),
        "revision": data[0xBC],
    }


def analyze_main_save(data: bytes) -> dict:
    if len(data) != SAVE_SIZE:
        raise ValueError(f"save must be 128 KiB, got {len(data)} bytes")

    groups: dict[int, list[int]] = {}
    recognized_count = 0
    valid_count = 0
    bad_checksums: list[int] = []

    for physical in range(28):
        sector = data[physical * SECTOR_SIZE:(physical + 1) * SECTOR_SIZE]
        section_id, stored_checksum, signature, counter = struct.unpack_from(
            "<HHII", sector, 0xFF4
        )
        if signature == SAVE_SIGNATURE and 0 <= section_id < 14:
            recognized_count += 1
            calculated = calculate_save_checksum(sector, SAVE_CHUNK_SIZES[section_id])
            if calculated != stored_checksum:
                bad_checksums.append(physical)
                continue
            valid_count += 1
            groups.setdefault(counter, []).append(section_id)

    if bad_checksums:
        raise ValueError(
            f"retail main-save checksum failure in physical sectors: {bad_checksums}"
        )

    normalized = {
        counter: sorted(ids)
        for counter, ids in groups.items()
    }
    complete = (
        recognized_count == 28
        and valid_count == 28
        and len(normalized) == 2
        and all(ids == list(range(14)) for ids in normalized.values())
    )
    if not complete:
        raise ValueError(
            f"main save sectors are not two checksum-valid rotating 0..13 sets: {normalized}"
        )

    return {
        "recognized_sector_count": recognized_count,
        "valid_sector_count": valid_count,
        "all_legacy_checksums_valid": True,
        "counter_groups": [
            {"counter": counter, "logical_ids": ids}
            for counter, ids in sorted(normalized.items())
        ],
        "latest_counter": latest_u32(normalized),
    }


def build_payload() -> bytes:
    payload = bytearray(PAYLOAD_SIZE)
    # Schema v1 reserves explicit fixed regions but does not enable form-change.
    # 0x000..0x1FF: expanded species seen bitset (4096 bits)
    # 0x200..0x3FF: expanded species caught bitset (4096 bits)
    # 0x400..0x46A: 2-bit ability-slot state for 426 party+box slots
    # 0x46B..0x47F: alignment/reserved
    # 0x480..end: future metadata; form-change remains deferred
    assert SPECIES_BITSET_BYTES == 0x200
    assert ABILITY_SLOT_BYTES == 107
    assert PAYLOAD_RESERVED < len(payload)
    return bytes(payload)


def build_sector(
    game_code: str,
    revision: int,
    base_save_counter: int,
    sequence: int,
    payload: bytes,
) -> bytes:
    if len(payload) != PAYLOAD_SIZE:
        raise ValueError(f"payload must be {PAYLOAD_SIZE} bytes")

    payload_crc = zlib.crc32(payload) & 0xFFFFFFFF
    header = bytearray(b"\0" * HEADER_SIZE)
    HEADER.pack_into(
        header,
        0,
        MAGIC,
        SCHEMA,
        HEADER_SIZE,
        sequence,
        base_save_counter,
        len(payload),
        0,
        payload_crc,
        0,
        game_code.encode("ascii"),
        revision,
    )
    header_crc = zlib.crc32(header) & 0xFFFFFFFF
    struct.pack_into("<I", header, 28, header_crc)
    return bytes(header) + payload


def parse_extension_sector(sector: bytes) -> dict | None:
    if len(sector) != SECTOR_SIZE:
        raise ValueError("extension sector must be exactly 4 KiB")
    if sector[:8] != MAGIC:
        return None

    (
        _magic,
        schema,
        header_size,
        sequence,
        base_save_counter,
        payload_len,
        flags,
        payload_crc,
        header_crc,
        game_code,
        revision,
    ) = HEADER.unpack_from(sector, 0)

    header = bytearray(sector[:HEADER_SIZE])
    struct.pack_into("<I", header, 28, 0)
    if (zlib.crc32(header) & 0xFFFFFFFF) != header_crc:
        raise ValueError("extension header CRC mismatch")
    if schema != SCHEMA or header_size != HEADER_SIZE or payload_len != PAYLOAD_SIZE:
        raise ValueError("unsupported extension layout")

    payload = sector[HEADER_SIZE:HEADER_SIZE + payload_len]
    if (zlib.crc32(payload) & 0xFFFFFFFF) != payload_crc:
        raise ValueError("extension payload CRC mismatch")

    return {
        "schema": schema,
        "sequence": sequence,
        "base_save_counter": base_save_counter,
        "flags": flags,
        "game_code": game_code.decode("ascii", "strict"),
        "revision": revision,
        "payload_crc32": f"{payload_crc:08x}",
    }


def extension_status(data: bytes) -> dict:
    copies = []
    unknown_nonblank = []
    for sector_id in EXT_SECTORS:
        sector = data[sector_id * SECTOR_SIZE:(sector_id + 1) * SECTOR_SIZE]
        parsed = parse_extension_sector(sector)
        if parsed is not None:
            copies.append({"sector": sector_id, **parsed})
        elif not is_blank_sector(sector):
            unknown_nonblank.append(sector_id)

    if unknown_nonblank:
        return {
            "result": "unknown_nonblank",
            "unknown_nonblank_sectors": unknown_nonblank,
            "copies": copies,
        }
    if not copies:
        return {"result": "empty", "copies": []}

    latest = copies[0]
    for candidate in copies[1:]:
        if is_newer_u32(candidate["sequence"], latest["sequence"]):
            latest = candidate
    return {
        "result": "pass",
        "copies": copies,
        "latest": latest,
    }


def initialize(rom: bytes, save: bytes) -> tuple[bytes, dict]:
    rom_info = analyze_rom(rom)
    main = analyze_main_save(save)
    status = extension_status(save)
    if status["result"] == "unknown_nonblank":
        raise ValueError(
            "sectors 30/31 contain unknown nonblank data; refusing to overwrite"
        )

    payload = build_payload()
    output = bytearray(save)
    preserved = save[:30 * SECTOR_SIZE]

    output[30 * SECTOR_SIZE:31 * SECTOR_SIZE] = build_sector(
        rom_info["game_code"],
        rom_info["revision"],
        main["latest_counter"],
        1,
        payload,
    )
    output[31 * SECTOR_SIZE:32 * SECTOR_SIZE] = build_sector(
        rom_info["game_code"],
        rom_info["revision"],
        main["latest_counter"],
        2,
        payload,
    )

    if output[:30 * SECTOR_SIZE] != preserved:
        raise AssertionError("sectors 0-29 changed during extension initialization")

    report = {
        "rom": rom_info,
        "main_save": main,
        "input_save_sha1": sha1(save),
        "output_save_sha1": sha1(output),
        "size_unchanged": len(output) == len(save) == SAVE_SIZE,
        "sectors_0_29_preserved_byte_for_byte": True,
        "extension": extension_status(output),
    }
    return bytes(output), report


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Initialize/verify the SAPPHIRE mirrored save-extension sectors"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init")
    init.add_argument("rom", type=Path)
    init.add_argument("save", type=Path)
    init.add_argument("output", type=Path)

    verify = sub.add_parser("verify")
    verify.add_argument("save", type=Path)

    inspect = sub.add_parser("inspect")
    inspect.add_argument("save", type=Path)

    args = ap.parse_args()

    if args.command == "init":
        output, report = initialize(
            args.rom.read_bytes(),
            args.save.read_bytes(),
        )
        args.output.write_bytes(output)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    data = args.save.read_bytes()
    main = analyze_main_save(data)
    ext = extension_status(data)
    report = {
        "size": len(data),
        "sha1": sha1(data),
        "main_save": main,
        "extension": ext,
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if ext["result"] != "unknown_nonblank" else 1


if __name__ == "__main__":
    raise SystemExit(main())
