# mGBA Runtime Test — Littleroot Eon Ticket Courier

Date: 2026-09-19

## Emulator

- mGBA build: `0.11-9138-25ca25612`
- Commit: `25ca25612eb806ad3a70f3209ccd93890ea0c2c6`
- Frontend: `mgba-qt`
- Headless display: Xvfb
- Hardware video acceleration disabled for deterministic headless capture.

## ROM under test

- Game: Pocket Monsters Sapphire (Japan)
- Patched ROM SHA-1: `a62ace911e95e9f44d69499fcb2a79831e78a624`
- Patched ROM SHA-256: `387288ee42763e6cad2410256c28b3c2a5179b4cbe51d2aa0a5cfd23cfacdaf2`
- Existing Japanese save was loaded without conversion.

No ROM image is stored in this repository.

## Runtime procedure

1. Boot the patched Japanese ROM in mGBA.
2. Load the existing Japanese save.
3. Exit the player's Littleroot house.
4. Walk to Professor Birch's Lab.
5. Confirm the injected Event Courier is present near the lab.
6. Stand directly below the courier and press A.
7. Confirm the native Japanese item-acquisition UI.
8. Advance the standard Key Items pocket message.
9. Confirm the courier disappears.
10. Enter Professor Birch's Lab and exit again to force Littleroot map reload.
11. Confirm the courier remains absent after the map reload.

## Observed result

**PASS**

The game displayed:

`むげんのチケットを てにいれた！`

The following standard message also appeared, confirming the item was placed in the Key Items pocket:

`たいせつなものポケットに しまった！`

After the grant completed, the courier disappeared immediately. After entering and leaving Professor Birch's Lab, the courier remained absent, validating that `FLAG_SYS_HAS_EON_TICKET` is active and is functioning as the courier visibility flag.

The original Littleroot residents remained present and the map remained playable.

## Screenshot evidence

- [Japanese Eon Ticket acquisition](screenshots/mgba-eon-ticket-acquired-ja.png)
- [Courier still hidden after Littleroot map reload](screenshots/mgba-courier-hidden-after-map-reload-ja.png)

## Evidence hashes

- Ticket acquisition screenshot SHA-256: `71a246615223481c4518f8c83a7dd1044e9bc138e03ad89fbee3cfb6ad9b8f3a`
- Post-grant immediate disappearance screenshot SHA-256: `fed4d04cf8431bf2bb424c114efdbdd7d608131069e39f20820022ac95f4ad7d`
- Post-map-reload screenshot SHA-256: `6fced250b84a1fddf971840a616b11fa500fcb906a239cc732e47fa7c2451847`

## Conclusion

The Littleroot Event Courier implementation is not only signature/static validated; the primary Japanese ROM path has now been executed successfully in mGBA through item receipt and map reload.
