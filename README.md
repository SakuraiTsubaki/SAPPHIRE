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

## Tools

`tools/sapphire_external_events.py`

```sh
python tools/sapphire_external_events.py analyze sapphire.gba
python tools/sapphire_external_events.py patch sapphire.gba sapphire.external-events.gba
```

The tool rejects unknown ROM hashes by default.

## Reference order

Japanese/original material is the primary reference. Korean is next where official material exists, then English, followed by other official languages.
