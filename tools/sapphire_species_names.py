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

CANONICAL_DIRECTORY_NAME = "species_names"
COMPAT_DIRECTORY_NAME = "species_names_cp"
SPECIES_CAPACITY = 4096
LEGACY_SPECIES_COUNT = 412
CANONICAL_STRIDE = 16
COMPAT_ALLOCATION_STRIDE = 11
COMPAT_ALLOCATION_SIZE = SPECIES_CAPACITY * COMPAT_ALLOCATION_STRIDE
EOS = 0xFF

PROFILES = {
    "jp": {
        "stride": 6,
        "signature": "acacacacacff6c5c889168ff6c5c885f53ff6c5c889665ff",
        "legacy_sha256": "ad86ecaa8dc648ca84ba43ba5f760c4529741242dc5f5745f655c307d1660c3c",
        "pointer_refs": 67,
    },
    "en": {
        "stride": 11,
        "signature": "acacacacacacacacacacffbccfc6bcbbcdbbcfccff00c3d0d3cdbbcfccff000000d0bfc8cfcdbbcfccff0000",
        "legacy_sha256": "a1d6a18af896762205e870cbc17f5e046982d17820f305ad5e8dd9f8c24f4c3e",
        "pointer_refs": 66,
    },
    "de": {
        "stride": 11,
        "signature": "acacacacacacacacacacffbcc3cdbbcdbbc7ff000000bcc3cdbbc5c8c9cdcaff00bcc3cdbbc0c6c9ccff0000",
        "legacy_sha256": "571910b14b247a37082b3f6da976c8cf3ea1740999adb79bd969216d965b4e22",
        "pointer_refs": 67,
    },
    "fr": {
        "stride": 11,
        "signature": "acacacacacacacacacacffbccfc6bcc3d4bbccccbfffc2bfccbcc3d4bbccccbfffc0c6c9ccc3d4bbccccbfff",
        "legacy_sha256": "85fe703a81ed6a63209879044bdbb69fb6dc9de0a6e30f71fdcf5a70672ea67c",
        "pointer_refs": 67,
    },
    "it": {
        "stride": 11,
        "signature": "acacacacacacacacacacffbccfc6bcbbcdbbcfccff00c3d0d3cdbbcfccff000000d0bfc8cfcdbbcfccff0000",
        "legacy_sha256": "a1d6a18af896762205e870cbc17f5e046982d17820f305ad5e8dd9f8c24f4c3e",
        "pointer_refs": 67,
    },
    "es": {
        "stride": 11,
        "signature": "5cac5dff00000000000000bccfc6bcbbcdbbcfccff00c3d0d3cdbbcfccff000000d0bfc8cfcdbbcfccff0000",
        "legacy_sha256": "f7cea4bfb94292c813fff77840a2701f51661888a5687f973b18baf13958d061",
        "pointer_refs": 67,
    },
}

SOURCE_PROFILE = {
    "3233342c2f3087e6ffe6c1791cd5867db07df842": "jp",
    "3ccbbd45f8553c36463f13b938e833f652b793e4": "en",
    "4722efb8cd45772ca32555b98fd3b9719f8e60a9": "en",
    "89b45fb172e6b55d51fc0e61989775187f6fe63c": "en",
    "5a087835009d552d4c5c1f96be3be3206e378153": "de",
    "7e6e034f9cdca6d2c4a270fdb50a94def5883d17": "de",
    "c269b5692b2d0e5800ba1ddf117fda95ac648634": "fr",
    "860e93f5ea44f4278132f6c1ee5650d07b852fd8": "fr",
    "f729dd571fb2c09e72c5c1d68fe0a21e72713d34": "it",
    "73edf67b9b82ff12795622dca412733755d2c0fe": "it",
    "3a6489189e581c4b29914071b79207883b8c16d8": "es",
    "0fe9ad1e602e2fafa090aee25e43d6980625173c": "es",
}


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


def profile_for_source(source_sha1: str) -> tuple[str, dict]:
    key = SOURCE_PROFILE.get(source_sha1)
    if key is None:
        raise ValueError(f"no species-name profile for source SHA-1 {source_sha1}")
    return key, PROFILES[key]


def find_legacy_names(
    source_prefix: bytes,
    source_sha1: str,
) -> tuple[int, bytes, str, dict]:
    profile_key, profile = profile_for_source(source_sha1)
    signature = bytes.fromhex(profile["signature"])
    start = find_unique(source_prefix, signature, "gSpeciesNames")
    size = LEGACY_SPECIES_COUNT * profile["stride"]
    table = source_prefix[start:start + size]
    if len(table) != size:
        raise ValueError("gSpeciesNames table is truncated")
    digest = sha256(table)
    if digest != profile["legacy_sha256"]:
        raise ValueError(
            f"unexpected {profile_key} gSpeciesNames SHA-256: {digest}"
        )

    for species_id in range(LEGACY_SPECIES_COUNT):
        off = species_id * profile["stride"]
        record = table[off:off + profile["stride"]]
        if EOS not in record:
            raise ValueError(
                f"legacy species name {species_id} has no EOS within stride "
                f"{profile['stride']}"
            )

    return start, table, profile_key, profile


def canonical_record(record: bytes) -> bytes:
    eos = record.index(EOS)
    encoded = record[:eos + 1]
    if len(encoded) > CANONICAL_STRIDE:
        raise ValueError("legacy species name exceeds canonical 16-byte record")
    return encoded.ljust(CANONICAL_STRIDE, b"\0")


def build_name_tables(
    source_prefix: bytes,
    source_sha1: str,
) -> tuple[bytes, bytes, dict]:
    legacy_offset, legacy, profile_key, profile = find_legacy_names(
        source_prefix,
        source_sha1,
    )
    stride = profile["stride"]

    canonical_records = []
    for species_id in range(LEGACY_SPECIES_COUNT):
        off = species_id * stride
        canonical_records.append(
            canonical_record(legacy[off:off + stride])
        )

    unknown_canonical = canonical_records[0]
    canonical_records.extend(
        [unknown_canonical] * (SPECIES_CAPACITY - LEGACY_SPECIES_COUNT)
    )
    canonical = b"".join(canonical_records)

    legacy_record0 = legacy[:stride]
    compat_active = (
        legacy
        + legacy_record0 * (SPECIES_CAPACITY - LEGACY_SPECIES_COUNT)
    )
    if len(compat_active) != SPECIES_CAPACITY * stride:
        raise AssertionError("species_names_cp active table size mismatch")
    if len(compat_active) > COMPAT_ALLOCATION_SIZE:
        raise AssertionError("species_names_cp exceeds fixed common allocation")
    compat = compat_active + bytes([0xFF]) * (
        COMPAT_ALLOCATION_SIZE - len(compat_active)
    )

    if len(canonical) != SPECIES_CAPACITY * CANONICAL_STRIDE:
        raise AssertionError("canonical species_names table size mismatch")
    if len(compat) != COMPAT_ALLOCATION_SIZE:
        raise AssertionError("species_names_cp allocation size mismatch")

    return canonical, compat, {
        "profile": profile_key,
        "legacy_table_offset": legacy_offset,
        "legacy_table_pointer": f"0x{offset_to_ptr(legacy_offset):08X}",
        "legacy_stride": stride,
        "legacy_count": LEGACY_SPECIES_COUNT,
        "legacy_size": len(legacy),
        "legacy_sha256": sha256(legacy),
        "canonical_stride": CANONICAL_STRIDE,
        "canonical_capacity": SPECIES_CAPACITY,
        "canonical_size": len(canonical),
        "canonical_sha256": sha256(canonical),
        "compat_active_stride": stride,
        "compat_allocation_stride": COMPAT_ALLOCATION_STRIDE,
        "compat_allocation_size": len(compat),
        "compat_sha256": sha256(compat),
        "future_default": "copy of regional SPECIES_NONE name",
        "expected_pointer_refs": profile["pointer_refs"],
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


def install_and_redirect(data: bytes) -> tuple[bytes, dict]:
    before = verify_expanded(data)
    existing = {entry["name"] for entry in before["directory"]}
    for name in (CANONICAL_DIRECTORY_NAME, COMPAT_DIRECTORY_NAME):
        if name in existing:
            raise ValueError(f"{name} is already installed")

    source_prefix = data[:before["input_size"]]
    canonical, compat, build = build_name_tables(
        source_prefix,
        before["source_sha1"],
    )

    with_canonical, canonical_install = install_blob(
        data,
        CANONICAL_DIRECTORY_NAME,
        canonical,
        count=SPECIES_CAPACITY,
        stride=CANONICAL_STRIDE,
        alignment=4,
    )
    with_compat, compat_install = install_blob(
        with_canonical,
        COMPAT_DIRECTORY_NAME,
        compat,
        count=SPECIES_CAPACITY,
        stride=build["compat_active_stride"],
        alignment=4,
    )

    after_install = verify_expanded(with_compat)
    canonical_entry = next(
        entry for entry in after_install["directory"]
        if entry["name"] == CANONICAL_DIRECTORY_NAME
    )
    compat_entry = next(
        entry for entry in after_install["directory"]
        if entry["name"] == COMPAT_DIRECTORY_NAME
    )

    old_ptr = offset_to_ptr(build["legacy_table_offset"])
    new_ptr = int(compat_entry["rom_pointer"], 16)
    refs = find_pointer_refs(source_prefix, old_ptr)
    if len(refs) != build["expected_pointer_refs"]:
        raise ValueError(
            "gSpeciesNames pointer reference count changed: "
            f"{len(refs)} != {build['expected_pointer_refs']}"
        )

    output = bytearray(with_compat)
    old_raw = struct.pack("<I", old_ptr)
    new_raw = struct.pack("<I", new_ptr)
    for offset in refs:
        if output[offset:offset + 4] != old_raw:
            raise AssertionError(
                f"gSpeciesNames literal changed at 0x{offset:X}"
            )
        output[offset:offset + 4] = new_raw

    patched = mark_prefix_patched(bytes(output))
    final = verify_expanded(patched)
    if find_pointer_refs(patched[:before["input_size"]], old_ptr):
        raise AssertionError("legacy gSpeciesNames pointer literals remain")
    new_refs = find_pointer_refs(
        patched[:before["input_size"]],
        new_ptr,
    )
    if len(new_refs) != len(refs):
        raise AssertionError("species_names_cp pointer count mismatch")

    return patched, {
        "result": "pass",
        "source_rom": before["known_source_rom"],
        "build": build,
        "canonical": {
            **canonical_entry,
            "sha256": sha256(canonical),
            "directory_count": canonical_install["directory_count"],
        },
        "compatibility": {
            **compat_entry,
            "sha256": sha256(compat),
            "active_stride": build["compat_active_stride"],
            "fixed_allocation_size": COMPAT_ALLOCATION_SIZE,
            "directory_count": compat_install["directory_count"],
        },
        "gSpeciesNames_redirection": {
            "old_pointer": f"0x{old_ptr:08X}",
            "new_pointer": f"0x{new_ptr:08X}",
            "patched_literal_count": len(refs),
            "literal_offsets": [f"0x{x:X}" for x in refs],
        },
        "working_prefix_sha256": final["working_prefix_sha256"],
        "limitations": [
            "direct legacy consumers still use the regional 6/11-byte compatibility stride",
            "new species names are stored canonically in 16-byte records but direct legacy consumers still show the regional SPECIES_NONE placeholder until migrated",
            "GetSpeciesName canonical 16-byte accessor patch is pending",
        ],
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Install 4096-slot canonical/compat species-name tables and "
            "redirect direct gSpeciesNames pointer literals"
        )
    )
    ap.add_argument("rom", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    if args.rom.resolve() == args.output.resolve():
        raise ValueError("output must not overwrite input")
    if args.output.exists() and not args.force:
        raise FileExistsError(
            f"refusing to overwrite existing output: {args.output}"
        )

    output, report = install_and_redirect(args.rom.read_bytes())
    args.output.write_bytes(output)
    report["output"] = str(args.output)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
