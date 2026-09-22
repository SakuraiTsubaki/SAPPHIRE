# Generation 10 Expansion Foundation

## Decision

Form-change work is **deferred**. The immediate priority is to make SAPPHIRE structurally ready for later-generation data, including Generation 10, without guessing unreleased species, moves, abilities, items, types, forms, or mechanics.

This foundation expands capacity first and content second.

## Verified Sapphire baseline

The baseline is pinned to `pret/pokeruby@63a8cbf0016b351a4e68f7036fa0b77e23d2f2c1`.

- `NUM_SPECIES = 412` (`SPECIES_EGG`); additional Unown constants extend through 439.
- `NUM_MOVES = 355`.
- `ITEMS_COUNT = 349`.
- `ABILITIES_COUNT = 78`.
- `NUMBER_OF_MON_TYPES = 18`.
- Box Pokémon already store species, held-item and four move IDs as `u16`.
- Ability identity is not stored directly. Gen III stores only the one-bit `altAbility` selector and derives the ability from species data.

That last point is a major modern-generation boundary: later games can require a third/hidden ability slot, so the expanded profile needs versioned auxiliary metadata rather than pretending the one-bit field can represent every modern ability state.

## ID model

The expanded profile uses a 16-bit logical namespace for all major content IDs.

- valid logical IDs: `0..65534`
- `65535` is reserved as an invalid/sentinel ID
- runtime/table counts are not stored in 8-bit fields
- future generations are not assigned a guessed fixed ID range
- tables are addressed by an explicit count and relocation manifest, not by a hard-coded historical end constant

Capacity floors are policy targets, not claims about Generation 10 content counts:

| Domain | Capacity floor |
| --- | ---: |
| Species | 4096 |
| Forms | 8192 |
| Moves | 4096 |
| Items | 4096 |
| Abilities | 2048 |
| Types | 256 |
| Evolution methods | 512 |
| Move effects | 2048 |

The 16-bit namespace intentionally leaves much more room than these floors.

## ROM profile

The native ROM size is region-dependent: the validated Japanese `AXPJ rev0` image is 8 MiB, while the validated western `AXPE/AXPD/AXPF/AXPI/AXPS` images are 16 MiB. The `classic` profile preserves each input at its native size.

The new `expanded` profile targets a 32 MiB ROM and uses **16 MiB (`0x01000000`) as the common expansion base** for every region. The Japanese image is padded/reserved up to that shared base; western images already occupy the lower 16 MiB. This gives every supported revision the same addresses for new common tables and assets.

The container stage is now implemented and validated across all 12 supplied ROM/SAV pairs. `tools/sapphire_rom_expansion.py` preserves the complete native ROM prefix, writes a 4 KiB SAPPX10 control page at `0x01000000` (CPU pointer `0x09000000`), and exposes the common payload from `0x01001000` (CPU pointer `0x09001000`) through the end of the 32 MiB ROM window. The usable common payload is 16,773,120 bytes.

The fixed 256-byte SAPPX10 header records source SHA-1/SHA-256 identity, game code, revision, geometry and CRC32. The remainder of the control page reserves 120 fixed-size relocation-directory entries. Table patchers will populate those entries instead of relying on incidental free space.

Expanded tables and assets must be placed into this explicit memory map. They must not rely on whichever long `0xFF` block happens to exist in a particular regional ROM.

The relocation allocator is now implemented. It appends registered payloads from the highest directory-owned end offset, supports explicit power-of-two alignment, updates the SAPPX10 directory/header CRC state, and rejects duplicate names, overlaps, invalid ranges and malformed unused directory space.

The first production relocation is now `species_data`, using `ExpandedSpeciesV1`:

- 4096 records × 40 bytes = 163,840 bytes;
- the 412 original `gBaseStats` source records are found by a cross-region ROM signature and verified against a canonical SHA-256;
- every original record round-trips back to its exact 26-byte Gen III data representation;
- type IDs are widened to u16;
- EXP yield is widened to u16;
- two u8 ability slots become three u16 slots;
- six packed 2-bit EV yields become six explicit u8 values;
- IDs 412..439 stay compatibility-reserved until the Egg/Unown mapping is explicit;
- runtime consumers still use legacy `gBaseStats` and are the next integration target.

The expanded profile therefore requires relocation metadata for:

- species/personal data
- learnsets
- evolutions
- moves
- items
- abilities
- type chart
- Pokédex data
- sprite/palette/icon pointer tables
- cries/audio tables

Every regional patch must continue to locate original structures by verified signatures.

## Save compatibility

The Gen III `BoxPokemon` core is kept intact so original Sapphire Pokémon and existing saves remain a migration target.

Modern-only state must live in a **versioned save extension**. The save-sector audit is now complete for the pinned retail baseline and the 12 supplied ROM/SAV pairs:

- retail sectors 0-27 are two rotating 14-sector main-save slots;
- sectors 28-29 are Hall of Fame;
- the pinned source explicitly defines `sUnusedFlashSectors[] = { 30, 31 }`;
- `SAVE_NORMAL` writes only the main-save sectors and does not touch 30-31;
- all 12 supplied saves have two complete main slots with valid retail checksums;
- sectors 30 and 31 are blank in all 12 supplied saves.

The expanded profile therefore assigns sectors **30 and 31** to a versioned A/B extension format. This is an allocation decision, not a claim that runtime support already exists: expanded-ROM load/save hooks still need to be added. The classic profile never writes these sectors.

The extension design must support at least:

- expanded Pokédex seen/caught bitsets
- modern ability-slot state where the Gen III one-bit selector is insufficient
- future per-Pokémon metadata
- reserved form metadata, inactive while form-change work remains deferred
- schema version and migration marker
- rollback/recovery validation

The offline extension format is defined in `catalog/expanded_save_layout.json`, but no save-layout change is considered runtime-implemented until old-save load, migration, save, reload and rollback tests pass.

## Required implementation order

1. Audit every hard-coded species, move, item, ability and type bound in Sapphire.
2. Establish the shared 16 MiB expansion base and 32 MiB expanded ROM container. **Implemented and validated for all 12 supplied revisions.**
3. Implement explicit SAPPX10 relocation allocation. **Implemented and validated for all 12 supplied revisions.**
4. Build the first widened production table (`species_data` / `ExpandedSpeciesV1`). **Implemented and cross-region validated; runtime consumers still pending.**
5. Use the audited retail-unused sectors 30-31 for the versioned A/B extension; add expanded-ROM runtime hooks without changing classic saves. **Offline format validated; runtime hooks still pending.**
6. Replace historical fixed-end comparisons and direct `gBaseStats` assumptions with count-driven expanded-profile accessors.
7. Add the remaining relocation manifests and table-family patchers.
8. Add tests for IDs above every original Gen III maximum.
9. Verify the same architecture across Japanese first, then the supported regional revisions.
10. Import later-generation data only after the capacity foundation passes.
11. Keep form-change logic out of this phase.

## Generation 10 rule

Generation 10 content is not guessed or invented. When official data becomes available, it is imported into the existing logical namespaces and tables. If the actual data exceeds a capacity floor, the floor is raised without changing the 16-bit ID model or save-extension versioning strategy.
