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

JP_SIZE = 8 * 1024 * 1024
WESTERN_SIZE = 16 * 1024 * 1024
COMMON_EXPANSION_BASE = 16 * 1024 * 1024
EXPANDED_SIZE = 32 * 1024 * 1024
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
    if rom.get("classic_input_sizes_bytes") != [JP_SIZE, WESTERN_SIZE]:
        errors.append("classic input sizes must preserve 8 MiB JP and 16 MiB western Sapphire dumps")
    if rom.get("common_expansion_base_offset") != COMMON_EXPANSION_BASE:
        errors.append("common expansion base must be 16 MiB")
    if rom.get("expanded_profile_size_bytes") != EXPANDED_SIZE:
        errors.append("expanded profile must target a 32 MiB ROM")
    if rom.get("common_expansion_capacity_bytes") != EXPANDED_SIZE - COMMON_EXPANSION_BASE:
        errors.append("common expansion capacity must be 16 MiB")
    if not rom.get("input_prefix_preserved_byte_for_byte"):
        errors.append("native ROM input prefix must be preserved byte-for-byte")

    save = data.get("save", {})
    if not save.get("preserve_gen3_box_pokemon_core_layout"):
        errors.append("Gen III BoxPokemon core layout preservation is required")
    if not save.get("versioned_extension_required"):
        errors.append("a versioned save extension is required")
    if save.get("extension_allocation_status") != "sectors_30_31_source_and_save_validated_runtime_hooks_pending":
        errors.append("save extension must use the source- and checksum-validated sector 30/31 allocation")
    if save.get("extension_sectors") != [30, 31]:
        errors.append("expanded save extension sectors must be 30 and 31")
    if save.get("hall_of_fame_sectors") != [28, 29]:
        errors.append("Hall of Fame sectors 28 and 29 must remain reserved")
    if save.get("mirrored_payload_bytes") != 4032:
        errors.append("mirrored extension payload must remain 4032 bytes per copy")
    if save.get("extension_layout") != "catalog/expanded_save_layout.json":
        errors.append("expanded save extension must use the canonical expanded_save_layout.json manifest")
    pair_audit = save.get("supplied_pair_audit", {})
    if pair_audit.get("validated_main_sector_checksums") != 336 or not pair_audit.get("all_legacy_sector_checksums_valid"):
        errors.append("all 336 supplied main-save sector checksums must remain validated")
    if save.get("runtime_hooks_implemented") is not False:
        errors.append("runtime save-extension hooks must remain explicitly pending until integrated")
    if not save.get("form_metadata_reserved_but_inactive_while_form_change_is_deferred"):
        errors.append("form metadata must stay reserved/inactive during this phase")

    return {
        "schema_version": data.get("schema_version"),
        "project": data.get("project"),
        "result": "pass" if not errors else "fail",
        "errors": errors,
        "capacity_floors": floors,
        "logical_id_bits": ids.get("logical_id_bits"),
        "common_expansion_base_offset": rom.get("common_expansion_base_offset"),
        "expanded_profile_size_bytes": rom.get("expanded_profile_size_bytes"),
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
