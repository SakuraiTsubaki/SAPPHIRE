#!/usr/bin/env python3
"""Pokemon Sapphire external-event permanence patcher.

Makes externally distributed Ruby/Sapphire content locally reachable while
preserving the original in-game destination gates.

Eon Ticket policy:
- Do NOT bypass Lilycove Harbor or Southern Island checks.
- Add a dedicated event courier to Littleroot Town.
- The courier gives the Eon Ticket once, using the game's normal item-give UI.
- Receiving/owning the ticket sets FLAG_SYS_HAS_EON_TICKET.
- The courier is hidden by that same system flag after the ticket is active.

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

COURIER_LOCAL_ID = 7
COURIER_GFX_ID = 25  # OBJ_EVENT_GFX_MAN_3
COURIER_X = 9
COURIER_Y = 16
COURIER_ELEVATION = 3
COURIER_MOVEMENT_TYPE = 8  # MOVEMENT_TYPE_FACE_DOWN

FERRY_PREFIX = bytes.fromhex("6A 5A 47 13 01 01 00 21 0D 80 01 00 06 01")

LATI_THEN_EON_RE = re.compile(
    rb"\x2B\xCE\x00\x06\x01....\x2B\x53\x08\x06\x00....",
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

OBJECT_EVENT_SIZE = 24
LITTLEROOT_ORIGINAL_OBJECT_COUNT = 6
LITTLEROOT_NEW_OBJECT_COUNT = 7
FREE_SPACE_SEARCH_START = 0x600000
FREE_SPACE_RESERVE = 0x200


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
    eon_flag_checks: list[int]
    mystery_event_enabled_function: int
    common_bag_full_script: int
    littleroot_object_table: int
    littleroot_map_events: int
    courier_injection: int
    courier_script: int


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


def identify_common_bag_full_script(data: bytes) -> int:
    matches = list(GIVE_ENIGMA_BERRY_RE.finditer(data))
    if len(matches) != 1:
        raise ValueError(
            "Enigma Berry gift script: expected exactly 1 match, "
            f"found {len(matches)}"
        )
    ptr = struct.unpack("<I", matches[0].group("bag_full"))[0]
    return ptr_to_offset(ptr, len(data))


def littleroot_object_signature() -> re.Pattern[bytes]:
    specs = [
        (1, 136, 16, 10, 3, 2, 0x21, 0x0000),
        (2, 17, 12, 13, 3, 2, 0x12, 0x0364),
        (3, 9, 14, 17, 3, 2, 0x12, 0x0000),
        (4, 215, 5, 8, 3, 7, 0x00, 0x02F0),
        (5, 94, 2, 10, 4, 10, 0x00, 0x02F9),
        (6, 94, 11, 10, 4, 10, 0x00, 0x02FA),
    ]

    pattern = bytearray()
    for local_id, gfx, x, y, elev, move, move_range, flag in specs:
        pattern += re.escape(struct.pack(
            "<BBBBhhBBBBHH",
            local_id,
            gfx,
            0,
            0,
            x,
            y,
            elev,
            move,
            move_range,
            0,
            0,
            0,
        ))
        pattern += b"...."  # region/language-specific script pointer
        pattern += re.escape(struct.pack("<H", flag) + b"\x00\x00")
    return re.compile(bytes(pattern), re.DOTALL)


LITTLEROOT_OBJECT_RE = littleroot_object_signature()


def identify_littleroot_object_table(data: bytes) -> int:
    return require_unique(
        "Littleroot original object table",
        (m.start() for m in LITTLEROOT_OBJECT_RE.finditer(data)),
    )


def identify_littleroot_map_events(data: bytes, object_table: int) -> int:
    object_ptr = struct.pack("<I", offset_to_ptr(object_table))
    candidates: list[int] = []
    for hit in find_all(data, object_ptr):
        start = hit - 4
        if start < 0:
            continue
        # 6 objects, 3 warps, 9 coord events, 4 background events.
        if data[start:start + 4] == bytes((6, 3, 9, 4)):
            candidates.append(start)
    return require_unique("Littleroot MapEvents", candidates)


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


def build_courier_script(script_offset: int, bag_full_ptr: int) -> bytes:
    out = bytearray()
    owned_fixups: list[int] = []

    def u16(value: int) -> bytes:
        return struct.pack("<H", value)

    def u32(value: int) -> bytes:
        return struct.pack("<I", value)

    # lock; faceplayer
    out += b"\x6A\x5A"

    # If the ticket already exists in Bag or PC, normalize the system flag
    # without creating a duplicate.
    out += b"\x47" + u16(ITEM_EON_TICKET) + u16(1)
    out += b"\x21" + u16(VAR_RESULT) + u16(1)
    out += b"\x06\x01"
    owned_fixups.append(len(out))
    out += b"\x00" * 4

    out += b"\x4A" + u16(ITEM_EON_TICKET) + u16(1)
    out += b"\x21" + u16(VAR_RESULT) + u16(1)
    out += b"\x06\x01"
    owned_fixups.append(len(out))
    out += b"\x00" * 4

    # giveitem ITEM_EON_TICKET
    out += b"\x1A" + u16(VAR_0x8000) + u16(ITEM_EON_TICKET)
    out += b"\x1A" + u16(VAR_0x8001) + u16(1)
    out += b"\x09\x00"

    # If the Key Items pocket is full, keep the courier present for retry.
    out += b"\x21" + u16(VAR_RESULT) + u16(0)
    out += b"\x06\x01" + u32(bag_full_ptr)

    owned_label = len(out)

    # Match the original distribution's system state and remove the courier.
    out += b"\x29" + u16(FLAG_SYS_HAS_EON_TICKET)
    out += b"\x53" + u16(COURIER_LOCAL_ID)
    out += b"\x6C\x02"

    owned_ptr = offset_to_ptr(script_offset + owned_label)
    for pos in owned_fixups:
        out[pos:pos + 4] = u32(owned_ptr)

    return bytes(out)


def build_courier_object(script_ptr: int) -> bytes:
    return (
        struct.pack(
            "<BBBBhhBBBBHHIH",
            COURIER_LOCAL_ID,
            COURIER_GFX_ID,
            0,  # OBJ_KIND_NORMAL
            0,
            COURIER_X,
            COURIER_Y,
            COURIER_ELEVATION,
            COURIER_MOVEMENT_TYPE,
            0,
            0,
            0,  # TRAINER_TYPE_NONE
            0,
            script_ptr,
            FLAG_SYS_HAS_EON_TICKET,
        )
        + b"\x00\x00"
    )


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

    eon_checks = [m.start() + 9 for m in LATI_THEN_EON_RE.finditer(data)]
    if len(eon_checks) != 2:
        raise ValueError(
            "Eon Ticket system-flag checks: expected Harbor + Southern Island "
            f"(2), found {len(eon_checks)}"
        )

    mystery = identify_mystery_event_enabled(data)
    bag_full = identify_common_bag_full_script(data)
    object_table = identify_littleroot_object_table(data)
    map_events = identify_littleroot_map_events(data, object_table)
    injection = find_free_ff_block(data)
    courier_script = injection + (LITTLEROOT_NEW_OBJECT_COUNT * OBJECT_EVENT_SIZE)

    return Analysis(
        sha1=digest,
        known_rom=known,
        title=title,
        game_code=game_code,
        revision=revision,
        ferry_attendant=ferry,
        eon_flag_checks=eon_checks,
        mystery_event_enabled_function=mystery,
        common_bag_full_script=bag_full,
        littleroot_object_table=object_table,
        littleroot_map_events=map_events,
        courier_injection=injection,
        courier_script=courier_script,
    )


def patch(
    data: bytes,
    allow_unknown_sha1: bool = False,
) -> tuple[bytes, Analysis, list[PatchPoint]]:
    info = analyze(data, allow_unknown_sha1=allow_unknown_sha1)
    rom = bytearray(data)
    changes: list[PatchPoint] = []

    # Keep MYSTERY EVENT available locally for the non-ticket event catalog.
    off = info.mystery_event_enabled_function
    before = bytes(rom[off:off + 4])
    rom[off:off + 4] = RETURN_TRUE_THUMB
    changes.append(PatchPoint(
        "Mystery Event menu always enabled",
        off,
        before.hex(),
        RETURN_TRUE_THUMB.hex(),
    ))

    # Build a complete replacement Littleroot object table with one appended
    # courier, plus the courier script immediately after the 7th entry.
    original_objects = data[
        info.littleroot_object_table:
        info.littleroot_object_table + LITTLEROOT_ORIGINAL_OBJECT_COUNT * OBJECT_EVENT_SIZE
    ]
    courier_script = build_courier_script(
        info.courier_script,
        offset_to_ptr(info.common_bag_full_script),
    )
    courier = build_courier_object(offset_to_ptr(info.courier_script))
    injected = original_objects + courier + courier_script

    if len(injected) > FREE_SPACE_RESERVE:
        raise ValueError(
            f"courier injection grew to {len(injected)} bytes; "
            f"reserved block is {FREE_SPACE_RESERVE}"
        )

    off = info.courier_injection
    before = bytes(rom[off:off + len(injected)])
    if before != b"\xFF" * len(injected):
        raise ValueError(f"courier free space changed at 0x{off:X}")
    rom[off:off + len(injected)] = injected
    changes.append(PatchPoint(
        "Littleroot Event Courier object table and script injected",
        off,
        before.hex(),
        injected.hex(),
    ))

    # MapEvents: increase object count 6 -> 7 and redirect only the object
    # table pointer. Warps, coord events, bg events, map scripts and layout
    # remain untouched.
    off = info.littleroot_map_events
    before = bytes(rom[off:off + 8])
    if before[0] != LITTLEROOT_ORIGINAL_OBJECT_COUNT:
        raise ValueError("Littleroot object count changed unexpectedly")
    replacement = bytearray(before)
    replacement[0] = LITTLEROOT_NEW_OBJECT_COUNT
    replacement[4:8] = struct.pack("<I", offset_to_ptr(info.courier_injection))
    rom[off:off + 8] = replacement
    changes.append(PatchPoint(
        "Littleroot MapEvents extended with Event Courier",
        off,
        before.hex(),
        bytes(replacement).hex(),
    ))

    # Hard invariant: ticket destination gates are not modified.
    if rom[
        info.ferry_attendant:
        info.ferry_attendant + len(FERRY_PREFIX)
    ] != data[
        info.ferry_attendant:
        info.ferry_attendant + len(FERRY_PREFIX)
    ]:
        raise ValueError("Lilycove Eon Ticket item gate was modified")

    for index, off in enumerate(info.eon_flag_checks, start=1):
        if rom[off:off + 9] != data[off:off + 9]:
            raise ValueError(
                f"Eon Ticket system-flag gate #{index} was modified at 0x{off:X}"
            )

    return bytes(rom), info, changes


def report(info: Analysis, changes: list[PatchPoint] | None = None) -> dict:
    d = asdict(info)
    d["courier"] = {
        "local_id": COURIER_LOCAL_ID,
        "graphics_id": COURIER_GFX_ID,
        "x": COURIER_X,
        "y": COURIER_Y,
        "elevation": COURIER_ELEVATION,
        "movement_type": COURIER_MOVEMENT_TYPE,
        "visibility_flag": f"0x{FLAG_SYS_HAS_EON_TICKET:04X}",
    }
    d["offsets_hex"] = {
        "ferry_attendant": f"0x{info.ferry_attendant:X}",
        "eon_flag_checks": [f"0x{x:X}" for x in info.eon_flag_checks],
        "mystery_event_enabled_function":
            f"0x{info.mystery_event_enabled_function:X}",
        "common_bag_full_script": f"0x{info.common_bag_full_script:X}",
        "littleroot_object_table": f"0x{info.littleroot_object_table:X}",
        "littleroot_map_events": f"0x{info.littleroot_map_events:X}",
        "courier_injection": f"0x{info.courier_injection:X}",
        "courier_script": f"0x{info.courier_script:X}",
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
