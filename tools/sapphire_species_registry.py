#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from sapphire_rom_expansion import install_blob, verify_expanded

SPECIES_DATA_NAME = "species_data"
DIRECTORY_NAME = "species_registry"
CAPACITY = 4096
SPECIES_RECORD_SIZE = 40
DEFINED_FLAG_OFFSET = 37
DEFINED_FLAG = 1 << 0
MAGIC = b"SAPPREG1"
SCHEMA = 1
BITSET_SIZE = CAPACITY // 8
HEADER = struct.Struct("<8sHHHHII")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def species_data_entry(report: dict) -> dict:
    entries = [
        entry for entry in report["directory"]
        if entry["name"] == SPECIES_DATA_NAME
    ]
    if len(entries) != 1:
        raise ValueError(
            "exactly one species_data directory entry is required"
        )
    entry = entries[0]
    if (
        entry["count"] != CAPACITY
        or entry["stride"] != SPECIES_RECORD_SIZE
        or entry["size"] != CAPACITY * SPECIES_RECORD_SIZE
    ):
        raise ValueError(
            "species_data does not match ExpandedSpeciesV1 geometry"
        )
    return entry


def defined_species_from_table(table: bytes) -> list[int]:
    if len(table) != CAPACITY * SPECIES_RECORD_SIZE:
        raise ValueError("species_data byte size mismatch")

    defined = []
    for species_id in range(CAPACITY):
        flags = table[
            species_id * SPECIES_RECORD_SIZE + DEFINED_FLAG_OFFSET
        ]
        if flags & DEFINED_FLAG:
            defined.append(species_id)

    if 0 in defined:
        raise ValueError(
            "SPECIES_NONE (0) must never be in the defined-species registry"
        )
    if not defined:
        raise ValueError("defined-species registry would be empty")
    return defined


def build_registry(table: bytes) -> tuple[bytes, dict]:
    defined = defined_species_from_table(table)
    bitset = bytearray(BITSET_SIZE)
    for species_id in defined:
        bitset[species_id >> 3] |= 1 << (species_id & 7)

    ids_offset = HEADER.size + BITSET_SIZE
    header = HEADER.pack(
        MAGIC,
        SCHEMA,
        CAPACITY,
        len(defined),
        0,
        BITSET_SIZE,
        ids_offset,
    )
    id_vector = struct.pack(
        f"<{len(defined)}H",
        *defined,
    )
    blob = header + bytes(bitset) + id_vector

    parsed = parse_registry(blob)
    if parsed["defined_ids"] != defined:
        raise AssertionError(
            "defined-species registry round-trip failed"
        )

    return blob, {
        "result": "pass",
        "schema": SCHEMA,
        "capacity": CAPACITY,
        "defined_count": len(defined),
        "first_defined": defined[0],
        "last_defined": defined[-1],
        "bitset_size": BITSET_SIZE,
        "ids_offset": ids_offset,
        "size": len(blob),
        "sha256": sha256(blob),
    }


def parse_registry(blob: bytes) -> dict:
    if len(blob) < HEADER.size + BITSET_SIZE:
        raise ValueError("species_registry blob is truncated")

    (
        magic,
        schema,
        capacity,
        count,
        flags,
        bitset_size,
        ids_offset,
    ) = HEADER.unpack_from(blob)

    if magic != MAGIC or schema != SCHEMA:
        raise ValueError("unsupported species_registry schema")
    if capacity != CAPACITY or bitset_size != BITSET_SIZE:
        raise ValueError("species_registry geometry mismatch")
    if flags != 0:
        raise ValueError(
            "species_registry reserved flags are nonzero"
        )
    if ids_offset != HEADER.size + BITSET_SIZE:
        raise ValueError(
            "species_registry ID-vector offset mismatch"
        )

    expected_size = ids_offset + count * 2
    if len(blob) != expected_size:
        raise ValueError(
            "species_registry size/count mismatch"
        )

    bitset = blob[HEADER.size:ids_offset]
    ids = list(
        struct.unpack_from(f"<{count}H", blob, ids_offset)
    )
    if ids != sorted(set(ids)):
        raise ValueError(
            "species_registry ID vector is not strictly unique/sorted"
        )
    if any(
        species_id <= 0 or species_id >= CAPACITY
        for species_id in ids
    ):
        raise ValueError(
            "species_registry contains an out-of-range species ID"
        )

    from_bits = [
        species_id
        for species_id in range(1, CAPACITY)
        if bitset[species_id >> 3]
        & (1 << (species_id & 7))
    ]
    if from_bits != ids:
        raise ValueError(
            "species_registry bitset and ID vector disagree"
        )

    return {
        "schema": schema,
        "capacity": capacity,
        "defined_count": count,
        "bitset_size": bitset_size,
        "ids_offset": ids_offset,
        "defined_ids": ids,
    }


def extract_species_data(
    data: bytes,
    report: dict,
) -> tuple[bytes, dict]:
    entry = species_data_entry(report)
    start = entry["rom_offset"]
    end = start + entry["size"]
    if start < 0 or end > len(data):
        raise ValueError(
            "species_data directory entry points outside ROM"
        )
    return data[start:end], entry


def build_from_rom(data: bytes) -> tuple[bytes, dict]:
    report = verify_expanded(data)
    table, entry = extract_species_data(data, report)
    blob, build = build_registry(table)
    return blob, {
        **build,
        "source_rom": report["known_source_rom"],
        "species_data_pointer": entry["rom_pointer"],
        "species_data_sha256": sha256(table),
    }


def install_registry(data: bytes) -> tuple[bytes, dict]:
    before = verify_expanded(data)
    if any(
        entry["name"] == DIRECTORY_NAME
        for entry in before["directory"]
    ):
        raise ValueError(
            "species_registry is already installed"
        )

    table, species_entry = extract_species_data(
        data,
        before,
    )
    blob, build = build_registry(table)
    output, install = install_blob(
        data,
        DIRECTORY_NAME,
        blob,
        count=build["defined_count"],
        stride=2,
        alignment=4,
    )

    after = verify_expanded(output)
    entry = next(
        item for item in after["directory"]
        if item["name"] == DIRECTORY_NAME
    )
    installed = output[
        entry["rom_offset"]:
        entry["rom_offset"] + entry["size"]
    ]
    parsed = parse_registry(installed)
    if sha256(installed) != build["sha256"]:
        raise AssertionError(
            "installed species_registry hash mismatch"
        )

    return output, {
        "result": "pass",
        "source_rom": before["known_source_rom"],
        "species_data_pointer": species_entry["rom_pointer"],
        "registry": {
            **entry,
            "defined_count": parsed["defined_count"],
            "first_defined": parsed["defined_ids"][0],
            "last_defined": parsed["defined_ids"][-1],
            "bitset_size": parsed["bitset_size"],
            "ids_offset": parsed["ids_offset"],
            "sha256": build["sha256"],
            "directory_count": install["directory_count"],
        },
        "runtime_status": (
            "registry materialized; TV/daycare and other dense "
            "iteration call sites still need runtime redirection"
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Build/install the SAPPHIRE defined-species bitset "
            "and dense u16 ID vector"
        )
    )
    sub = ap.add_subparsers(
        dest="command",
        required=True,
    )

    bp = sub.add_parser("build")
    bp.add_argument("rom", type=Path)
    bp.add_argument("output", type=Path)
    bp.add_argument("--force", action="store_true")

    ip = sub.add_parser("install")
    ip.add_argument("rom", type=Path)
    ip.add_argument("output", type=Path)
    ip.add_argument("--force", action="store_true")

    args = ap.parse_args()
    if args.rom.resolve() == args.output.resolve():
        raise ValueError(
            "output must not overwrite input"
        )
    if args.output.exists() and not args.force:
        raise FileExistsError(
            f"refusing to overwrite existing output: {args.output}"
        )

    data = args.rom.read_bytes()
    if args.command == "build":
        output, report = build_from_rom(data)
    else:
        output, report = install_registry(data)

    args.output.write_bytes(output)
    report["output"] = str(args.output)
    print(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
