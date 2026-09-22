#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

INVALID_U16 = 0xFFFF
REQUIRED_DOMAINS = {
    "species",
    "forms",
    "moves",
    "items",
    "abilities",
    "types",
    "evolution_methods",
    "move_effects",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def verify(registry_path: Path, capacity_path: Path) -> dict:
    registry = load_json(registry_path)
    capacity = load_json(capacity_path)
    errors: list[str] = []

    ids = registry.get("identifier_model", {})
    if ids.get("logical_id_bits") != 16:
        errors.append("registry logical IDs must be 16-bit")
    if ids.get("reserved_invalid_id") != INVALID_U16:
        errors.append("registry must reserve 0xFFFF as invalid")
    if ids.get("valid_range") != [0, INVALID_U16 - 1]:
        errors.append("registry valid logical ID range must be 0..65534")

    allocator = registry.get("allocator_contract", {})
    if allocator.get("directory_name_max_ascii_bytes") != 16:
        errors.append("directory-name width must stay 16 ASCII bytes")
    if allocator.get("directory_entries_available") != 120:
        errors.append("registry must target the 120-entry SAPPX10 directory")
    if allocator.get("form_change_status") != "deferred":
        errors.append("form-change must remain deferred")
    if allocator.get("allocator") != "tools/sapphire_rom_expansion.py install":
        errors.append("registry allocator path must point to sapphire_rom_expansion.py install")

    floors = capacity.get("capacity_floors", {})
    domains = registry.get("domains", {})
    if set(domains) != REQUIRED_DOMAINS:
        errors.append(
            "registry domains must exactly match the required capacity domains: "
            + ", ".join(sorted(REQUIRED_DOMAINS))
        )

    domain_directory_names = set()
    for name in sorted(REQUIRED_DOMAINS):
        entry = domains.get(name)
        if not isinstance(entry, dict):
            continue
        floor = entry.get("capacity_floor")
        if floor != floors.get(name):
            errors.append(
                f"{name} capacity floor {floor!r} does not match expansion_capacity "
                f"{floors.get(name)!r}"
            )
        if not isinstance(floor, int) or not (0 <= floor < INVALID_U16):
            errors.append(f"{name} capacity floor must fit the valid u16 logical namespace")

        directory_name = entry.get("directory_name")
        if not isinstance(directory_name, str):
            errors.append(f"{name} directory_name must be a string")
        else:
            try:
                raw = directory_name.encode("ascii", "strict")
            except UnicodeEncodeError:
                errors.append(f"{name} directory_name must be ASCII")
            else:
                if not raw or len(raw) > 16:
                    errors.append(f"{name} directory_name must be 1..16 ASCII bytes")
                if directory_name in domain_directory_names:
                    errors.append(f"duplicate domain directory_name: {directory_name}")
                domain_directory_names.add(directory_name)

    if domains.get("forms", {}).get("status") != "reserved_inactive":
        errors.append("forms domain must stay reserved_inactive while form-change is deferred")

    families = registry.get("table_families", [])
    if not isinstance(families, list) or not families:
        errors.append("table_families must be a non-empty list")
        families = []

    family_names = set()
    for index, family in enumerate(families):
        if not isinstance(family, dict):
            errors.append(f"table_families[{index}] must be an object")
            continue
        directory_name = family.get("directory_name")
        domain = family.get("domain")
        if domain not in REQUIRED_DOMAINS:
            errors.append(f"table family {directory_name!r} uses unknown domain {domain!r}")
        if not isinstance(directory_name, str):
            errors.append(f"table_families[{index}] directory_name must be a string")
            continue
        try:
            raw = directory_name.encode("ascii", "strict")
        except UnicodeEncodeError:
            errors.append(f"table family {directory_name!r} name must be ASCII")
            continue
        if not raw or len(raw) > 16:
            errors.append(f"table family {directory_name!r} name must be 1..16 ASCII bytes")
        if directory_name in family_names:
            errors.append(f"duplicate table family directory_name: {directory_name}")
        family_names.add(directory_name)

    missing_domain_names = domain_directory_names - family_names
    if missing_domain_names:
        errors.append(
            "every domain directory_name must also appear in table_families: "
            + ", ".join(sorted(missing_domain_names))
        )

    species_family = next(
        (family for family in families if family.get("directory_name") == "species_data"),
        None,
    )
    if species_family is None:
        errors.append("species_data table family is required")
    else:
        if species_family.get("format_status") != "implemented_v1":
            errors.append("species_data must be marked implemented_v1")
        if species_family.get("format_manifest") != "catalog/expanded_species_record_v1.json":
            errors.append("species_data format manifest path is not canonical")
        if species_family.get("builder") != "tools/sapphire_species_table.py":
            errors.append("species_data builder path is not canonical")
        if species_family.get("capacity") != 4096:
            errors.append("species_data capacity must be 4096")
        if species_family.get("stride") != 40:
            errors.append("species_data stride must be 40 bytes")
        if species_family.get("table_size_bytes") != 4096 * 40:
            errors.append("species_data table size must be 163840 bytes")
        if species_family.get("runtime_consumers") != "pending":
            errors.append("canonical species_data wide consumers must remain pending until redirected")

    compat_family = next(
        (family for family in families if family.get("directory_name") == "species_compat"),
        None,
    )
    if compat_family is None:
        errors.append("species_compat table family is required during staged runtime migration")
    else:
        if compat_family.get("format_status") != "implemented_v1":
            errors.append("species_compat must be marked implemented_v1")
        if compat_family.get("builder") != "tools/sapphire_species_runtime_patch.py":
            errors.append("species_compat builder path is not canonical")
        if compat_family.get("capacity") != 4096:
            errors.append("species_compat capacity must be 4096")
        if compat_family.get("stride") != 28:
            errors.append("species_compat stride must be 28 bytes")
        if compat_family.get("table_size_bytes") != 4096 * 28:
            errors.append("species_compat table size must be 114688 bytes")
        if compat_family.get("canonical") is not False:
            errors.append("species_compat must be explicitly non-canonical")
        if compat_family.get("runtime_consumers") != "direct_gBaseStats_pointer_literals_redirected":
            errors.append("species_compat runtime scope must match the verified direct-pointer redirection")

    return {
        "schema_version": registry.get("schema_version"),
        "project": registry.get("project"),
        "result": "pass" if not errors else "fail",
        "errors": errors,
        "domain_count": len(domains),
        "table_family_count": len(families),
        "directory_names": sorted(family_names),
        "capacity_floors": {name: domains.get(name, {}).get("capacity_floor") for name in sorted(REQUIRED_DOMAINS)},
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify the SAPPHIRE expanded table registry")
    ap.add_argument(
        "registry",
        nargs="?",
        type=Path,
        default=Path("catalog/expanded_table_registry.json"),
    )
    ap.add_argument(
        "--capacity",
        type=Path,
        default=Path("catalog/expansion_capacity.json"),
    )
    args = ap.parse_args()
    report = verify(args.registry, args.capacity)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
