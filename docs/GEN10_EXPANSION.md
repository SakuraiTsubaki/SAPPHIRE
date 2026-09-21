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

Expanded tables and assets must be placed into an explicit memory map at or above the shared base. They must not rely on whichever long `0xFF` block happens to exist in a particular regional ROM.

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

Modern-only state must live in a **versioned save extension**. No save bytes are allocated yet: the Sapphire save-sector map must be audited first.

The extension design must support at least:

- expanded Pokédex seen/caught bitsets
- modern ability-slot state where the Gen III one-bit selector is insufficient
- future per-Pokémon metadata
- reserved form metadata, inactive while form-change work remains deferred
- schema version and migration marker
- rollback/recovery validation

No save-layout change is considered implemented until old-save load, migration, save, reload and rollback tests pass.

## Required implementation order

1. Audit every hard-coded species, move, item, ability and type bound in Sapphire.
2. Establish the shared 16 MiB expansion base and 32 MiB expanded ROM container.
3. Audit save sectors and identify a safe versioned extension location.
4. Replace historical fixed-end comparisons with count-driven checks.
5. Add relocation manifests and patchers for each table family.
6. Add tests for IDs above every original Gen III maximum.
7. Verify the same architecture across Japanese first, then the supported regional revisions.
8. Import later-generation data only after the capacity foundation passes.
9. Keep form-change logic out of this phase.

## Generation 10 rule

Generation 10 content is not guessed or invented. When official data becomes available, it is imported into the existing logical namespaces and tables. If the actual data exceeds a capacity floor, the floor is raised without changing the 16-bit ID model or save-extension versioning strategy.
