"""Decode a captured TomeGift document (the grammar the exchange actually uses).

WHAT THE GRAMMAR IS (proved 2026-09-28 21:47:47, the first gift the game itself
ever wrote - see VICTORY_RU.md):

    GET /thqconnect/cloud/execute
        ?profileNames=/steam/76561198000000002
        &ops=MAP_SET&docKeys=TomeGift
        &keys=5394434090946791192          <- one MAP key per gift (the gift id)
        &values=Qk9E/QQBAQAeAAAAJgAAAJ4D...<- base64 of the frame below

    document = base64( "BOD" + header + zlib(serialized object graph) )
                   ^^^                  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
       0xf8ee09 in the exe (next to `STL`)   zlib stream (`78 9c`/`78 da`)
    frame header = 04 01 01 00 | 30 | 38 | 926 (three little-endian dwords; the
    last is the INFLATED size) - so the cloud client's base64 decoder (0xb4394c)
    hands `BOD`+zlib to the deserializer that builds the gift objects, and
    `doc_content`'s only-non-empty-key fallback is what makes the record come
    back under the numeric MAP key instead of the docKey.

Usage:
    python tools/gift_doc_decode.py                 # newest capture in the log
    python tools/gift_doc_decode.py --text <base64> # decode a value directly
    python tools/gift_doc_decode.py --strings 40    # ... more strings
"""
import base64
import os
import sys
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import gift_capture as cap                                     # noqa: E402

LOG = cap.LOG
DOC_KEY = cap.DOC_KEY


def strings_of(blob, minimum=4):
    """Printable runs in `blob` (the object graph's names live here)."""
    runs, cur = [], ''
    for byte in blob:
        if 32 <= byte < 127:
            cur += chr(byte)
        else:
            if len(cur) >= minimum:
                runs.append(cur)
            cur = ''
    if len(cur) >= minimum:
        runs.append(cur)
    return runs


def layers(doc):
    """Peel a captured document -> (magic, frame_header, payload, strings).

    `payload`/`strings` are None when a layer does not parse, so callers can
    report exactly how far a document got.
    """
    try:
        blob = base64.b64decode(doc, validate=True)
    except Exception:                               # noqa: BLE001 - reported as None
        return None, None, None, None
    magic = blob[:4]
    body = blob[4:]
    at = -1
    for marker in (b'\x78\x9c', b'\x78\xda', b'\x78\x01'):
        found = body.find(marker)
        if found >= 0 and (at < 0 or found < at):
            at = found
    if at < 0:
        return magic, None, None, None
    try:
        plain = zlib.decompress(body[at:])
    except zlib.error:
        return magic, body[:at], None, None
    return magic, body[:at], plain, strings_of(plain)


def decode(doc, limit=25):
    """Peel base64 -> `BOD` frame -> zlib payload; prints every layer."""
    print('document   : %d chars' % len(doc))
    magic, header, plain, runs = layers(doc)
    if magic is None:
        print('!! not base64')
        return None
    blob = base64.b64decode(doc)
    print('[1] base64 -> %d bytes' % len(blob))
    print('    magic     : %r' % magic)
    if magic != b'BOD\xfd':
        print('    (expected BOD\\xfd - other versions may differ)')
    if header is None:
        print('    no zlib stream found; head: %s' % blob[:48].hex())
        return blob
    print('    frame hdr : %s' % header.hex())
    if plain is None:
        print('!! inflate failed; head: %s' % (blob[4 + len(header):][:48].hex()))
        return blob
    print('[2] inflate -> %d bytes' % len(plain))
    print('    head hex  : %s' % plain[:48].hex())
    print('    strings (%d):' % len(runs))
    for text in runs[:limit]:
        print('      %s' % text)
    return plain


def main():
    doc = None
    limit = 25
    argv = list(sys.argv[1:])
    i = 0
    while i < len(argv):
        arg = argv[i]
        i += 1
        if arg == '--text':
            if i >= len(argv):
                print('--text needs the base64 document')
                return 2
            doc = argv[i]
            i += 1
        elif arg == '--strings':
            if i >= len(argv):
                print('--strings needs a count')
                return 2
            limit = int(argv[i])
            i += 1
        else:
            print('unknown argument %r' % arg)
            return 2
    if doc is None:
        hits = [r for r in cap.cloud_requests(LOG) if r['docs'].get(DOC_KEY)]
        if not hits:
            print('no %s document captured in %s' % (DOC_KEY, LOG))
            return 1
        src = hits[-1]
        doc = src['docs'][DOC_KEY]
        print('newest capture: [%s] %s' % (src['ts'], src['method']))
        key = ''
        for field in src['raw'].split('&'):
            if field.startswith('keys='):
                key = field[5:]
        print('map key (gift id): %s' % key)
    decode(doc, limit)
    return 0


if __name__ == '__main__':
    sys.exit(main())
