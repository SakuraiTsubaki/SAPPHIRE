# External Events

## Definition

For this repository, an **external event** is Ruby/Sapphire game content whose activation or data originally depended on an external distribution path such as the e-Reader/Mystery Event transport.

This work does not redefine ordinary RTC-driven world events as external events; RTC independence is a separate patch axis.

## Permanent-access invariants

1. No e-Reader or historical distribution hardware is required.
2. Existing story progression is preserved unless the external distribution itself was the only gate.
3. Ticket events keep their original ticket/item checks; permanence is achieved by making the ticket obtainable in-game.
4. One-time encounter completion remains one-time unless a separate repeatable-event policy explicitly changes it.
5. Existing saves must remain usable.
6. Japanese data is the primary/origin reference.
7. Every supported regional ROM is located by signatures and verified rather than by a single hard-coded address table.

## Content inventory

| Class | Count | Original storage |
| --- | ---: | --- |
| Battle-e Trainers | 114 | One `BattleTowerEReaderTrainer` slot |
| e-Card Berries | 12 | One dynamic `EnigmaBerry` slot |
| Decoration Present | 1 card / 3 choices | Decoration inventory |
| Eon Ticket | 1 | Event script/item/system flag |

The exact card-to-source mapping is in `catalog/card_sources.json`.

## Ticket policy

Ticket-type events do **not** bypass the destination gate when the original game already knows how to consume/check the ticket.

For Sapphire Eon Ticket:

```
Hall of Fame complete
        ↓
talk to Norman
        ↓
EON TICKET granted once
        ↓
FLAG_SYS_HAS_EON_TICKET set
        ↓
original Lilycove Harbor checks
        ↓
original Southern Island checks
```

If an existing save already has the EON TICKET in the Bag or PC, the patch does not give another one. It only normalizes `FLAG_SYS_HAS_EON_TICKET` and then continues Norman's original post-game dialogue.

The Harbor and Southern Island ticket checks remain byte-for-byte original.

### Why flags alone are insufficient for Battle-e/Berries

A Battle-e trainer is a complete trainer payload with its own checksum. The save holds one such payload; scanning another trainer overwrites it.

An e-Card Berry is not twelve pre-existing item records waiting behind flags. Ruby/Sapphire stores one dynamic Enigma Berry definition. Loading another e-Card Berry replaces that definition.

Therefore the non-ticket design is:

```
ROM-local catalog
  ├─ 114 trainer payloads
  ├─ 12 berry payloads
  └─ Decoration Present payloads
          ↓
local selector / loader
          ↓
original single save slot(s)
```

This preserves engine compatibility while removing the external transport dependency.

## Status

### Implemented and ROM-validated

- Mystery Event title-menu gate removal.
- Local Eon Ticket grant through Norman after Hall of Fame.
- Duplicate prevention using Bag + PC checks.
- `FLAG_SYS_HAS_EON_TICKET` normalization for existing saves.
- Original Lilycove Harbor ticket requirement preserved.
- Original Southern Island ticket/system-flag requirement preserved.
- Story-clear gate preserved.
- Latias/Latios encounter-completion flag preserved.
- 12-ROM cross-region signature validation.
- Full 128-card content inventory and exact upstream source mapping.

### Remaining engine integration

The ROM-local selector/loader still has to be wired to copy the selected Battle-e Trainer or Berry payload into the original save slot. Until that loader is wired, the 114 trainers and 12 berries are catalogued but are **not yet all simultaneously playable from a patched ROM**.

This distinction is intentional: the repository must never label a catalogued event as implemented merely because its external gate is known.

## Separate distribution archive

Gift Pokémon delivered by other mechanisms (for example game-based or venue distributions) are a separate distribution-archive axis. They are not silently counted as Ruby/Sapphire Mystery Event/e-Reader cards.
