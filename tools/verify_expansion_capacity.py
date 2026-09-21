#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

MIN_FLOORS = {
    "species": 4096,
    "forms": 8192,
    "moves": 4096,
    "items": 4096,
    "abilities": 2048,
    "types": 256,
    "evolution_methods": 512,
    "move_effects": 2048,
}

MAX_GBA_ROM = 32 * 1024 * 1024
INVALID_U16 = 0xFFFF


def verify(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []

    policy = data.get("policy", {})
    if policy.get("form_change_status") != "deferred":
        errors.append("form-change must remain deferred in this foundation phase")
    if policy.get("generation_10_content_status") != "unknown_not_encoded":
        errors.append("Generation 10 content must not be pre-invented")

    ids = data.get("identifier_model", {})
    if ids.get("logical_id_bits") != 16:
        errors.append("logical IDs must be 16-bit")
    if ids.get("reserved_invalid_id") != INVALID_U16:
        errors.append("0xFFFF must remain reserved as the invalid logical ID")
    if ids.get("valid_range") != [0, INVALID_U16 - 1]:
        errors.append("valid logical ID range must be 0..65534")

    floors = data.get("capacity_floors", {})
    for name, minimum in MIN_FLOORS.items():
        value = floors.get(name)
        if not isinstance(value, int) or value < minimum:
            errors.append(f"{name} capacity floor must be >= {minimum}")
        elif value >= INVALID_U16:
            errors.append(f"{name} capacity floor collides with the reserved u16 sentinel")

    rom = data.get("rom", {})
    if rom.get("classic_input_size_bytes") != 8 * 1024 * 1024:
        errors.append("classic Sapphire baseline must remain 8 MiB")
    if rom.get("expanded_profile_max_size_bytes") != MAX_GBA_ROM:
        errors.append("expanded profile must target the 32 MiB GBA ROM window")

    save = data.get("save", {})
    if not save.get("preserve_gen3_box_pokemon_core_layout"):
        errors.append("Gen III BoxPokemon core layout preservation is required")
    if not save.get("versioned_extension_required"):
        errors.append("a versioned save extension is required")
    if save.get("extension_allocation_status") != "pending_save_sector_audit":
        errors.append("save extension allocation must remain pending until the sector audit")
    if not save.get("form_metadata_reserved_but_inactive_while_form_change_is_deferred"):
        errors.append("form metadata must stay reserved/inactive during this phase")

    return {
        "schema_version": data.get("schema_version"),
        "project": data.get("project"),
        "result": "pass" if not errors else "fail",
        "errors": errors,
        "capacity_floors": floors,
        "logical_id_bits": ids.get("logical_id_bits"),
        "expanded_profile_max_size_bytes": rom.get("expanded_profile_max_size_bytes"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify SAPPHIRE Generation 10 expansion-capacity policy")
    ap.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=Path("catalog/expansion_capacity.json"),
    )
    args = ap.parse_args()
    report = verify(args.manifest)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
