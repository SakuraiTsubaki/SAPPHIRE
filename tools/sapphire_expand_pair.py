#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from sapphire_rom_expansion import expand as expand_rom
from sapphire_rom_expansion import verify_expanded
from sapphire_save_extension import extension_status
from sapphire_save_extension import initialize as initialize_save


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def expand_pair(
    rom: bytes,
    save: bytes,
) -> tuple[bytes, bytes, dict]:
    expanded_rom, rom_report = expand_rom(rom)
    expanded_save, save_report = initialize_save(rom, save)

    rom_verify = verify_expanded(expanded_rom)
    save_ext = extension_status(expanded_save)
    if save_ext["result"] != "pass":
        raise ValueError(f"expanded save failed extension verification: {save_ext}")

    native = rom_report["native"]
    save_rom = save_report["rom"]
    if (
        native["sha1"] != save_rom["sha1"]
        or native["game_code"] != save_rom["game_code"]
        or native["revision"] != save_rom["revision"]
    ):
        raise AssertionError("ROM and save expansion identity checks disagreed")

    return expanded_rom, expanded_save, {
        "result": "pass",
        "source": {
            "rom_sha1": native["sha1"],
            "game_code": native["game_code"],
            "revision": native["revision"],
            "rom_size": native["size"],
            "save_sha1": sha1(save),
            "save_size": len(save),
        },
        "expanded_rom": {
            "sha1": sha1(expanded_rom),
            "size": len(expanded_rom),
            "control_magic": rom_verify["magic"],
            "payload_base_offset": rom_verify["payload_base_offset"],
            "payload_capacity_bytes": rom_verify["payload_capacity_bytes"],
            "source_prefix_preserved": rom_verify["source_prefix_preserved"],
        },
        "expanded_save": {
            "sha1": sha1(expanded_save),
            "size": len(expanded_save),
            "sectors_0_29_preserved_byte_for_byte":
                save_report["sectors_0_29_preserved_byte_for_byte"],
            "latest_extension": save_ext["latest"],
            "copies": save_ext["copies"],
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Expand a validated Sapphire ROM and its classic 128 KiB save as one pair"
    )
    ap.add_argument("rom", type=Path)
    ap.add_argument("save", type=Path)
    ap.add_argument("output_rom", type=Path)
    ap.add_argument("output_save", type=Path)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    input_paths = {args.rom.resolve(), args.save.resolve()}
    output_paths = {args.output_rom.resolve(), args.output_save.resolve()}
    if input_paths & output_paths:
        raise ValueError("output paths must not overwrite the source ROM or save")
    if args.output_rom.resolve() == args.output_save.resolve():
        raise ValueError("ROM and save outputs must be different files")
    if not args.force:
        existing = [p for p in (args.output_rom, args.output_save) if p.exists()]
        if existing:
            raise FileExistsError(
                "refusing to overwrite existing output(s): "
                + ", ".join(str(p) for p in existing)
            )

    rom = args.rom.read_bytes()
    save = args.save.read_bytes()
    expanded_rom, expanded_save, report = expand_pair(rom, save)

    # Both transformations complete before either output is written.
    args.output_rom.write_bytes(expanded_rom)
    args.output_save.write_bytes(expanded_save)
    report["outputs"] = {
        "rom": str(args.output_rom),
        "save": str(args.output_save),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
