#!/usr/bin/env python3
"""Dump a TomeGift BOD blob WITH its TYPE TAGS (journal NEXT step, 2026-10-13 16:20).

The crash is a type confusion: the gift renderer does `mov eax,[ecx]; jmp [eax+0x40]`
and eax turns out to be the LOW HALF OF A STRING HASH -> a BOD STRING record handed
to a consumer expecting an OBJECT (vtable at +0x40). This tool lays out the serialized
object graph so we can see the type tag in front of every field and spot the slot
where a scalar/string is emitted where an object is expected.

BOD framing (from gift_doc_decode.py + the AV):
    document = base64( "BOD\\xfd" + 17B header + zlib(object graph) )
    string field in the graph = [u16 len][text] and, per the AV, the field NAME is
    stored as [8B hash][u16 len][text].  So we annotate every such field with the
    8 hash bytes before it AND the type tag byte(s) before that.

We dump the FULL inflated payload hex with offsets and overlay annotations so the
tag sequence can be compared against the field-name order.
"""
import base64
import json
import pathlib
import struct
import sys
import zlib

# Known engine hashes (deterministic; tools/_hashcmp.py proved portability).
KNOWN = {
    0x73B849C1206A83B3: "Gift",
    0x8B5B7457FE236C3D: "Items",
    0x647E850169E20C8A: "GifterName",
    0x7213B0EDE1BD509D: "Location",
    0xA51F9A33DAD7AF4B: "UniqueId",
}


def inflate(b64):
    raw = base64.b64decode(b64)
    i = raw.find(b"\x78", 16)
    return raw, zlib.decompress(raw[i:])


def find_string_fields(p):
    """Return list of dicts for every [u16 len][ascii] preceded by 8 hash bytes."""
    out = []
    i = 0
    n = len(p)
    while i < n - 2:
        ln = struct.unpack_from("<H", p, i)[0]
        if 3 <= ln <= 64 and i + 2 + ln <= n:
            txt = p[i + 2:i + 2 + ln]
            if all(32 <= c < 127 for c in txt):
                h = struct.unpack_from("<Q", p, i - 8)[0] if i >= 8 else None
                out.append({
                    "len_off": i,
                    "text": txt.decode("ascii", "replace"),
                    "len": ln,
                    "hash": h,
                    "name": KNOWN.get(h, ""),
                })
                i += 2 + ln
                continue
        i += 1
    return out


def main():
    if len(sys.argv) < 2:
        print("usage: python _bodtags.py <TomeGift.json> [mapkey]")
        return 2
    path = pathlib.Path(sys.argv[1])
    d = json.loads(path.read_text())
    vals = d.get("values") or {}
    # pick the gift blob (the Qk9E... value), unless a mapkey was given
    key = sys.argv[2] if len(sys.argv) > 2 else None
    if key is None:
        for k, v in vals.items():
            if isinstance(v, str) and v.startswith("Qk9E"):
                key = k
                break
    if not key or key not in vals:
        print("no Qk9E gift blob found in %s" % path)
        return 1
    b64 = vals[key]
    raw, plain = inflate(b64)
    print("file     : %s" % path)
    print("mapkey   : %s" % key)
    print("b64      : %d chars -> %d bytes, magic=%r" % (len(b64), len(raw), raw[:4]))
    print("inflated : %d bytes" % len(plain))
    print()

    print("=== STRING FIELDS (offset | 8B hash | u16 len | text | tag-before) ===")
    print("%-7s %-18s %-4s %-4s %-32s %s" % ("off", "hash(LE)", "u16", "len", "text", "known-name"))
    fields = find_string_fields(plain)
    for f in fields:
        h = ("%016x" % f["hash"]) if f["hash"] is not None else "?"
        # the bytes immediately before the 8-byte hash = type tag region
        tagoff = f["len_off"] - 8
        tag = plain[tagoff - 2:tagoff].hex() if tagoff - 2 >= 0 else "?"
        print("%-7d %-18s %-4d %-4d %-32s %-6s  tag2=%s" % (
            f["len_off"], h, f["len_off"], f["len"], f["text"], f["name"], tag))
    print()

    # Value-type tag: the byte immediately AFTER a field's name text tells us the
    # type of that field's VALUE (07=root obj, 09=array, 04=obj, 0f=string, ...).
    # The crash is a STRING handed where an OBJECT is expected, so this column is
    # the answer to "which field is a string where the consumer wants an object".
    print("=== FIELD -> VALUE-TYPE TAG (byte after name text) ===")
    print("%-34s %-6s %s" % ("field", "vtag", "meaning"))
    VTAG = {0x07: "obj(root)", 0x09: "array", 0x04: "obj", 0x0f: "string",
            0x65: "?", 0x73: "?", 0x75: "?", 0x02: "num/bool?", 0x01: "?"}
    for f in fields:
        end = f["len_off"] + 2 + f["len"]
        vtag = plain[end] if end < len(plain) else None
        print("%-34s %-6s %s" % (f["text"],
                                 ("%02x" % vtag) if vtag is not None else "??",
                                 VTAG.get(vtag, "")))
    print()

    print("=== FULL HEX (16/row) ===")
    for off in range(0, len(plain), 16):
        chunk = plain[off:off + 16]
        hexs = " ".join("%02x" % b for b in chunk)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        print("%04x  %-47s  %s" % (off, hexs, asc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
