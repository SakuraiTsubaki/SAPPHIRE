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
RUNTIME_DIRECTORY_NAME = "species_runtime"
COMPAT_CAPACITY = 4096
COMPAT_STRIDE = 28
EXPECTED_GBASESTATS_POINTER_REFS = 45
COMPAT_SHA256 = (
    "63f0b7a2a4575d4f21b8dbb7c76041751225d7a869d7e88114060e629fa60dff"
)

SANITIZE_SPECIES_SIGNATURE = bytes.fromhex(
    "00 b5 00 04 01 0c 03 48 81 42 00 d9 00 21 08 1c "
    "02 bc 08 47 9b 01 00 00"
)

# Thumb-1 helper template. It returns the original u16 species only when
# species < 4096 and ExpandedSpeciesV1.flags bit 0 (defined) is set.
# r1-r3 are caller-saved under the ABI, so the helper needs no stack frame.
RUNTIME_HELPER_TEMPLATE = bytes.fromhex(
    "01 04 08 0c 01 21 09 03 88 42 0a d2 28 22 03 00 "
    "53 43 05 49 5b 18 25 33 19 78 01 22 11 42 00 d0 "
    "70 47 00 20 70 47 c0 46 00 00 00 00"
)
RUNTIME_HELPER_DATA_PTR_OFFSET = len(RUNTIME_HELPER_TEMPLATE) - 4


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


def build_runtime_helper(species_data_pointer: int) -> bytes:
    helper = bytearray(RUNTIME_HELPER_TEMPLATE)
    struct.pack_into(
        "<I",
        helper,
        RUNTIME_HELPER_DATA_PTR_OFFSET,
        species_data_pointer,
    )
    return bytes(helper)


def find_unique(data: bytes, needle: bytes, label: str) -> int:
    first = data.find(needle)
    if first < 0:
        raise ValueError(f"{label}: signature not found")
    second = data.find(needle, first + 1)
    if second >= 0:
        raise ValueError(
            f"{label}: signature is not unique: 0x{first:X}, 0x{second:X}"
        )
    return first


def build_thumb_absolute_tailcall(target_pointer: int) -> bytes:
    # ldr r1, [pc, #0]; bx r1; .word target|1
    return struct.pack("<HHI", 0x4900, 0x4708, target_pointer | 1)


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
    if any(entry["name"] == RUNTIME_DIRECTORY_NAME for entry in before["directory"]):
        raise ValueError("species_runtime is already installed")

    species_data_entries = [
        entry for entry in before["directory"]
        if entry["name"] == SPECIES_DATA_NAME
    ]
    if len(species_data_entries) != 1:
        raise ValueError(
            "exactly one species_data entry must be installed before runtime patching"
        )
    species_data_entry = species_data_entries[0]
    if (
        species_data_entry["count"] != 4096
        or species_data_entry["stride"] != 40
        or species_data_entry["size"] != 4096 * 40
    ):
        raise ValueError("species_data does not match ExpandedSpeciesV1 geometry")

    source_prefix = data[:before["input_size"]]
    sanitize_offset = find_unique(
        source_prefix,
        SANITIZE_SPECIES_SIGNATURE,
        "SanitizeSpecies",
    )

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

    helper_blob = build_runtime_helper(
        int(species_data_entry["rom_pointer"], 16)
    )
    with_helper, helper_install = install_blob(
        with_compat,
        RUNTIME_DIRECTORY_NAME,
        helper_blob,
        count=1,
        stride=len(helper_blob),
        alignment=4,
    )
    helper_entry = verify_expanded(with_helper)["directory"][-1]

    old_ptr = offset_to_ptr(build["legacy_table_offset"])
    new_ptr = offset_to_ptr(compat_entry["rom_offset"])
    refs = find_pointer_refs(source_prefix, old_ptr)
    if len(refs) != EXPECTED_GBASESTATS_POINTER_REFS:
        raise ValueError(
            "gBaseStats pointer reference count changed: "
            f"{len(refs)} != {EXPECTED_GBASESTATS_POINTER_REFS}"
        )

    output = bytearray(with_helper)
    old_raw = struct.pack("<I", old_ptr)
    new_raw = struct.pack("<I", new_ptr)
    for offset in refs:
        if output[offset:offset + 4] != old_raw:
            raise AssertionError(f"gBaseStats literal changed at 0x{offset:X}")
        output[offset:offset + 4] = new_raw

    helper_pointer = int(helper_entry["rom_pointer"], 16)
    trampoline = build_thumb_absolute_tailcall(helper_pointer)
    if output[
        sanitize_offset:sanitize_offset + len(SANITIZE_SPECIES_SIGNATURE)
    ] != SANITIZE_SPECIES_SIGNATURE:
        raise AssertionError("SanitizeSpecies signature changed before patch")
    output[sanitize_offset:sanitize_offset + len(trampoline)] = trampoline

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
    if SANITIZE_SPECIES_SIGNATURE in patched[:before["input_size"]]:
        raise AssertionError("legacy SanitizeSpecies signature remains after patch")
    if patched[
        sanitize_offset:sanitize_offset + len(trampoline)
    ] != trampoline:
        raise AssertionError("SanitizeSpecies trampoline did not round-trip")

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
        "species_runtime": {
            "directory_name": RUNTIME_DIRECTORY_NAME,
            "rom_offset": helper_entry["rom_offset"],
            "rom_pointer": helper_entry["rom_pointer"],
            "size": helper_entry["size"],
            "sha256": sha256(helper_blob),
            "species_data_pointer": species_data_entry["rom_pointer"],
            "sanitizer_offset": f"0x{sanitize_offset:X}",
            "sanitizer_pointer": f"0x{offset_to_ptr(sanitize_offset):08X}",
            "trampoline_size": len(trampoline),
            "defined_flag_offset": 37,
            "capacity": 4096,
            "directory_count": helper_install["directory_count"],
        },
        "prefix_patched": final["prefix_patched"],
        "source_prefix_preserved": final["source_prefix_preserved"],
        "working_prefix_sha256": final["working_prefix_sha256"],
        "runtime_limitations": [
            "SanitizeSpecies is now defined-flag driven, but other NUM_SPECIES/SPECIES_EGG bounds are still pending",
            "dense 1..NUM_SPECIES selection/iteration paths are still pending",
            "legacy BaseStats consumers still see 8-bit type/ability fields through species_compat",
            "wide ExpandedSpeciesV1 accessors remain pending",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Install species_compat/species_runtime, redirect direct legacy "
            "gBaseStats pointers, and patch SanitizeSpecies"
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
