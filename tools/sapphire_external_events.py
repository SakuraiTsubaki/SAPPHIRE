#!/usr/bin/env python3
"""Pokemon Sapphire external-event permanence patcher.

Makes externally distributed Ruby/Sapphire content locally reachable while
preserving the original in-game gates wherever a real item can satisfy them.

Eon Ticket policy:
- Do NOT bypass Lilycove Harbor or Southern Island checks.
- After the Hall of Fame, Norman grants the Eon Ticket in-game once.
- Granting/owning the ticket also sets FLAG_SYS_HAS_EON_TICKET, matching the
  original distribution script.

The patch is signature-based and supports the known Japanese, English,
German, French, Italian, and Spanish Sapphire revisions in KNOWN_ROMS.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

ROM_BASE = 0x08000000

KNOWN_ROMS = {
    "3233342c2f3087e6ffe6c1791cd5867db07df842": "JP AXPJ rev0",
    "3ccbbd45f8553c36463f13b938e833f652b793e4": "EN AXPE rev0",
    "4722efb8cd45772ca32555b98fd3b9719f8e60a9": "EN AXPE rev1",
    "89b45fb172e6b55d51fc0e61989775187f6fe63c": "EN AXPE rev2",
    "5a087835009d552d4c5c1f96be3be3206e378153": "DE AXPD rev0",
    "7e6e034f9cdca6d2c4a270fdb50a94def5883d17": "DE AXPD rev1",
    "c269b5692b2d0e5800ba1ddf117fda95ac648634": "FR AXPF rev0",
    "860e93f5ea44f4278132f6c1ee5650d07b852fd8": "FR AXPF rev1",
    "f729dd571fb2c09e72c5c1d68fe0a21e72713d34": "IT AXPI rev0",
    "73edf67b9b82ff12795622dca412733755d2c0fe": "IT AXPI rev1",
    "3a6489189e581c4b29914071b79207883b8c16d8": "ES AXPS rev0",
    "0fe9ad1e602e2fafa090aee25e43d6980625173c": "ES AXPS rev1",
}

ITEM_EON_TICKET = 0x0113
VAR_0x8000 = 0x8000
VAR_0x8001 = 0x8001
VAR_RESULT = 0x800D
FLAG_SYS_HAS_EON_TICKET = 0x0853

FERRY_PREFIX = bytes.fromhex("6A 5A 47 13 01 01 00 21 0D 80 01 00 06 01")

LATI_THEN_EON_RE = re.compile(
    rb"\x2B\xCE\x00\x06\x01....\x2B\x53\x08\x06\x00....",
    re.DOTALL,
)

NORMAN_POST_BATTLE_RE = re.compile(
    rb"\x04...."
    rb"\x21\x0D\x80\x01\x00\x06\x01...."
    rb"\x2B..\x06\x00...."
    rb"\x2B\x04\x08\x06\x01(?P<no_amount>....)"
    rb"\x0F\x00....\x09\x04\x6C\x02"
    rb"\x26\x0D\x80",
    re.DOTALL,
)

GIVE_ENIGMA_BERRY_RE = re.compile(
    rb"\x1A\x00\x80\xAF\x00"
    rb"\x1A\x01\x80\x01\x00"
    rb"\x09\x00"
    rb"\x21\x0D\x80\x00\x00"
    rb"\x06\x01(?P<bag_full>....)"
    rb"\x16\x2D\x40\x00\x00\x6C\x02",
    re.DOTALL,
)

EXDATA_LITERAL = struct.pack("<I", 0x084C)
IS_MYSTERY_EVENT_PROLOGUE = bytes.fromhex("00 B5 03 48")
IS_MYSTERY_EVENT_EPILOGUE = bytes.fromhex("02 BC 08 47")
RETURN_TRUE_THUMB = bytes.fromhex("01 20 70 47")

FREE_SPACE_SEARCH_START = 0x600000
FREE_SPACE_RESERVE = 0x100


@dataclass
class PatchPoint:
    name: str
    offset: int
    before: str
    after: str


@dataclass
class Analysis:
    sha1: str
    known_rom: str | None
    title: str
    game_code: str
    revision: int
    ferry_attendant: int
    ferry_boarding_target: int
    eon_flag_checks: list[int]
    mystery_event_enabled_function: int
    norman_post_battle: int
    norman_game_clear_target_pointer: int
    original_norman_game_clear_target: int
    common_bag_full_script: int
    ticket_grant_injection: int


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def find_all(data: bytes, needle: bytes) -> list[int]:
    out: list[int] = []
    pos = 0
    while True:
        pos = data.find(needle, pos)
        if pos < 0:
            return out
        out.append(pos)
        pos += 1


def require_unique(name: str, values: Iterable[int]) -> int:
    vals = list(values)
    if len(vals) != 1:
        raise ValueError(f"{name}: expected exactly 1 match, found {len(vals)}: {vals}")
    return vals[0]


def require_unique_match(name: str, regex: re.Pattern[bytes], data: bytes) -> re.Match[bytes]:
    matches = list(regex.finditer(data))
    if len(matches) != 1:
        raise ValueError(f"{name}: expected exactly 1 match, found {len(matches)}")
    return matches[0]


def ptr_to_offset(ptr: int, rom_size: int) -> int:
    if not (ROM_BASE <= ptr < ROM_BASE + rom_size):
        raise ValueError(f"ROM pointer out of range: 0x{ptr:08X}")
    return ptr - ROM_BASE


def offset_to_ptr(offset: int) -> int:
    return ROM_BASE + offset


def identify_mystery_event_enabled(data: bytes) -> int:
    candidates: list[int] = []
    for lit in find_all(data, EXDATA_LITERAL):
        start = lit - 16
        if start < 0:
            continue
        if data[start:start + 4] != IS_MYSTERY_EVENT_PROLOGUE:
            continue
        if data[lit - 4:lit] != IS_MYSTERY_EVENT_EPILOGUE:
            continue
        candidates.append(start)
    return require_unique("IsMysteryEventEnabled", candidates)


def find_free_ff_block(
    data: bytes,
    size: int = FREE_SPACE_RESERVE,
    start: int = FREE_SPACE_SEARCH_START,
    alignment: int = 4,
) -> int:
    marker = b"\xFF" * size
    pos = start
    while True:
        pos = data.find(marker, pos)
        if pos < 0:
            raise ValueError(f"no {size:#x}-byte 0xFF free-space block found")
        aligned = (pos + alignment - 1) & ~(alignment - 1)
        if aligned + size <= len(data) and data[aligned:aligned + size] == marker:
            return aligned
        pos += 1


def build_ticket_grant_script(
    injection_offset: int,
    original_no_amount_ptr: int,
    bag_full_ptr: int,
) -> bytes:
    out = bytearray()
    fixups: list[tuple[int, str]] = []

    def u16(value: int) -> bytes:
        return struct.pack("<H", value)

    def u32(value: int) -> bytes:
        return struct.pack("<I", value)

    out += b"\x47" + u16(ITEM_EON_TICKET) + u16(1)
    out += b"\x21" + u16(VAR_RESULT) + u16(1)
    out += b"\x06\x01"
    fixups.append((len(out), "already_owned"))
    out += b"\x00" * 4

    out += b"\x4A" + u16(ITEM_EON_TICKET) + u16(1)
    out += b"\x21" + u16(VAR_RESULT) + u16(1)
    out += b"\x06\x01"
    fixups.append((len(out), "already_owned"))
    out += b"\x00" * 4

    out += b"\x1A" + u16(VAR_0x8000) + u16(ITEM_EON_TICKET)
    out += b"\x1A" + u16(VAR_0x8001) + u16(1)
    out += b"\x09\x00"
    out += b"\x21" + u16(VAR_RESULT) + u16(0)
    out += b"\x06\x01" + u32(bag_full_ptr)
    out += b"\x29" + u16(FLAG_SYS_HAS_EON_TICKET)
    out += b"\x6C\x02"

    labels = {"already_owned": len(out)}
    out += b"\x29" + u16(FLAG_SYS_HAS_EON_TICKET)
    out += b"\x05" + u32(original_no_amount_ptr)

    for position, label in fixups:
        target = offset_to_ptr(injection_offset + labels[label])
        out[position:position + 4] = u32(target)

    return bytes(out)


def analyze(data: bytes, allow_unknown_sha1: bool = False) -> Analysis:
    if len(data) < 0xC0:
        raise ValueError("file is too small to be a GBA ROM")

    digest = sha1(data)
    known = KNOWN_ROMS.get(digest)
    if not known and not allow_unknown_sha1:
        raise ValueError(
            f"unknown Sapphire SHA-1 {digest}; "
            "use --allow-unknown-sha1 only after verifying the ROM"
        )

    game_code = data[0xAC:0xB0].decode("ascii", "replace")
    title = data[0xA0:0xAC].rstrip(b"\0").decode("ascii", "replace")
    revision = data[0xBC]
    if not game_code.startswith("AXP"):
        raise ValueError(f"not a Pokemon Sapphire game code: {game_code!r}")

    ferry = require_unique(
        "Lilycove Eon Ticket ferry attendant",
        find_all(data, FERRY_PREFIX),
    )
    target_ptr = struct.unpack_from("<I", data, ferry + 14)[0]
    target = ptr_to_offset(target_ptr, len(data))

    if data[target:target + 5] != bytes.fromhex("2B 04 08 06 00"):
        raise ValueError(
            f"unexpected ferry boarding routine at 0x{target:X}; "
            "story-clear guard signature missing"
        )

    eon_checks = [m.start() + 9 for m in LATI_THEN_EON_RE.finditer(data)]
    if len(eon_checks) != 2:
        raise ValueError(
            "Eon Ticket system-flag checks: expected Harbor + Southern Island "
            f"(2), found {len(eon_checks)}"
        )

    mystery = identify_mystery_event_enabled(data)

    norman = require_unique_match("Norman post-game script", NORMAN_POST_BATTLE_RE, data)
    no_amount_ptr_pos = norman.start("no_amount")
    no_amount_ptr = struct.unpack("<I", norman.group("no_amount"))[0]
    ptr_to_offset(no_amount_ptr, len(data))

    enigma = require_unique_match("Enigma Berry gift script", GIVE_ENIGMA_BERRY_RE, data)
    bag_full_ptr = struct.unpack("<I", enigma.group("bag_full"))[0]
    ptr_to_offset(bag_full_ptr, len(data))

    injection = find_free_ff_block(data)

    return Analysis(
        sha1=digest,
        known_rom=known,
        title=title,
        game_code=game_code,
        revision=revision,
        ferry_attendant=ferry,
        ferry_boarding_target=target,
        eon_flag_checks=eon_checks,
        mystery_event_enabled_function=mystery,
        norman_post_battle=norman.start(),
        norman_game_clear_target_pointer=no_amount_ptr_pos,
        original_norman_game_clear_target=ptr_to_offset(no_amount_ptr, len(data)),
        common_bag_full_script=ptr_to_offset(bag_full_ptr, len(data)),
        ticket_grant_injection=injection,
    )


def patch(
    data: bytes,
    allow_unknown_sha1: bool = False,
) -> tuple[bytes, Analysis, list[PatchPoint]]:
    info = analyze(data, allow_unknown_sha1=allow_unknown_sha1)
    rom = bytearray(data)
    changes: list[PatchPoint] = []

    off = info.mystery_event_enabled_function
    before = bytes(rom[off:off + 4])
    rom[off:off + 4] = RETURN_TRUE_THUMB
    changes.append(PatchPoint(
        "Mystery Event menu always enabled",
        off,
        before.hex(),
        RETURN_TRUE_THUMB.hex(),
    ))

    original_no_amount_ptr = offset_to_ptr(info.original_norman_game_clear_target)
    bag_full_ptr = offset_to_ptr(info.common_bag_full_script)
    script = build_ticket_grant_script(
        info.ticket_grant_injection,
        original_no_amount_ptr,
        bag_full_ptr,
    )
    off = info.ticket_grant_injection
    before = bytes(rom[off:off + len(script)])
    if before != b"\xFF" * len(script):
        raise ValueError(f"ticket injection free space changed at 0x{off:X}")
    rom[off:off + len(script)] = script
    changes.append(PatchPoint(
        "Norman Eon Ticket grant script injected",
        off,
        before.hex(),
        script.hex(),
    ))

    off = info.norman_game_clear_target_pointer
    before = bytes(rom[off:off + 4])
    replacement = struct.pack("<I", offset_to_ptr(info.ticket_grant_injection))
    rom[off:off + 4] = replacement
    changes.append(PatchPoint(
        "Norman post-game branch redirected to Eon Ticket grant",
        off,
        before.hex(),
        replacement.hex(),
    ))

    return bytes(rom), info, changes


def report(info: Analysis, changes: list[PatchPoint] | None = None) -> dict:
    d = asdict(info)
    d["offsets_hex"] = {
        "ferry_attendant": f"0x{info.ferry_attendant:X}",
        "ferry_boarding_target": f"0x{info.ferry_boarding_target:X}",
        "eon_flag_checks": [f"0x{x:X}" for x in info.eon_flag_checks],
        "mystery_event_enabled_function":
            f"0x{info.mystery_event_enabled_function:X}",
        "norman_post_battle": f"0x{info.norman_post_battle:X}",
        "norman_game_clear_target_pointer":
            f"0x{info.norman_game_clear_target_pointer:X}",
        "original_norman_game_clear_target":
            f"0x{info.original_norman_game_clear_target:X}",
        "common_bag_full_script": f"0x{info.common_bag_full_script:X}",
        "ticket_grant_injection": f"0x{info.ticket_grant_injection:X}",
    }
    if changes is not None:
        d["changes"] = [
            {**asdict(c), "offset_hex": f"0x{c.offset:X}"}
            for c in changes
        ]
    return d


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="command", required=True)

    a = sub.add_parser("analyze", help="verify a Sapphire ROM and print patch points")
    a.add_argument("rom", type=Path)
    a.add_argument("--allow-unknown-sha1", action="store_true")

    p = sub.add_parser("patch", help="write a patched Sapphire ROM")
    p.add_argument("rom", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--allow-unknown-sha1", action="store_true")

    args = ap.parse_args()
    data = args.rom.read_bytes()

    if args.command == "analyze":
        info = analyze(data, args.allow_unknown_sha1)
        print(json.dumps(report(info), indent=2, ensure_ascii=False))
        return 0

    patched, info, changes = patch(data, args.allow_unknown_sha1)
    args.output.write_bytes(patched)
    out = report(info, changes)
    out["output"] = str(args.output)
    out["output_sha1"] = sha1(patched)
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
