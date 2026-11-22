#!/usr/bin/env python3
"""Print the full vtag sequence for a gift, for direct comparison."""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'server'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import _bodtags as bt                                        # noqa: E402

path, key = sys.argv[1], sys.argv[2]
d = json.load(open(os.path.join(ROOT, path), encoding='utf-8'))
vals = d.get('values') or {}
if key not in vals:
    print('KEY %s not in %s; present keys: %s' % (key, path, list(vals)))
    raise SystemExit(1)
_, plain = bt.inflate(vals[key])
fields = bt.find_string_fields(plain)
seq = []
for f in fields:
    end = f['len_off'] + 2 + f['len']
    vtag = plain[end] if end < len(plain) else None
    seq.append('%02x' % vtag if vtag is not None else '??')
label = os.path.basename(os.path.dirname(path))
print('%s (%s): %d fields' % (key, label, len(fields)))
print('vtags: ' + ' '.join(seq))
# flag any 00 tag (the suspected type-confusion slot)
zeros = [f['text'] for f in fields
         if (lambda e: e < len(plain) and plain[e] == 0x00)(
             f['len_off'] + 2 + f['len'])]
print('fields followed by a 00 vtag: %s' % (zeros if zeros else 'NONE'))

