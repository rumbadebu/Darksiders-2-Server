#!/usr/bin/env python3
"""Identify the 8-byte BOD string hash seen in the AV dump.

The crash was `jmp dword ptr [eax+0x40]` with eax = 0xdad7af4b, which is the LOW
32 bits of the 8-byte hash sitting in front of the BOD string "UniqueId"
(ecx -> 4b af d7 da 33 9a 1f a5 | 08 00 | "Unique").  So the engine dereferenced
raw hash bytes as an object pointer: the string was never interned.

If the hash is a deterministic function of the text we can recompute it - and, more
importantly, tell whether a hash written by the SENDER's process is meaningful in
the RECIPIENT's process (per-process seed => not).
"""
import sys

TARGET = 0xA51F9A33DAD7AF4B  # from the AV dump, little-endian 4b af d7 da 33 9a 1f a5
M64 = 0xFFFFFFFFFFFFFFFF


def fnv1a(b, h=0xCBF29CE484222325):
    for c in b:
        h = ((h ^ c) * 0x100000001B3) & M64
    return h


def fnv1(b, h=0xCBF29CE484222325):
    for c in b:
        h = ((h * 0x100000001B3) & M64) ^ c
    return h


def djb2(b, h=5381):
    for c in b:
        h = ((h * 33) + c) & M64
    return h


def sdbm(b, h=0):
    for c in b:
        h = (c + (h << 6) + (h << 16) - h) & M64
    return h


def crc64(b):
    # ISO-HDLC-ish reflected CRC-64 (xz poly), common in game engines
    poly = 0xC96C5795D7870F42
    crc = M64 if False else 0xFFFFFFFFFFFFFFFF
    for c in b:
        crc ^= c
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return crc ^ 0xFFFFFFFFFFFFFFFF


def main():
    texts = [b"UniqueId", b"GifterName", b"playerA", b"Location", b"Gift"]
    algos = [("fnv1a64", fnv1a), ("fnv1_64", fnv1), ("djb2", djb2),
             ("sdbm", sdbm), ("crc64xz", crc64)]
    print("TARGET (UniqueId, from AV dump) = %016x" % TARGET)
    print()
    for t in texts:
        print("--- %s ---" % t.decode())
        for name, f in algos:
            v = f(t)
            mark = "  <<< MATCH" if v == TARGET else ""
            print("  %-9s %016x%s" % (name, v, mark))
        print()


if __name__ == "__main__":
    sys.exit(main())
