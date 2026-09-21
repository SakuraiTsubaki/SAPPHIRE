#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from pathlib import Path

SAVE_SIZE = 128 * 1024
SECTOR_SIZE = 0x1000
SAVE_SIGNATURE = 0x08012025

SAVE_CHUNK_SIZES = [
    0x890,
    0xF80, 0xF80, 0xF80, 0xC40,
    0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0xF80, 0x7D0,
]

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


def digest(data: bytes, algorithm: str) -> str:
    return hashlib.new(algorithm, data).hexdigest()


def calculate_save_checksum(data: bytes, size: int) -> int:
    total = 0
    for offset in range(0, size, 4):
        total = (total + struct.unpack_from("<I", data, offset)[0]) & 0xFFFFFFFF
    return ((total >> 16) + total) & 0xFFFF


def longest_ff_run(data: bytes) -> tuple[int, int] | None:
    runs = re.finditer(b"\xFF{65536,}", data)
    best = max(runs, key=lambda m: m.end() - m.start(), default=None)
    return None if best is None else (best.start(), best.end())


def audit_rom(path: Path) -> dict:
    data = path.read_bytes()
    sha1 = digest(data, "sha1")
    known = KNOWN_ROMS.get(sha1)
    if known is None:
        raise ValueError(f"{path}: unknown Sapphire SHA-1 {sha1}")
    label, expected_size = known
    if len(data) != expected_size:
        raise ValueError(f"{path}: size {len(data)} != expected {expected_size}")

    game_code = data[0xAC:0xB0].decode("ascii", "strict")
    revision = data[0xBC]
    last_non_ff = len(data.rstrip(b"\xFF")) - 1
    ff_run = longest_ff_run(data)

    return {
        "file": path.name,
        "known_rom": label,
        "size": len(data),
        "sha1": sha1,
        "sha256": digest(data, "sha256"),
        "header": {
            "title": data[0xA0:0xAC].decode("ascii", "replace"),
            "game_code": game_code,
            "maker_code": data[0xB0:0xB2].decode("ascii", "replace"),
            "revision": revision,
            "header_checksum": data[0xBD],
        },
        "last_non_ff_offset": last_non_ff,
        "longest_ff_run": (
            None
            if ff_run is None
            else {
                "start": ff_run[0],
                "end_exclusive": ff_run[1],
                "size": ff_run[1] - ff_run[0],
            }
        ),
    }


def parse_save_sectors(data: bytes) -> list[dict]:
    sectors = []
    for physical in range(32):
        sector = data[physical * SECTOR_SIZE:(physical + 1) * SECTOR_SIZE]
        section_id, checksum, signature, counter = struct.unpack_from(
            "<HHII", sector, 0xFF4
        )
        checksum_valid = None
        if physical < 28 and signature == SAVE_SIGNATURE and 0 <= section_id < 14:
            checksum_valid = (
                calculate_save_checksum(sector, SAVE_CHUNK_SIZES[section_id]) == checksum
            )
        sectors.append(
            {
                "physical_sector": physical,
                "section_id": section_id,
                "checksum": checksum,
                "signature": signature,
                "counter": counter,
                "checksum_valid": checksum_valid,
                "all_zero": not any(sector),
                "all_ff": all(b == 0xFF for b in sector),
            }
        )
    return sectors


def audit_save(path: Path) -> dict:
    data = path.read_bytes()
    if len(data) != SAVE_SIZE:
        raise ValueError(f"{path}: expected 128 KiB save, got {len(data)} bytes")

    sectors = parse_save_sectors(data)
    recognized = [
        s for s in sectors[:28]
        if s["signature"] == SAVE_SIGNATURE and 0 <= s["section_id"] < 14
    ]
    valid = [s for s in recognized if s["checksum_valid"]]

    by_counter: dict[int, list[dict]] = {}
    for sector in valid:
        by_counter.setdefault(sector["counter"], []).append(sector)

    counter_groups = []
    for counter, group in sorted(by_counter.items()):
        ids = sorted(s["section_id"] for s in group)
        counter_groups.append(
            {
                "counter": counter,
                "logical_ids": ids,
                "complete_0_13": ids == list(range(14)),
                "all_checksums_valid": all(s["checksum_valid"] for s in group),
            }
        )

    return {
        "file": path.name,
        "size": len(data),
        "sha1": digest(data, "sha1"),
        "sha256": digest(data, "sha256"),
        "main_save": {
            "physical_sectors": [0, 27],
            "recognized_sector_count": len(recognized),
            "valid_sector_count": len(valid),
            "all_legacy_checksums_valid": (
                len(recognized) == 28
                and len(valid) == 28
                and all(s["checksum_valid"] for s in recognized)
            ),
            "counter_groups": counter_groups,
            "latest_counter": max(by_counter) if by_counter else None,
            "two_complete_rotating_slots": (
                len(counter_groups) == 2
                and all(g["complete_0_13"] for g in counter_groups)
            ),
        },
        "special_sectors": {
            "hall_of_fame": [
                {"sector": 28, "all_zero": sectors[28]["all_zero"], "all_ff": sectors[28]["all_ff"]},
                {"sector": 29, "all_zero": sectors[29]["all_zero"], "all_ff": sectors[29]["all_ff"]},
            ],
            "retail_unused": [
                {"sector": 30, "all_zero": sectors[30]["all_zero"], "all_ff": sectors[30]["all_ff"]},
                {"sector": 31, "all_zero": sectors[31]["all_zero"], "all_ff": sectors[31]["all_ff"]},
            ],
        },
    }


def audit_pair(rom_path: Path, save_path: Path) -> dict:
    rom = audit_rom(rom_path)
    save = audit_save(save_path)
    return {
        "rom": rom,
        "save": save,
        "pairing": {
            "basename_matches": rom_path.stem == save_path.stem,
            "game_code": rom["header"]["game_code"],
            "revision": rom["header"]["revision"],
        },
    }


def audit_directory(root: Path) -> dict:
    roms = {p.stem: p for p in root.glob("*.gba")}
    saves = {p.stem: p for p in root.glob("*.sav")}
    common = sorted(set(roms) & set(saves))

    pairs = [audit_pair(roms[stem], saves[stem]) for stem in common]
    return {
        "schema_version": 2,
        "root": str(root),
        "rom_count": len(roms),
        "save_count": len(saves),
        "paired_count": len(pairs),
        "all_known_roms": all(p["rom"]["known_rom"] for p in pairs),
        "all_saves_128k": all(p["save"]["size"] == SAVE_SIZE for p in pairs),
        "all_main_saves_have_two_complete_slots": all(
            p["save"]["main_save"]["two_complete_rotating_slots"] for p in pairs
        ),
        "all_legacy_sector_checksums_valid": all(
            p["save"]["main_save"]["all_legacy_checksums_valid"] for p in pairs
        ),
        "validated_main_sector_checksums": sum(
            p["save"]["main_save"]["valid_sector_count"] for p in pairs
        ),
        "all_sector_30_31_blank": all(
            all(x["all_zero"] or x["all_ff"] for x in p["save"]["special_sectors"]["retail_unused"])
            for p in pairs
        ),
        "pairs": pairs,
        "source_evidence": {
            "repository": "pret/pokeruby",
            "ref": "63a8cbf0016b351a4e68f7036fa0b77e23d2f2c1",
            "file": "src/save.c",
            "main_save_sectors": "0-27 (two rotating 14-sector slots)",
            "hall_of_fame_sectors": "28-29",
            "unused_sector_symbol": "sUnusedFlashSectors[] = { 30, 31 }",
            "normal_save_behavior": "SAVE_NORMAL writes only sectors 0-27",
            "overwrite_behavior": "SAVE_OVERWRITE_DIFFERENT_FILE erases sectors 28-31",
        },
        "conclusions": {
            "rom_common_expansion_base": 16 * 1024 * 1024,
            "rom_expanded_size": 32 * 1024 * 1024,
            "save_main_sectors": "0-27",
            "save_hall_of_fame_sectors": "28-29; preserve retail behavior",
            "save_extension_sectors": "30-31; source-labeled unused in retail and assigned to expanded-profile A/B copies",
            "save_extension_runtime_status": "offline layout validated; expanded ROM read/write hooks still required",
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audit paired Sapphire ROM and save files for expansion work"
    )
    ap.add_argument("root", type=Path)
    ap.add_argument("--output", type=Path)
    args = ap.parse_args()

    report = audit_directory(args.root)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
