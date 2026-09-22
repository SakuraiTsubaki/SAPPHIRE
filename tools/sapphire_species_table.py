#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from sapphire_rom_expansion import (
    identify_native,
    install_blob,
    offset_to_ptr,
    verify_expanded,
)

LEGACY_DATA_SIZE = 26
LEGACY_RECORD_STRIDE = 28
LEGACY_RECORD_COUNT = 412  # SPECIES_NONE through Chimecho; SPECIES_EGG is the historical end constant.
EXPANDED_RECORD_SIZE = 40
EXPANDED_CAPACITY = 4096
DIRECTORY_NAME = "species_data"

DEFINED_FLAG = 1 << 0
NO_FLIP_FLAG = 1 << 1

# Unique Bulbasaur BaseStats record, verified once in every supplied Sapphire ROM.
BULBASAUR_SIGNATURE = bytes.fromhex(
    "2d31312d41410c032d400001000000001f144603010741000003"
)

# 412 records x 28-byte compiler stride, including the all-zero 2-byte tail
# padding in every record.
CANONICAL_LEGACY_SHA256 = (
    "e3a2e4c0b165602701bd3beab5ea0885643d0b5e4cf69898edb43d9f600a7b5c"
)

# ExpandedSpeciesV1:
#   8 byte fields
#   8 u16 fields
#   14 byte fields
#   1 reserved u16
# = 40 bytes.
EXPANDED_RECORD = struct.Struct("<8B8H14BH")
assert EXPANDED_RECORD.size == EXPANDED_RECORD_SIZE


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def find_legacy_table(data: bytes) -> tuple[int, bytes]:
    hits = []
    pos = 0
    while True:
        hit = data.find(BULBASAUR_SIGNATURE, pos)
        if hit < 0:
            break
        hits.append(hit)
        pos = hit + 1

    if len(hits) != 1:
        raise ValueError(
            f"Bulbasaur BaseStats signature: expected exactly 1 match, found {hits}"
        )

    start = hits[0] - LEGACY_RECORD_STRIDE
    if start < 0:
        raise ValueError("BaseStats signature occurs before a possible SPECIES_NONE record")

    species_none = data[start:start + LEGACY_RECORD_STRIDE]
    if species_none != b"\0" * LEGACY_RECORD_STRIDE:
        raise ValueError(
            "BaseStats SPECIES_NONE record is not the expected all-zero padded record"
        )

    table = data[start:start + LEGACY_RECORD_COUNT * LEGACY_RECORD_STRIDE]
    if len(table) != LEGACY_RECORD_COUNT * LEGACY_RECORD_STRIDE:
        raise ValueError("BaseStats table is truncated")

    for species_id in range(LEGACY_RECORD_COUNT):
        off = species_id * LEGACY_RECORD_STRIDE
        if table[off + LEGACY_DATA_SIZE:off + LEGACY_RECORD_STRIDE] != b"\0\0":
            raise ValueError(f"BaseStats padding changed at species {species_id}")

    digest = sha256(table)
    if digest != CANONICAL_LEGACY_SHA256:
        raise ValueError(f"unexpected legacy BaseStats SHA-256: {digest}")

    return start, table


def legacy_to_expanded(record: bytes, species_id: int) -> bytes:
    if len(record) != LEGACY_DATA_SIZE:
        raise ValueError("legacy BaseStats data must be 26 bytes")

    stats = list(record[0:6])
    type1, type2, catch_rate, exp_yield = record[6:10]
    ev = int.from_bytes(record[10:12], "little")
    item1 = int.from_bytes(record[12:14], "little")
    item2 = int.from_bytes(record[14:16], "little")
    gender_ratio = record[16]
    egg_cycles = record[17]
    friendship = record[18]
    growth_rate = record[19]
    egg_group1 = record[20]
    egg_group2 = record[21]
    ability1 = record[22]
    ability2 = record[23]
    safari_flee = record[24]
    body_and_flip = record[25]
    body_color = body_and_flip & 0x7F
    no_flip = (body_and_flip >> 7) & 1

    ev_yields = [(ev >> (2 * i)) & 0x3 for i in range(6)]
    flags = (
        (DEFINED_FLAG if species_id != 0 else 0)
        | (NO_FLIP_FLAG if no_flip else 0)
    )

    return EXPANDED_RECORD.pack(
        *stats,
        catch_rate,
        gender_ratio,
        type1,
        type2,
        exp_yield,
        item1,
        item2,
        ability1,
        ability2,
        0,  # ability3: absent in the Gen III source record
        *ev_yields,
        egg_cycles,
        friendship,
        growth_rate,
        egg_group1,
        egg_group2,
        safari_flee,
        body_color,
        flags,
        0,  # reserved
    )


def expanded_to_legacy(record: bytes, species_id: int) -> bytes:
    if len(record) != EXPANDED_RECORD_SIZE:
        raise ValueError("expanded species record must be 40 bytes")

    values = EXPANDED_RECORD.unpack(record)
    stats = values[0:6]
    catch_rate = values[6]
    gender_ratio = values[7]
    (
        type1,
        type2,
        exp_yield,
        item1,
        item2,
        ability1,
        ability2,
        ability3,
    ) = values[8:16]
    ev_yields = values[16:22]
    (
        egg_cycles,
        friendship,
        growth_rate,
        egg_group1,
        egg_group2,
        safari_flee,
        body_color,
        flags,
    ) = values[22:30]
    reserved = values[30]

    if type1 > 0xFF or type2 > 0xFF:
        raise ValueError("expanded type ID cannot round-trip to Gen III u8")
    if exp_yield > 0xFF:
        raise ValueError("expanded EXP yield cannot round-trip to Gen III u8")
    if ability1 > 0xFF or ability2 > 0xFF or ability3 != 0:
        raise ValueError(
            "expanded ability state cannot round-trip to Gen III BaseStats"
        )
    if any(value > 3 for value in ev_yields):
        raise ValueError(
            "expanded EV yield cannot round-trip to Gen III 2-bit fields"
        )
    if body_color > 0x7F or reserved != 0:
        raise ValueError(
            "expanded body/reserved fields cannot round-trip to Gen III"
        )

    if species_id == 0:
        if flags & DEFINED_FLAG:
            raise ValueError("SPECIES_NONE must not be marked defined")
    elif not flags & DEFINED_FLAG:
        raise ValueError("legacy species must remain marked defined")

    if flags & ~(DEFINED_FLAG | NO_FLIP_FLAG):
        raise ValueError(
            "unknown expanded species flags prevent legacy round-trip"
        )

    ev = sum(value << (2 * i) for i, value in enumerate(ev_yields))
    body_and_flip = body_color | (0x80 if flags & NO_FLIP_FLAG else 0)

    out = bytearray()
    out.extend(stats)
    out.extend((type1, type2, catch_rate, exp_yield))
    out.extend(ev.to_bytes(2, "little"))
    out.extend(item1.to_bytes(2, "little"))
    out.extend(item2.to_bytes(2, "little"))
    out.extend(
        (
            gender_ratio,
            egg_cycles,
            friendship,
            growth_rate,
            egg_group1,
            egg_group2,
            ability1,
            ability2,
            safari_flee,
            body_and_flip,
        )
    )
    return bytes(out)


def build_expanded_table(source: bytes) -> tuple[bytes, dict]:
    legacy_offset, legacy = find_legacy_table(source)
    records = []

    for species_id in range(LEGACY_RECORD_COUNT):
        start = species_id * LEGACY_RECORD_STRIDE
        record = legacy[start:start + LEGACY_DATA_SIZE]
        expanded = legacy_to_expanded(record, species_id)
        if expanded_to_legacy(expanded, species_id) != record:
            raise AssertionError(
                f"species {species_id} failed legacy BaseStats round-trip"
            )
        records.append(expanded)

    empty_record = bytes(EXPANDED_RECORD_SIZE)
    records.extend(
        [empty_record] * (EXPANDED_CAPACITY - LEGACY_RECORD_COUNT)
    )
    table = b"".join(records)

    if len(table) != EXPANDED_CAPACITY * EXPANDED_RECORD_SIZE:
        raise AssertionError("expanded species table has unexpected size")

    for species_id in range(LEGACY_RECORD_COUNT, EXPANDED_CAPACITY):
        off = species_id * EXPANDED_RECORD_SIZE
        if table[off:off + EXPANDED_RECORD_SIZE] != empty_record:
            raise AssertionError(
                f"reserved species {species_id} is not zero-filled"
            )

    return table, {
        "result": "pass",
        "legacy_table_offset": legacy_offset,
        "legacy_table_pointer": f"0x{offset_to_ptr(legacy_offset):08X}",
        "legacy_data_size": LEGACY_DATA_SIZE,
        "legacy_record_stride": LEGACY_RECORD_STRIDE,
        "legacy_record_count": LEGACY_RECORD_COUNT,
        "legacy_sha256": sha256(legacy),
        "expanded_record_size": EXPANDED_RECORD_SIZE,
        "expanded_capacity": EXPANDED_CAPACITY,
        "expanded_table_size": len(table),
        "expanded_sha256": sha256(table),
        "defined_species_ids": [1, LEGACY_RECORD_COUNT - 1],
        "reserved_species_ids": [LEGACY_RECORD_COUNT, EXPANDED_CAPACITY - 1],
        "legacy_unown_extra_constants": [413, 439],
        "legacy_unown_mapping_status": (
            "not stored in gBaseStats; preserve through a separate compatibility "
            "mapping before those logical IDs are reused"
        ),
        "legacy_round_trip": True,
    }


def install_into_expanded_rom(data: bytes) -> tuple[bytes, dict]:
    rom_info = verify_expanded(data)
    source_prefix = data[:rom_info["input_size"]]
    table, build_report = build_expanded_table(source_prefix)

    output, install_report = install_blob(
        data,
        DIRECTORY_NAME,
        table,
        count=EXPANDED_CAPACITY,
        stride=EXPANDED_RECORD_SIZE,
        flags=0,
        alignment=4,
    )

    return output, {
        "result": "pass",
        "source_rom": rom_info["known_source_rom"],
        "build": build_report,
        "install": install_report,
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build/install the SAPPHIRE ExpandedSpeciesV1 species_data table"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build")
    p_build.add_argument("rom", type=Path, help="native Sapphire ROM")
    p_build.add_argument("output", type=Path, help="species_data binary")
    p_build.add_argument("--force", action="store_true")

    p_install = sub.add_parser("install")
    p_install.add_argument("rom", type=Path, help="existing SAPPX10 expanded ROM")
    p_install.add_argument("output", type=Path)
    p_install.add_argument("--force", action="store_true")

    args = ap.parse_args()

    if args.output.exists() and not args.force:
        raise FileExistsError(
            f"refusing to overwrite existing output: {args.output}"
        )
    if args.rom.resolve() == args.output.resolve():
        raise ValueError("output must not overwrite the input ROM")

    data = args.rom.read_bytes()

    if args.command == "build":
        identify_native(data)
        table, report = build_expanded_table(data)
        args.output.write_bytes(table)
        report["output"] = str(args.output)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    output, report = install_into_expanded_rom(data)
    args.output.write_bytes(output)
    report["output"] = str(args.output)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
