#!/usr/bin/env python3
"""Safely extend Pokemon Sapphire's item table to the Emerald Gen III ID layout.

This patcher establishes Emerald's 0..376 item-ID space without changing the
ROM size or save layout. Existing Sapphire records 0..348 are copied byte for
byte because their scalar game parameters already match Emerald, aside from the
six Emerald EV-reducing berry behaviors documented as an engine backport.

IDs 349..376 are appended in Emerald order. Core scalar parameters are copied
from Emerald semantics. Display names are localized Gen III names; descriptions
use Sapphire's harmless dummy description until the text layer is ported.

Engine-parity exceptions intentionally left safe rather than faked:
- Pomeg/Kelpsy/Qualot/Hondew/Grepa/Tamato (153..158): Emerald changes their
  party-use behavior and uses signed -10 EV item effects. Sapphire's original
  item-effect engine is unsigned in that path, so the six records remain
  byte-identical to Sapphire until the signed-EV backport is installed.
- Powder Jar (372): Emerald's Berry Powder UI does not exist in Sapphire. Its
  field-use callback is mapped to CannotUse while retaining Emerald's remaining
  scalar parameters.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
from dataclasses import asdict, dataclass
from pathlib import Path

ROM_BASE = 0x08000000
EMERALD_ITEMS_COUNT = 377
RUBY_SAPPHIRE_ITEMS_COUNT = 349
NEW_ITEM_FIRST = 349
NEW_ITEM_LAST = 376

KNOWN_ROMS = {
    "3233342c2f3087e6ffe6c1791cd5867db07df842": "JP AXPJ rev0",
    "3ccbbd45f8553c36463f13b938e833f652b793e4": "EN AXPE rev0",
    "4722efb8cd45772ca32555b98fd3b9719f8e60a9": "EN AXPE rev1",
    "89b45fb172e6b55d51fc0e61989775187f6fe63c": "EN AXPE rev2",
    "5a087835009d552d4c5c1f96be3be3206e378153": "DE AXPD rev0",
    "7e6e034f9cdca6d2c4a270fdb50a94def5883d17": "DE AXPD rev1",
    "c269b5692b2d0e5800ba1ddf117fda95ac648634": "FR AXPF rev0",
    "860e93f5ea44f4278132f6c1ee5650d07b852fd8": "FR AXPF rev1",
    "f729dd571fb2c09e72c5c1d68fe0a21e72713d34": "IT AXPI rev0",
    "73edf67b9b82ff12795622dca412733755d2c0fe": "IT AXPI rev1",
    "3a6489189e581c4b29914071b79207883b8c16d8": "ES AXPS rev0",
    "0fe9ad1e602e2fafa090aee25e43d6980625173c": "ES AXPS rev1",
}

NEW_SYMBOLS = [
    "ITEM_OAKS_PARCEL", "ITEM_POKE_FLUTE", "ITEM_SECRET_KEY",
    "ITEM_BIKE_VOUCHER", "ITEM_GOLD_TEETH", "ITEM_OLD_AMBER",
    "ITEM_CARD_KEY", "ITEM_LIFT_KEY", "ITEM_HELIX_FOSSIL",
    "ITEM_DOME_FOSSIL", "ITEM_SILPH_SCOPE", "ITEM_BICYCLE",
    "ITEM_TOWN_MAP", "ITEM_VS_SEEKER", "ITEM_FAME_CHECKER",
    "ITEM_TM_CASE", "ITEM_BERRY_POUCH", "ITEM_TEACHY_TV",
    "ITEM_TRI_PASS", "ITEM_RAINBOW_PASS", "ITEM_TEA",
    "ITEM_MYSTIC_TICKET", "ITEM_AURORA_TICKET", "ITEM_POWDER_JAR",
    "ITEM_RUBY", "ITEM_SAPPHIRE", "ITEM_MAGMA_EMBLEM", "ITEM_OLD_SEA_MAP",
]

NAMES = {
    "JP": [
        "おとどけもの", "ポケモンのふえ", "ひみつのカギ", "ひきかえけん",
        "きんのいれば", "ひみつのコハク", "カードキー", "エレベータのカギ",
        "かいのカセキ", "こうらのカセキ", "シルフスコープ", "じてんしゃ",
        "タウンマップ", "バトルサーチャー", "ボイスチェッカー", "わざマシンケース",
        "きのみぶくろ", "おしえテレビ", "トライパス", "レインボーパス", "おちゃ",
        "しんぴのチケット", "オーロラチケット", "こないれ", "ルビー", "サファイア",
        "マグマのしるし", "ふるびたかいず",
    ],
    "EN": [
        "Oak's Parcel", "Poké Flute", "Secret Key", "Bike Voucher", "Gold Teeth",
        "Old Amber", "Card Key", "Lift Key", "Helix Fossil", "Dome Fossil",
        "Silph Scope", "Bicycle", "Town Map", "Vs. Seeker", "Fame Checker",
        "TM Case", "Berry Pouch", "Teachy TV", "Tri-Pass", "Rainbow Pass", "Tea",
        "MysticTicket", "AuroraTicket", "Powder Jar", "Ruby", "Sapphire",
        "Magma Emblem", "Old Sea Map",
    ],
    "DE": [
        "Eichs Paket", "Pokéflöte", "?-Öffner", "Rad-Coupon", "Goldzähne",
        "Altbernstein", "Türöffner", "Liftöffner", "Helixfossil", "Domfossil",
        "Silph-Scope", "Fahrrad", "Karte", "Kampffahnder", "Ruhmesdatei",
        "VM/TM-Box", "Beerentüte", "Lehrkanal", "Tri-Pass", "Bunt-Pass", "Tee",
        "Geheimticket", "Auroraticket", "Puderdöschen", "Rubin", "Saphir",
        "Magmaemblem", "Alte Karte",
    ],
    "FR": [
        "Colis Chen", "Pokéflûte", "Clé Secrète", "Bon Commande", "Dent d'Or",
        "Vieil Ambre", "Carte Magn.", "Clé Asc.", "Nautile", "Fossile Dôme",
        "Scope Sylphe", "Bicyclette", "Carte", "Cherche VS", "Memorydex",
        "Boîte CT", "Sac à Baies", "TV ABC", "Tri-Passe", "Passe Prisme", "Thé",
        "Ticketmystik", "Ticketaurora", "Pot Poudre", "Rubis", "Saphir",
        "Sceau Magma", "Vieillecarte",
    ],
    "IT": [
        "Pacco di Oak", "Poké Flauto", "Chiave Segr.", "Buono Bici", "Denti d'oro",
        "Ambra Antica", "Apriporta", "Chiave Asc.", "Helixfossile", "Domofossile",
        "Spettrosonda", "Bicicletta", "Mappa Città", "Cercasfide", "Pokévip",
        "Porta-MT", "Portabacche", "Pokétivù", "Tris Pass", "Sette Pass", "Tè",
        "Bigl. Magico", "Bigl. Aurora", "Portafarina", "Rubino", "Zaffiro",
        "Stemma Magma", "Mappa Stinta",
    ],
    "ES": [
        "Correo Oak", "Poké flauta", "Llave secreta", "Bono bici", "Dientes oro",
        "Ámbar viejo", "Llave magn.", "Llave asc.", "Fósil hélix", "Fósil domo",
        "Scope Silph", "Bicicleta", "Mapa", "Buscapelea", "Memorín", "Tubo MT/MO",
        "Saco bayas", "Poké Tele", "Tri-Ticket", "Iris-Ticket", "Té", "Misti-Ticket",
        "Ori-Ticket", "Bote polvos", "Rubí", "Zafiro", "Signo Magma", "Mapa viejo",
    ],
}

WESTERN = {
    " ":0x00, "À":0x01, "Á":0x02, "Â":0x03, "Ç":0x04, "È":0x05, "É":0x06,
    "Ê":0x07, "Ë":0x08, "Ì":0x09, "Î":0x0B, "Ï":0x0C, "Ò":0x0D, "Ó":0x0E,
    "Ô":0x0F, "Œ":0x10, "Ù":0x11, "Ú":0x12, "Û":0x13, "Ñ":0x14, "ß":0x15,
    "à":0x16, "á":0x17, "ç":0x19, "è":0x1A, "é":0x1B, "ê":0x1C, "ë":0x1D,
    "ì":0x1E, "î":0x20, "ï":0x21, "ò":0x22, "ó":0x23, "ô":0x24, "œ":0x25,
    "ù":0x26, "ú":0x27, "û":0x28, "ñ":0x29, "Í":0x5A, "í":0x6F,
    "0":0xA1, "1":0xA2, "2":0xA3, "3":0xA4, "4":0xA5, "5":0xA6,
    "6":0xA7, "7":0xA8, "8":0xA9, "9":0xAA, "!":0xAB, "?":0xAC,
    ".":0xAD, "-":0xAE, "'":0xB4, ",":0xB8, "/":0xBA,
    "Ä":0xF1, "Ö":0xF2, "Ü":0xF3, "ä":0xF4, "ö":0xF5, "ü":0xF6,
}
for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
    WESTERN[ch] = 0xBB + i
for i, ch in enumerate("abcdefghijklmnopqrstuvwxyz"):
    WESTERN[ch] = 0xD5 + i

JP = {"　":0x00, "！":0xAB, "？":0xAC, "。":0xAD, "ー":0xAE, "·":0xAF, "‥":0xB0}
_hira = "あいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわをんぁぃぅぇぉゃゅょがぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽっ"
_kata = "アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲンァィゥェォャュョガギグゲゴザジズゼゾダヂヅデドバビブベボパピプペポッ"
for i,ch in enumerate(_hira): JP[ch]=0x01+i
for i,ch in enumerate(_kata): JP[ch]=0x51+i

SANITIZE_SIG = bytes.fromhex("00 B5 00 04 01 0C AE 20 40 00 81 42 01 D8")
FREE_START = 0x600000

@dataclass
class Layout:
    record_size: int
    name_size: int
    item_id_off: int
    price_off: int
    hold_effect_off: int
    hold_param_off: int
    description_off: int
    importance_off: int
    misc_byte_off: int
    pocket_off: int
    type_off: int
    field_func_off: int
    battle_usage_off: int
    battle_func_off: int
    secondary_id_off: int

JP_LAYOUT = Layout(40,10,10,12,14,15,16,20,21,22,23,24,28,32,36)
WEST_LAYOUT = Layout(44,14,14,16,18,19,20,24,25,26,27,28,32,36,40)

@dataclass
class Analysis:
    sha1: str
    known_rom: str | None
    game_code: str
    revision: int
    region: str
    item_table: int
    record_size: int
    pointer_references: list[int]
    sanitize_item_id: int
    injection: int
    injected_table_size: int

def sha1(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()

def region_for(game_code: str) -> str:
    return {"AXPJ":"JP","AXPE":"EN","AXPD":"DE","AXPF":"FR","AXPI":"IT","AXPS":"ES"}.get(game_code, "EN")

def layout_for(game_code: str) -> Layout:
    return JP_LAYOUT if game_code == "AXPJ" else WEST_LAYOUT

def encode_name(text: str, region: str, size: int) -> bytes:
    table = JP if region == "JP" else WESTERN
    raw = bytearray()
    for ch in text:
        if ch not in table:
            raise ValueError(f"cannot encode {ch!r} in {region} item name {text!r}")
        raw.append(table[ch])
    if len(raw) >= size:
        raise ValueError(f"item name too long for {region} {size}-byte field: {text!r} -> {len(raw)} bytes")
    raw.append(0xFF)
    raw += b"\x00" * (size - len(raw))
    return bytes(raw)

def find_all(data: bytes, needle: bytes) -> list[int]:
    return [m.start() for m in re.finditer(re.escape(needle), data)]

def find_item_table(data: bytes, layout: Layout) -> int:
    one = struct.pack("<H", 1)
    out=[]
    pos=0
    while True:
        hit=data.find(one,pos)
        if hit<0: break
        base=hit-layout.item_id_off-layout.record_size
        if base>=0 and base+RUBY_SAPPHIRE_ITEMS_COUNT*layout.record_size<=len(data):
            ok=True
            for item_id in range(0,32):
                got=struct.unpack_from("<H",data,base+item_id*layout.record_size+layout.item_id_off)[0]
                if got!=item_id:
                    ok=False; break
            if ok: out.append(base)
        pos=hit+1
    out=sorted(set(out))
    if len(out)!=1:
        raise ValueError(f"gItems signature: expected one table, got {len(out)} {out}")
    return out[0]

def find_sanitize(data: bytes) -> int:
    hits=find_all(data,SANITIZE_SIG)
    if len(hits)!=1:
        raise ValueError(f"SanitizeItemId signature: expected 1, got {len(hits)} {hits}")
    return hits[0]

def find_free(data: bytes, size: int, start: int=FREE_START, alignment: int=4) -> int:
    marker=b"\xFF"*size
    pos=start
    while True:
        pos=data.find(marker,pos)
        if pos<0: raise ValueError(f"no {size:#x}-byte FF block found")
        a=(pos+alignment-1)&~(alignment-1)
        if a+size<=len(data) and data[a:a+size]==marker:
            return a
        pos+=1

def analyze(data: bytes, allow_unknown_sha1: bool=False) -> Analysis:
    if len(data)<0xC0: raise ValueError("too small for GBA ROM")
    digest=sha1(data)
    known=KNOWN_ROMS.get(digest)
    if not known and not allow_unknown_sha1:
        raise ValueError(f"unknown Sapphire SHA-1 {digest}; use --allow-unknown-sha1 after verifying the ROM")
    game_code=data[0xAC:0xB0].decode("ascii","replace")
    if not game_code.startswith("AXP"):
        raise ValueError(f"not Sapphire game code: {game_code}")
    revision=data[0xBC]
    region=region_for(game_code)
    layout=layout_for(game_code)
    table=find_item_table(data,layout)
    ptr=struct.pack("<I",ROM_BASE+table)
    refs=find_all(data,ptr)
    if len(refs)<10:
        raise ValueError(f"unexpectedly few gItems pointer refs: {len(refs)}")
    sanitize=find_sanitize(data)
    new_size=EMERALD_ITEMS_COUNT*layout.record_size
    injection=find_free(data,new_size)
    return Analysis(digest,known,game_code,revision,region,table,layout.record_size,refs,sanitize,injection,new_size)

def build_added_record(item_id: int, index: int, data: bytes, table: int, layout: Layout, region: str) -> bytes:
    rec=bytearray(layout.record_size)
    rec[:layout.name_size]=encode_name(NAMES[region][index],region,layout.name_size)
    struct.pack_into("<H",rec,layout.item_id_off,item_id)
    struct.pack_into("<H",rec,layout.price_off,0)
    rec[layout.hold_effect_off]=0
    rec[layout.hold_param_off]=0

    dummy_desc=struct.unpack_from("<I",data,table+layout.description_off)[0]
    struct.pack_into("<I",rec,layout.description_off,dummy_desc)

    rec[layout.importance_off]=2 if item_id==349 else 1
    rec[layout.misc_byte_off]=1 if item_id>=360 else 0
    eon=table+275*layout.record_size
    rec[layout.pocket_off]=data[eon+layout.pocket_off]
    rec[layout.type_off]=2 if item_id in (360,362,366) else 4

    cannot_use=struct.unpack_from("<I",data,eon+layout.field_func_off)[0]
    struct.pack_into("<I",rec,layout.field_func_off,cannot_use)
    rec[layout.battle_usage_off]=0
    struct.pack_into("<I",rec,layout.battle_func_off,0)
    rec[layout.secondary_id_off]=0
    return bytes(rec)

def patch(data: bytes, allow_unknown_sha1: bool=False):
    info=analyze(data,allow_unknown_sha1)
    layout=layout_for(info.game_code)
    rom=bytearray(data)
    old_table=data[info.item_table:info.item_table+RUBY_SAPPHIRE_ITEMS_COUNT*layout.record_size]
    new_table=bytearray(old_table)
    for i,item_id in enumerate(range(NEW_ITEM_FIRST,NEW_ITEM_LAST+1)):
        new_table += build_added_record(item_id,i,data,info.item_table,layout,info.region)
    if len(new_table)!=info.injected_table_size:
        raise AssertionError((len(new_table),info.injected_table_size))
    if rom[info.injection:info.injection+len(new_table)] != b"\xFF"*len(new_table):
        raise ValueError("injection block no longer blank")
    rom[info.injection:info.injection+len(new_table)] = new_table

    old_ptr=struct.pack("<I",ROM_BASE+info.item_table)
    new_ptr=struct.pack("<I",ROM_BASE+info.injection)
    for off in info.pointer_references:
        if rom[off:off+4]!=old_ptr: raise ValueError(f"gItems ref changed at {off:#x}")
        rom[off:off+4]=new_ptr

    imm_off=info.sanitize_item_id+6
    if rom[imm_off]!=0xAE: raise ValueError("SanitizeItemId immediate changed")
    rom[imm_off]=0xBC

    return bytes(rom), info

def make_report(data: bytes, patched: bytes|None, info: Analysis) -> dict:
    d=asdict(info)
    d["offsets_hex"]={
        "item_table":f"0x{info.item_table:X}",
        "pointer_references":[f"0x{x:X}" for x in info.pointer_references],
        "sanitize_item_id":f"0x{info.sanitize_item_id:X}",
        "injection":f"0x{info.injection:X}",
    }
    d["emerald_baseline"]={
        "items_count":EMERALD_ITEMS_COUNT,
        "max_item_id":NEW_ITEM_LAST,
        "extension_range":[NEW_ITEM_FIRST,NEW_ITEM_LAST],
        "ticket_ids":{"eon":275,"mystic":370,"aurora":371,"old_sea_map":376},
        "legacy_scalar_status":"0..348 verified equal to Emerald except behavior-level EV berry changes 153..158",
        "engine_backports_pending":[
            "Pomeg/Kelpsy/Qualot/Hondew/Grepa/Tamato signed -10 EV behavior and Emerald ReduceEV UI",
            "Powder Jar Berry Powder UI/function",
        ],
    }
    if patched is not None:
        d["output_sha1"]=sha1(patched)
        d["changed_bytes"]=sum(a!=b for a,b in zip(data,patched))
    return d

def main()->int:
    ap=argparse.ArgumentParser()
    sub=ap.add_subparsers(dest="command",required=True)
    a=sub.add_parser("analyze")
    a.add_argument("rom",type=Path); a.add_argument("--allow-unknown-sha1",action="store_true")
    p=sub.add_parser("patch")
    p.add_argument("rom",type=Path); p.add_argument("output",type=Path); p.add_argument("--allow-unknown-sha1",action="store_true")
    args=ap.parse_args(); data=args.rom.read_bytes()
    if args.command=="analyze":
        info=analyze(data,args.allow_unknown_sha1); print(json.dumps(make_report(data,None,info),indent=2,ensure_ascii=False)); return 0
    patched,info=patch(data,args.allow_unknown_sha1); args.output.write_bytes(patched)
    print(json.dumps(make_report(data,patched,info),indent=2,ensure_ascii=False)); return 0

if __name__=="__main__": raise SystemExit(main())
