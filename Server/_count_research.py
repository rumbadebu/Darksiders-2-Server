#!/usr/bin/env python3
"""Research the COUNT hypothesis: does the crash come from 2 gifts, or one gift?

No game, no edits - purely offline, using the server's REAL renderer so the
served bytes cannot drift from what the client receives.

Shows:
  1. the exact document served for 1 gift vs 2 gifts (armed docshape)
  2. a full field-by-field diff of every gift blob (gloves/boots/axe) so a
     malformed field can be ruled in or out
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'server'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import ds2_server as srv                                     # noqa: E402
import _bodtags as bt                                        # noqa: E402

DOC_KEY = 'TomeGift'
GLOVES = ('server/data/cloud_backup_20261009_preifp/100000001/TomeGift.json',
          '15717548710165239937')
BOOTS = ('server/data/cloud/100000002/TomeGift.json', '17892332919421272059')
AXE = ('server/data/cloud/100000002/TomeGift.json', '15141810279299964802')


def blob(spec):
    import json
    path, key = spec
    with open(os.path.join(ROOT, path), 'r', encoding='utf-8') as f:
        return (json.load(f).get('values') or {}).get(key)


def served(values):
    spec = '[%E%] => {"%K%":"%V%","data":1}'
    return srv.render_doc(values, spec)


print('=== WHAT THE SERVER SERVES (armed docshape) ===')
print('--- 1 gift (gloves) ---')
one = {'15717548710165239937': blob(GLOVES)}
d1 = served(one)
print(d1[:120] + ('...' if len(d1) > 120 else ''))
print('len=%d  entries=%d' % (len(d1), d1.count('"data":1')))
print()
print('--- 2 gifts (axe + boots) ---')
two = {'15141810279299964802': blob(AXE), '17892332919421272059': blob(BOOTS)}
d2 = served(two)
print(d1 and d2)  # full, so the list-of-two structure is visible
print('len=%d  entries=%d' % (len(d2), d2.count('"data":1')))
print()

print('=== FIELD DIFF: gloves vs boots vs axe (name -> vtag) ===')


def fields(spec):
    _, plain = bt.inflate(blob(spec))
    out = []
    for f in bt.find_string_fields(plain):
        end = f['len_off'] + 2 + f['len']
        vtag = plain[end] if end < len(plain) else None
        out.append((f['text'], '%02x' % vtag if vtag is not None else '??'))
    return out


g, b, a = fields(GLOVES), fields(BOOTS), fields(AXE)
print('gloves: %d fields   boots: %d fields   axe: %d fields'
      % (len(g), len(b), len(a)))
gn = [x[0] for x in g]
bn = [x[0] for x in b]
an = [x[0] for x in a]
print('boots-only names : %s' % [x for x in bn if x not in gn])
print('gloves-only names: %s' % [x for x in gn if x not in bn])
print('axe-only names   : %s' % [x for x in an if x not in gn])
print('axe-only vs boots: %s' % [x for x in an if x not in bn])
print()
print('gloves vtags:', ' '.join(t for _, t in g))
print('boots  vtags:', ' '.join(t for _, t in b))
print('axe    vtags:', ' '.join(t for _, t in a))
