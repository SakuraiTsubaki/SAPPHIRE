#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zlib
from pathlib import Path

MAGIC = b"SPEVPACK"
VERSION = 1
HEADER = struct.Struct("<8sHHI")
ENTRY = struct.Struct("<8sBBHIII")
TRAINER_KIND = 1
BERRY_KIND = 2
TRAINER_SIZE = 0xBC
BERRY_SIZE = 0x530
MEV_PAYLOAD_OFFSET = 0x18


def trainer_checksum(payload: bytes) -> int:
    if len(payload) != TRAINER_SIZE:
        raise ValueError(f"trainer payload must be {TRAINER_SIZE:#x} bytes")
    return sum(
        struct.unpack_from("<I", payload, i)[0]
        for i in range(0, 0xB8, 4)
    ) & 0xFFFFFFFF


def berry_checksum(payload: bytes) -> int:
    if len(payload) != BERRY_SIZE:
        raise ValueError(f"berry payload must be {BERRY_SIZE:#x} bytes")
    return sum(payload[:0x52C]) & 0xFFFFFFFF


def extract_mev(path: Path, kind: int) -> bytes:
    data = path.read_bytes()
    size = TRAINER_SIZE if kind == TRAINER_KIND else BERRY_SIZE
    end = MEV_PAYLOAD_OFFSET + size
    if len(data) < end:
        raise ValueError(
            f"{path}: too small for payload ({len(data):#x} < {end:#x})"
        )
    payload = data[MEV_PAYLOAD_OFFSET:end]
    stored = struct.unpack_from("<I", payload, size - 4)[0]
    computed = (
        trainer_checksum(payload)
        if kind == TRAINER_KIND
        else berry_checksum(payload)
    )
    if stored != computed:
        raise ValueError(
            f"{path}: payload checksum mismatch: "
            f"stored={stored:#010x}, computed={computed:#010x}"
        )
    return payload


def resolve_path(source_root: Path, entry: dict, region: str) -> Path:
    template = entry.get("payload_template")
    if template:
        rel = template.format(region=region)
    else:
        rel = entry["payload"].replace("-EN.mev", f"-{region}.mev")
    return source_root / rel


def build(
    manifest_path: Path,
    source_root: Path,
    region: str,
    output: Path,
    index_output: Path | None,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records: list[tuple[str, int, bytes, str]] = []

    for card_id, entry in sorted(manifest["trainers"].items()):
        p = resolve_path(source_root, entry, region)
        records.append(
            (card_id, TRAINER_KIND, extract_mev(p, TRAINER_KIND), str(p))
        )

    for card_id, entry in sorted(manifest["berries"].items()):
        p = resolve_path(source_root, entry, region)
        records.append(
            (card_id, BERRY_KIND, extract_mev(p, BERRY_KIND), str(p))
        )

    data_offset = HEADER.size + ENTRY.size * len(records)
    directory = bytearray()
    payload_blob = bytearray()
    index = []
    cursor = data_offset

    for card_id, kind, payload, source in records:
        raw_id = card_id.encode("ascii")
        if len(raw_id) > 8:
            raise ValueError(f"card id too long for pack: {card_id}")

        digest32 = zlib.crc32(payload) & 0xFFFFFFFF
        directory += ENTRY.pack(
            raw_id.ljust(8, b"\0"),
            kind,
            0,
            0,
            cursor,
            len(payload),
            digest32,
        )
        index.append(
            {
                "card_id": card_id,
                "kind": "trainer" if kind == TRAINER_KIND else "berry",
                "offset": cursor,
                "size": len(payload),
                "crc32": f"{digest32:08x}",
                "sha1": hashlib.sha1(payload).hexdigest(),
                "source": source,
            }
        )
        payload_blob += payload
        cursor += len(payload)

    blob = (
        HEADER.pack(MAGIC, VERSION, len(records), data_offset)
        + directory
        + payload_blob
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(blob)

    report = {
        "schema_version": 1,
        "pack_format_version": VERSION,
        "region": region,
        "entries": len(records),
        "trainers": sum(1 for _, k, _, _ in records if k == TRAINER_KIND),
        "berries": sum(1 for _, k, _, _ in records if k == BERRY_KIND),
        "size": len(blob),
        "sha1": hashlib.sha1(blob).hexdigest(),
        "index": index,
    }

    if index_output:
        index_output.parent.mkdir(parents=True, exist_ok=True)
        index_output.write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return report


def main() -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Build a ROM-local Sapphire Battle-e/e-Card Berry payload pack "
            "from compiled .mev files"
        )
    )
    ap.add_argument(
        "--manifest",
        type=Path,
        default=Path("catalog/card_sources.json"),
    )
    ap.add_argument(
        "--source-root",
        type=Path,
        required=True,
        help="Artrios/pokecarde checkout with compiled build/*.mev",
    )
    ap.add_argument(
        "--region",
        default="JP",
        help="compiled region suffix, e.g. JP or EN (default: JP)",
    )
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--index-output", type=Path)
    args = ap.parse_args()

    report = build(
        args.manifest,
        args.source_root,
        args.region.upper(),
        args.output,
        args.index_output,
    )
    print(
        json.dumps(
            {k: v for k, v in report.items() if k != "index"},
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
