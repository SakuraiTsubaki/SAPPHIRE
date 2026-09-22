#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

ROM_BASE = 0x08000000
JP_SIZE = 8 * 1024 * 1024
WESTERN_SIZE = 16 * 1024 * 1024
COMMON_BASE = 16 * 1024 * 1024
EXPANDED_SIZE = 32 * 1024 * 1024
CONTROL_SIZE = 0x1000
HEADER_SIZE = 0x100
PAYLOAD_BASE = COMMON_BASE + CONTROL_SIZE
DIRECTORY_OFFSET = HEADER_SIZE
DIRECTORY_ENTRY_SIZE = 32
DIRECTORY_CAPACITY = (CONTROL_SIZE - DIRECTORY_OFFSET) // DIRECTORY_ENTRY_SIZE
MAGIC = b"SAPPX10\0"
SCHEMA = 1
FILL = 0xFF
MAX_ALIGNMENT = 0x1000

# 8s magic, H schema, H header size, I control size, I input size,
# I expanded size, I common base, I payload base, H dir entry size,
# H dir capacity, H dir count, H flags, 4s game code, B revision,
# 3x pad, 20s source sha1, 32s source sha256, I header crc32.
HEADER = struct.Struct("<8sHHIIIIIHHHH4sB3x20s32sI")
DIRECTORY_ENTRY = struct.Struct("<16sIIIHH")
HEADER_DIR_COUNT_OFFSET = struct.calcsize("<8sHHIIIIIHH")
HEADER_CRC_OFFSET = HEADER.size - 4

KNOWN_ROMS = {
    "3233342c2f3087e6ffe6c1791cd5867db07df842": ("JP AXPJ rev0", JP_SIZE),
    "3ccbbd45f8553c36463f13b938e833f652b793e4": ("EN AXPE rev0", WESTERN_SIZE),
    "4722efb8cd45772ca32555b98fd3b9719f8e60a9": ("EN AXPE rev1", WESTERN_SIZE),
    "89b45fb172e6b55d51fc0e61989775187f6fe63c": ("EN AXPE rev2", WESTERN_SIZE),
    "5a087835009d552d4c5c1f96be3be3206e378153": ("DE AXPD rev0", WESTERN_SIZE),
    "7e6e034f9cdca6d2c4a270fdb50a94def5883d17": ("DE AXPD rev1", WESTERN_SIZE),
    "c269b5692b2d0e5800ba1ddf117fda95ac648634": ("FR AXPF rev0", WESTERN_SIZE),
    "860e93f5ea44f4278132f6c1ee5650d07b852fd8": ("FR AXPF rev1", WESTERN_SIZE),
    "f729dd571fb2c09e72c5c1d68fe0a21e72713d34": ("IT AXPI rev0", WESTERN_SIZE),
    "73edf67b9b82ff12795622dca412733755d2c0fe": ("IT AXPI rev1", WESTERN_SIZE),
    "3a6489189e581c4b29914071b79207883b8c16d8": ("ES AXPS rev0", WESTERN_SIZE),
    "0fe9ad1e602e2fafa090aee25e43d6980625173c": ("ES AXPS rev1", WESTERN_SIZE),
}


def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def offset_to_ptr(offset: int) -> int:
    if not (0 <= offset < EXPANDED_SIZE):
        raise ValueError(f"ROM offset out of expanded range: 0x{offset:X}")
    return ROM_BASE + offset


def align_up(value: int, alignment: int) -> int:
    if alignment < 1 or alignment > MAX_ALIGNMENT or alignment & (alignment - 1):
        raise ValueError("alignment must be a power of two from 1 through 4096")
    return (value + alignment - 1) & ~(alignment - 1)


def encode_name(name: str) -> bytes:
    raw = name.encode("ascii", "strict")
    if not raw or len(raw) > 16:
        raise ValueError("directory name must be 1..16 ASCII bytes")
    if b"\0" in raw:
        raise ValueError("directory name may not contain NUL")
    return raw.ljust(16, b"\0")


def identify_native(data: bytes) -> dict:
    digest = sha1(data)
    known = KNOWN_ROMS.get(digest)
    if known is None:
        raise ValueError(f"unknown Sapphire SHA-1: {digest}")
    label, expected_size = known
    if len(data) != expected_size:
        raise ValueError(f"ROM size mismatch for {label}: {len(data)} != {expected_size}")
    game_code = data[0xAC:0xB0].decode("ascii", "strict")
    revision = data[0xBC]
    if not game_code.startswith("AXP"):
        raise ValueError(f"not a Sapphire game code: {game_code!r}")
    return {
        "known_rom": label,
        "sha1": digest,
        "sha256": sha256(data),
        "size": len(data),
        "game_code": game_code,
        "revision": revision,
    }


def refresh_header_crc(control: bytearray) -> None:
    struct.pack_into("<I", control, HEADER_CRC_OFFSET, 0)
    crc = zlib.crc32(control[:HEADER_SIZE]) & 0xFFFFFFFF
    struct.pack_into("<I", control, HEADER_CRC_OFFSET, crc)


def build_control(native: dict) -> bytes:
    control = bytearray([FILL]) * CONTROL_SIZE
    HEADER.pack_into(
        control,
        0,
        MAGIC,
        SCHEMA,
        HEADER_SIZE,
        CONTROL_SIZE,
        native["size"],
        EXPANDED_SIZE,
        COMMON_BASE,
        PAYLOAD_BASE,
        DIRECTORY_ENTRY_SIZE,
        DIRECTORY_CAPACITY,
        0,
        0,
        native["game_code"].encode("ascii"),
        native["revision"],
        bytes.fromhex(native["sha1"]),
        bytes.fromhex(native["sha256"]),
        0,
    )
    refresh_header_crc(control)
    return bytes(control)


def expand(data: bytes) -> tuple[bytes, dict]:
    native = identify_native(data)
    output = bytearray([FILL]) * EXPANDED_SIZE
    output[:len(data)] = data
    output[COMMON_BASE:COMMON_BASE + CONTROL_SIZE] = build_control(native)

    if output[:len(data)] != data:
        raise AssertionError("native ROM prefix changed")
    if len(data) == JP_SIZE and output[JP_SIZE:COMMON_BASE] != bytes([FILL]) * (COMMON_BASE - JP_SIZE):
        raise AssertionError("Japanese 8->16 MiB gap is not erased padding")

    report = verify_expanded(bytes(output))
    report["native"] = native
    return bytes(output), report


def parse_control(data: bytes) -> dict:
    if len(data) != EXPANDED_SIZE:
        raise ValueError(f"expanded ROM must be 32 MiB, got {len(data)} bytes")
    control = data[COMMON_BASE:COMMON_BASE + CONTROL_SIZE]
    values = HEADER.unpack_from(control, 0)
    (
        magic, schema, header_size, control_size, input_size, expanded_size,
        common_base, payload_base, dir_entry_size, dir_capacity, dir_count,
        flags, game_code, revision, source_sha1, source_sha256, header_crc,
    ) = values
    if magic != MAGIC:
        raise ValueError(f"missing SAPPX10 expanded-ROM header: {magic!r}")

    header_copy = bytearray(control[:HEADER_SIZE])
    struct.pack_into("<I", header_copy, HEADER_CRC_OFFSET, 0)
    calculated_crc = zlib.crc32(header_copy) & 0xFFFFFFFF
    if calculated_crc != header_crc:
        raise ValueError(
            f"expanded-ROM header CRC mismatch: {calculated_crc:08x} != {header_crc:08x}"
        )
    if schema != SCHEMA or header_size != HEADER_SIZE:
        raise ValueError("unsupported expanded-ROM header schema")
    if control_size != CONTROL_SIZE or expanded_size != EXPANDED_SIZE:
        raise ValueError("expanded-ROM geometry mismatch")
    if common_base != COMMON_BASE or payload_base != PAYLOAD_BASE:
        raise ValueError("expanded-ROM base offsets mismatch")
    if dir_entry_size != DIRECTORY_ENTRY_SIZE or dir_capacity != DIRECTORY_CAPACITY:
        raise ValueError("expanded-ROM directory geometry mismatch")
    if dir_count > dir_capacity:
        raise ValueError("expanded-ROM directory count exceeds capacity")
    if input_size not in (JP_SIZE, WESTERN_SIZE):
        raise ValueError("invalid native input size recorded in expanded header")

    entries = []
    names = set()
    ranges = []
    for i in range(dir_count):
        off = DIRECTORY_OFFSET + i * DIRECTORY_ENTRY_SIZE
        name_raw, rom_offset, size, count, stride, entry_flags = DIRECTORY_ENTRY.unpack_from(
            control, off
        )
        try:
            name = name_raw.split(b"\0", 1)[0].decode("ascii", "strict")
        except UnicodeDecodeError as exc:
            raise ValueError(f"directory entry {i} name is not ASCII") from exc
        if not name:
            raise ValueError(f"directory entry {i} has an empty name")
        if name in names:
            raise ValueError(f"duplicate directory entry name: {name}")
        names.add(name)
        if size <= 0:
            raise ValueError(f"directory entry {name} has zero size")
        if rom_offset < PAYLOAD_BASE or rom_offset + size > EXPANDED_SIZE:
            raise ValueError(f"directory entry {name} lies outside the expansion payload")
        if stride and count and count * stride > size:
            raise ValueError(f"directory entry {name} count*stride exceeds allocation size")
        ranges.append((rom_offset, rom_offset + size, name))
        entries.append(
            {
                "name": name,
                "rom_offset": rom_offset,
                "rom_pointer": f"0x{offset_to_ptr(rom_offset):08X}",
                "size": size,
                "count": count,
                "stride": stride,
                "flags": entry_flags,
            }
        )

    ranges.sort()
    for previous, current in zip(ranges, ranges[1:]):
        if previous[1] > current[0]:
            raise ValueError(
                f"directory allocations overlap: {previous[2]} and {current[2]}"
            )

    unused_dir_start = DIRECTORY_OFFSET + dir_count * DIRECTORY_ENTRY_SIZE
    unused_dir_end = DIRECTORY_OFFSET + DIRECTORY_CAPACITY * DIRECTORY_ENTRY_SIZE
    if any(b != FILL for b in control[unused_dir_start:unused_dir_end]):
        raise ValueError("unused relocation-directory bytes are not erased")

    return {
        "magic": magic.rstrip(b"\0").decode("ascii"),
        "schema": schema,
        "header_size": header_size,
        "control_size": control_size,
        "input_size": input_size,
        "expanded_size": expanded_size,
        "common_expansion_base_offset": common_base,
        "common_expansion_base_pointer": f"0x{offset_to_ptr(common_base):08X}",
        "payload_base_offset": payload_base,
        "payload_base_pointer": f"0x{offset_to_ptr(payload_base):08X}",
        "payload_capacity_bytes": EXPANDED_SIZE - payload_base,
        "directory_entry_size": dir_entry_size,
        "directory_capacity": dir_capacity,
        "directory_count": dir_count,
        "flags": flags,
        "game_code": game_code.decode("ascii", "strict"),
        "revision": revision,
        "source_sha1": source_sha1.hex(),
        "source_sha256": source_sha256.hex(),
        "header_crc32": f"{header_crc:08x}",
        "directory": entries,
    }


def verify_expanded(data: bytes) -> dict:
    info = parse_control(data)
    input_size = info["input_size"]
    source_prefix = data[:input_size]
    prefix_sha1 = sha1(source_prefix)
    prefix_sha256 = sha256(source_prefix)
    if prefix_sha1 != info["source_sha1"]:
        raise ValueError("native input prefix SHA-1 does not match expanded header")
    if prefix_sha256 != info["source_sha256"]:
        raise ValueError("native input prefix SHA-256 does not match expanded header")
    known = KNOWN_ROMS.get(prefix_sha1)
    if known is None:
        raise ValueError("expanded ROM source prefix is not a known Sapphire revision")
    label, expected_size = known
    if expected_size != input_size:
        raise ValueError("expanded header input size disagrees with known source")
    if (
        data[0xAC:0xB0].decode("ascii", "strict") != info["game_code"]
        or data[0xBC] != info["revision"]
    ):
        raise ValueError("expanded header identity disagrees with native GBA header")
    if input_size == JP_SIZE:
        gap = data[JP_SIZE:COMMON_BASE]
        if any(b != FILL for b in gap):
            raise ValueError("Japanese 8->16 MiB gap contains non-0xFF data")

    return {
        "result": "pass",
        "known_source_rom": label,
        "expanded_sha1": sha1(data),
        "expanded_sha256": sha256(data),
        "source_prefix_preserved": True,
        **info,
    }


def install_blob(
    data: bytes,
    name: str,
    blob: bytes,
    *,
    count: int = 0,
    stride: int = 0,
    flags: int = 0,
    alignment: int = 4,
) -> tuple[bytes, dict]:
    info = verify_expanded(data)
    if not blob:
        raise ValueError("refusing to allocate an empty blob")
    if not (0 <= count <= 0xFFFFFFFF):
        raise ValueError("count must fit u32")
    if not (0 <= stride <= 0xFFFF):
        raise ValueError("stride must fit u16")
    if not (0 <= flags <= 0xFFFF):
        raise ValueError("flags must fit u16")
    encode_name(name)
    if any(entry["name"] == name for entry in info["directory"]):
        raise ValueError(f"directory entry already exists: {name}")
    if info["directory_count"] >= DIRECTORY_CAPACITY:
        raise ValueError("relocation directory is full")

    high_water = PAYLOAD_BASE
    for entry in info["directory"]:
        high_water = max(high_water, entry["rom_offset"] + entry["size"])
    rom_offset = align_up(high_water, alignment)
    end = rom_offset + len(blob)
    if end > EXPANDED_SIZE:
        raise ValueError(
            f"allocation {name} exceeds ROM payload by {end - EXPANDED_SIZE} bytes"
        )

    output = bytearray(data)
    if any(b != FILL for b in output[high_water:rom_offset]):
        raise ValueError("alignment gap contains non-erased data")
    if any(b != FILL for b in output[rom_offset:end]):
        raise ValueError("target allocation range contains non-erased data")
    output[rom_offset:end] = blob

    control = bytearray(output[COMMON_BASE:COMMON_BASE + CONTROL_SIZE])
    dir_slot = DIRECTORY_OFFSET + info["directory_count"] * DIRECTORY_ENTRY_SIZE
    DIRECTORY_ENTRY.pack_into(
        control,
        dir_slot,
        encode_name(name),
        rom_offset,
        len(blob),
        count,
        stride,
        flags,
    )
    struct.pack_into("<H", control, HEADER_DIR_COUNT_OFFSET, info["directory_count"] + 1)
    refresh_header_crc(control)
    output[COMMON_BASE:COMMON_BASE + CONTROL_SIZE] = control

    verified = verify_expanded(bytes(output))
    installed = verified["directory"][-1]
    if installed["name"] != name or installed["rom_offset"] != rom_offset:
        raise AssertionError("installed relocation entry did not round-trip")
    if output[rom_offset:end] != blob:
        raise AssertionError("installed payload did not round-trip")

    return bytes(output), {
        "result": "pass",
        "installed": {
            **installed,
            "alignment": alignment,
            "sha1": sha1(blob),
            "sha256": sha256(blob),
        },
        "directory_count": verified["directory_count"],
        "payload_bytes_remaining": EXPANDED_SIZE - end,
        "expanded_sha1": verified["expanded_sha1"],
        "expanded_sha256": verified["expanded_sha256"],
    }


def inspect(data: bytes) -> dict:
    if len(data) in (JP_SIZE, WESTERN_SIZE):
        return {"profile": "classic", **identify_native(data)}
    if len(data) == EXPANDED_SIZE:
        return {"profile": "expanded", **verify_expanded(data)}
    raise ValueError(f"unsupported Sapphire ROM size: {len(data)}")


def write_output(path: Path, data: bytes, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"refusing to overwrite existing output: {path}")
    path.write_bytes(data)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Build, allocate, inspect, and verify SAPPHIRE 32 MiB expanded ROMs"
    )
    sub = ap.add_subparsers(dest="command", required=True)

    p_expand = sub.add_parser("expand")
    p_expand.add_argument("rom", type=Path)
    p_expand.add_argument("output", type=Path)
    p_expand.add_argument("--force", action="store_true")

    p_install = sub.add_parser("install")
    p_install.add_argument("rom", type=Path, help="existing expanded ROM")
    p_install.add_argument("blob", type=Path)
    p_install.add_argument("output", type=Path)
    p_install.add_argument("--name", required=True)
    p_install.add_argument("--count", type=int, default=0)
    p_install.add_argument("--stride", type=int, default=0)
    p_install.add_argument("--flags", type=lambda x: int(x, 0), default=0)
    p_install.add_argument("--alignment", type=lambda x: int(x, 0), default=4)
    p_install.add_argument("--force", action="store_true")

    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("rom", type=Path)

    p_verify = sub.add_parser("verify")
    p_verify.add_argument("rom", type=Path)

    args = ap.parse_args()
    data = args.rom.read_bytes()

    if args.command == "expand":
        if args.rom.resolve() == args.output.resolve():
            raise ValueError("expanded output must not overwrite the native source ROM")
        output, report = expand(data)
        write_output(args.output, output, args.force)
        report["output"] = str(args.output)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.command == "install":
        if args.rom.resolve() == args.output.resolve():
            raise ValueError("install output must not overwrite the input expanded ROM")
        output, report = install_blob(
            data,
            args.name,
            args.blob.read_bytes(),
            count=args.count,
            stride=args.stride,
            flags=args.flags,
            alignment=args.alignment,
        )
        write_output(args.output, output, args.force)
        report["output"] = str(args.output)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.command == "inspect":
        print(json.dumps(inspect(data), indent=2, ensure_ascii=False))
        return 0

    print(json.dumps(verify_expanded(data), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
