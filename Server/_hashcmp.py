#!/usr/bin/env python3
"""Decisive test: is the BOD string hash per-process?

The AV dump gave the 8 bytes in front of the "UniqueId" string in a blob written by
playerB's game process:  4b af d7 da 33 9a 1f a5  = a51f9a33dad7af4b

If the hash is deterministic, a blob written by a DIFFERENT process (playerA's
axe gift) must carry the SAME 8 bytes for the same text.  If it differs, the hash
uses a per-process seed and a blob written by the sender can NEVER be resolved by
the recipient - which would explain the empty inbox, the exit crash (raw hash bytes
dereferenced as a pointer) and the missing notification.
"""
import base64
import json
import pathlib
import struct
import sys
import zlib

TARGET = 0xA51F9A33DAD7AF4B  # "UniqueId", from playerB's blob (AV dump)

WANT = [b"UniqueId", b"GifterName", b"Location", b"Gift", b"Items"]


def inflate(b64):
    raw = base64.b64decode(b64)
    i = raw.find(b"\x78", 16)
    return zlib.decompress(raw[i:])


def string_hashes(p):
    """Yield (text, hash) for every [u16 len][text] preceded by 8 hash bytes."""
    out = []
    for m in _iter_strings(p):
        out.append(m)
    return out


def _iter_strings(p):
    i = 0
    n = len(p)
    while i < n - 10:
        ln = struct.unpack_from("<H", p, i)[0]
        if 3 <= ln <= 64 and i + 2 + ln <= n:
            txt = p[i + 2:i + 2 + ln]
            if all(32 <= c < 127 for c in txt):
                if i >= 8:
                    h = struct.unpack_from("<Q", p, i - 8)[0]
                    yield (txt, h)
                i += 2 + ln
                continue
        i += 1


def main():
    print("TARGET from AV dump (playerB's process): UniqueId = %016x" % TARGET)
    print()
    found = False
    for path, label in _sources():
        try:
            d = json.loads(pathlib.Path(path).read_text())
        except Exception as e:
            print("  %-46s unreadable (%s)" % (label, e))
            continue
        for k, v in (d.get("values") or {}).items():
            if k == "TomeGift" or not isinstance(v, str) or not v.startswith("Qk9E"):
                continue
            try:
                p = inflate(v)
            except Exception:
                continue
            print("%s  gift=%s  inflated=%d" % (label, k, len(p)))
            for txt, h in string_hashes(p):
                if txt in WANT:
                    same = "  <<< SAME AS TARGET" if (txt == b"UniqueId" and h == TARGET) else ""
                    print("    %-14s %016x%s" % (txt.decode(), h, same))
                    found = True
            print()
    if not found:
        print("no gift blobs left in the store - restore from a backup first")
    return 0


def _sources():
    base = pathlib.Path("server/data")
    out = []
    for p in sorted(base.glob("cloud*/[0-9]*/TomeGift.json")):
        out.append((str(p), str(p.parent.parent.name + "/" + p.parent.name)))
    for p in sorted(base.glob("cloud_backup*/[0-9]*/TomeGift.json")):
        out.append((str(p), "BK:" + p.parent.parent.parent.name + "/" + p.parent.name))
    return out


if __name__ == "__main__":
    sys.exit(main())
