# SAPPHIRE

Pokémon Sapphire ROM modification project.

## External-event permanence

The target is **not only Eon Ticket**. Ruby/Sapphire external content is being converted so it can be accessed locally without the original e-Reader/distribution transport.

Current base patch:

- MYSTERY EVENT main-menu entry is always enabled for a valid save.
- A dedicated Event Courier NPC is added to Littleroot Town near Professor Birch's Lab.
- The courier gives the EON TICKET immediately, with no story-progression requirement.
- The ticket grant sets `FLAG_SYS_HAS_EON_TICKET`, matching the original distribution behavior.
- If the EON TICKET already exists in the Bag or PC, the courier does not create a duplicate and only normalizes the system flag.
- The courier uses `FLAG_SYS_HAS_EON_TICKET` as its visibility flag, so it disappears after the ticket is active.
- Lilycove Harbor and Southern Island keep their original EON TICKET/system-flag checks.
- Norman and the original story scripts are not modified by the ticket grant.
- Signature-based detection is validated against 12 Japanese/English/German/French/Italian/Spanish Sapphire ROM revisions.
- No ROM image is stored in this repository.

Full card catalog:

- 114 Battle-e Trainer cards
- 12 e-Card Berry cards
- 1 Decoration Present card, containing three Regi Doll choices
- 1 Eon Ticket card

Ticket-type world events should use their real ticket/item path whenever possible. Data-bearing Battle-e Trainer and Berry cards need the ROM-local catalog/loader because the original engine only stores one trainer and one e-Reader Berry at a time. See `docs/EXTERNAL_EVENTS.md`.

## Generation 10 expansion foundation

Form-change work is currently **deferred**. The active expansion priority is to make Sapphire structurally ready for later-generation data through a separate `expanded` profile before importing additional gameplay content.

- 16-bit logical ID namespace with `0xFFFF` reserved as invalid.
- Capacity floors: 4096 species, 8192 forms, 4096 moves, 4096 items, 2048 abilities, 256 types.
- Native ROM size is 8 MiB for Japanese AXPJ rev0 and 16 MiB for the validated western revisions.
- Expanded ROM profile uses a shared 16 MiB (`0x01000000`) expansion base and a 32 MiB final ROM size, which fills the GBA's directly addressable ROM window.
- The 32 MiB container is implemented: a 4 KiB SAPPX10 control page begins at `0x01000000` / `0x09000000`, and common payload allocation begins at `0x01001000` / `0x09001000`.
- The control page reserves 120 relocation-directory entries and records the source ROM SHA-1/SHA-256, game code, revision and a CRC32-protected header.
- The relocation allocator is implemented. It appends explicitly registered blobs from the payload high-water mark, supports explicit power-of-two alignment, updates the directory/header CRC, and rejects duplicate names, overlaps and out-of-range entries.
- Variable tables and assets must use explicit relocation metadata instead of incidental free-space assumptions.
- `catalog/expanded_table_registry.json` defines the Generation 10 capacity domains and the table families that still need concrete binary formats; form-change remains reserved/inactive.
- Existing Gen III BoxPokemon layout remains the compatibility core.
- Modern-only persistent state uses a versioned A/B save extension in retail-unused flash sectors 30-31; the offline format is defined, while expanded-ROM runtime hooks are still pending.
- Generation 10 IDs and mechanics are not guessed before official data exists.
- Form metadata is reserved but inactive until form-change work is explicitly resumed.

See `docs/GEN10_EXPANSION.md`, `catalog/expansion_capacity.json`, `catalog/engine_profiles.json`, `catalog/expanded_rom_layout.json`, `catalog/expanded_table_registry.json`, and `catalog/expanded_save_layout.json`.

Validate the capacity policy with:

```sh
python tools/verify_expansion_capacity.py
```

## Tools

Expansion ROM/save tooling:

```sh
python tools/sapphire_rom_save_audit.py /path/to/paired-rom-save-directory

python tools/sapphire_rom_expansion.py inspect sapphire.gba
python tools/sapphire_rom_expansion.py expand sapphire.gba sapphire.expanded.gba
python tools/sapphire_rom_expansion.py verify sapphire.expanded.gba
python tools/sapphire_rom_expansion.py install sapphire.expanded.gba table.bin sapphire.with-table.gba --name species_data --count 4096 --alignment 4

python tools/sapphire_save_extension.py inspect sapphire.sav
python tools/sapphire_save_extension.py init sapphire.gba sapphire.sav sapphire.expanded.sav
python tools/sapphire_save_extension.py verify sapphire.expanded.sav

python tools/sapphire_expand_pair.py sapphire.gba sapphire.sav sapphire.expanded.gba sapphire.expanded.sav
python tools/verify_expanded_table_registry.py
```

The paired expander validates both inputs first, builds both outputs in memory, refuses to overwrite the source pair, and refuses to reset an already initialized SAPPXSV1 save unless a separate migration path is implemented.

The ROM expander accepts the 12 validated Sapphire revisions, preserves the complete native ROM prefix, pads Japanese AXPJ rev0 from 8 MiB to the common 16 MiB base, writes the SAPPX10 control page, and produces an exact 32 MiB image. `install` adds an explicit payload allocation and relocation-directory entry without scanning for arbitrary free space. The validation report is `reports/expanded-rom-container-validation.json`.

`init` verifies the known Sapphire ROM identity, two complete retail save slots, and every retail main-sector checksum before writing the mirrored SAPPXSV1 extension to sectors 30-31. It preserves sectors 0-29 byte-for-byte.

`tools/sapphire_external_events.py`

```sh
python tools/sapphire_external_events.py analyze sapphire.gba
python tools/sapphire_external_events.py patch sapphire.gba sapphire.external-events.gba
```

The tool rejects unknown ROM hashes by default.

## Reference order

Japanese/original material is the primary reference. Korean is next where official material exists, then English, followed by other official languages.
