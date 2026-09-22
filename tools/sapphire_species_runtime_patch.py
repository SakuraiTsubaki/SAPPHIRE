#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
from pathlib import Path

from sapphire_rom_expansion import (
    install_blob,
    mark_prefix_patched,
    offset_to_ptr,
    verify_expanded,
)
from sapphire_species_table import (
    DIRECTORY_NAME as SPECIES_DATA_NAME,
    LEGACY_RECORD_COUNT,
    find_legacy_table,
)

COMPAT_DIRECTORY_NAME = "species_compat"
COMPAT_CAPACITY = 4096
COMPAT_STRIDE = 28
EXPECTED_GBASESTATS_POINTER_REFS = 45
COMPAT_SHA256 = (
    "63f0b7a2a4575d4f21b8dbb7c76041751225d7a869d7e88114060e629fa60dff"
)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_compat_table(source: bytes) -> tuple[bytes, dict]:
    legacy_offset, legacy = find_legacy_table(source)
    table = legacy + bytes(COMPAT_CAPACITY * COMPAT_STRIDE - len(legacy))

    if len(table) != COMPAT_CAPACITY * COMPAT_STRIDE:
        raise AssertionError("compat table size mismatch")
    if sha256(table) != COMPAT_SHA256:
        raise AssertionError("compat table hash mismatch")

    return table, {
        "legacy_table_offset": legacy_offset,
        "legacy_table_pointer": f"0x{offset_to_ptr(legacy_offset):08X}",
        "capacity": COMPAT_CAPACITY,
        "stride": COMPAT_STRIDE,
        "size": len(table),
        "sha256": sha256(table),
        "legacy_records_preserved": LEGACY_RECORD_COUNT,
        "reserved_records_zeroed": [LEGACY_RECORD_COUNT, COMPAT_CAPACITY - 1],
    }


def find_pointer_refs(prefix: bytes, pointer: int) -> list[int]:
    needle = struct.pack("<I", pointer)
    out = []
    pos = 0
    while True:
        hit = prefix.find(needle, pos)
        if hit < 0:
            return out
        out.append(hit)
        pos = hit + 1


def patch_runtime(data: bytes) -> tuple[bytes, dict]:
    before = verify_expanded(data)
    if before["prefix_patched"]:
        raise ValueError("runtime prefix is already patched")
    if any(entry["name"] == COMPAT_DIRECTORY_NAME for entry in before["directory"]):
        raise ValueError("species_compat is already installed")
    if not any(entry["name"] == SPECIES_DATA_NAME for entry in before["directory"]):
        raise ValueError(
            "species_data must be installed before species runtime compatibility patch"
        )

    source_prefix = data[:before["input_size"]]
    compat, build = build_compat_table(source_prefix)
    with_compat, install = install_blob(
        data,
        COMPAT_DIRECTORY_NAME,
        compat,
        count=COMPAT_CAPACITY,
        stride=COMPAT_STRIDE,
        alignment=4,
    )

    compat_entry = verify_expanded(with_compat)["directory"][-1]
    old_ptr = offset_to_ptr(build["legacy_table_offset"])
    new_ptr = offset_to_ptr(compat_entry["rom_offset"])
    refs = find_pointer_refs(source_prefix, old_ptr)
    if len(refs) != EXPECTED_GBASESTATS_POINTER_REFS:
        raise ValueError(
            "gBaseStats pointer reference count changed: "
            f"{len(refs)} != {EXPECTED_GBASESTATS_POINTER_REFS}"
        )

    output = bytearray(with_compat)
    old_raw = struct.pack("<I", old_ptr)
    new_raw = struct.pack("<I", new_ptr)
    for offset in refs:
        if output[offset:offset + 4] != old_raw:
            raise AssertionError(f"gBaseStats literal changed at 0x{offset:X}")
        output[offset:offset + 4] = new_raw

    patched = mark_prefix_patched(bytes(output))
    final = verify_expanded(patched)

    old_refs_after = find_pointer_refs(
        patched[:before["input_size"]],
        old_ptr,
    )
    new_refs_after = find_pointer_refs(
        patched[:before["input_size"]],
        new_ptr,
    )
    if old_refs_after:
        raise AssertionError("legacy gBaseStats pointer literals remain after patch")
    if len(new_refs_after) != EXPECTED_GBASESTATS_POINTER_REFS:
        raise AssertionError("new species_compat pointer literal count mismatch")

    return patched, {
        "result": "pass",
        "source_rom": before["known_source_rom"],
        "species_compat": {
            **build,
            "rom_offset": compat_entry["rom_offset"],
            "rom_pointer": compat_entry["rom_pointer"],
            "directory_count": install["directory_count"],
        },
        "gBaseStats_redirection": {
            "old_pointer": f"0x{old_ptr:08X}",
            "new_pointer": f"0x{new_ptr:08X}",
            "patched_literal_count": len(refs),
            "literal_offsets": [f"0x{offset:X}" for offset in refs],
        },
        "prefix_patched": final["prefix_patched"],
        "source_prefix_preserved": final["source_prefix_preserved"],
        "working_prefix_sha256": final["working_prefix_sha256"],
        "runtime_limitations": [
            "NUM_SPECIES and sanitizer bounds are not patched yet",
            "legacy BaseStats consumers still see 8-bit type/ability fields through species_compat",
            "wide ExpandedSpeciesV1 accessors remain pending",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Install species_compat and redirect direct legacy gBaseStats "
            "pointer literals"
        )
    )
    ap.add_argument("rom", type=Path, help="expanded ROM with species_data installed")
    ap.add_argument("output", type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.rom.resolve() == args.output.resolve():
        raise ValueError("output must not overwrite input")
    if args.output.exists() and not args.force:
        raise FileExistsError(f"refusing to overwrite existing output: {args.output}")

    output, report = patch_runtime(args.rom.read_bytes())
    args.output.write_bytes(output)
    report["output"] = str(args.output)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
