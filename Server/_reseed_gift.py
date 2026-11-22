#!/usr/bin/env python3
"""Restore the recipient's (100000001 / playerA) received-gift blob.

The live store's TomeGift.json was emptied during the 2026-10-13 session (only
the empty sentinel remains), so the gift walk never fires and no receive test is
possible. This copies the gift-id -> base64-BOD-blob entry back from the
cloud_backup_20261009_preifp snapshot, leaving everything else in the live doc
untouched. No server restart needed: ds2_server._load_doc re-reads the file from
disk on every request.

    python _reseed_gift.py            # restore from the preifp snapshot
    python _reseed_gift.py --dry-run  # show what would change, write nothing
"""
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'server'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import ds2_server as srv                                     # noqa: E402

ACCT = '100000001'                       # playerA (recipient)
DOC_KEY = 'TomeGift'
GIFT_ID = '15717548710165239937'         # the gloves gift from playerB
SRC = os.path.join(ROOT, 'server', 'data', 'cloud_backup_20261009_preifp',
                   ACCT, DOC_KEY + '.json')

dry = '--dry-run' in sys.argv

with open(SRC, 'r', encoding='utf-8') as f:
    src = json.load(f)
blob = (src.get('values') or {}).get(GIFT_ID)
if not (isinstance(blob, str) and blob.startswith('Qk9E')):
    print('!! snapshot %s has no Qk9E blob under %s' % (SRC, GIFT_ID))
    raise SystemExit(1)

doc = srv._load_doc(ACCT, DOC_KEY)
values = doc.setdefault('values', {})
before = values.get(GIFT_ID)
print('recipient %s %s: gift %s currently %s'
      % (ACCT, DOC_KEY, GIFT_ID,
         ('present, %d chars' % len(before)) if isinstance(before, str) and before
         else 'ABSENT/empty'))
print('snapshot blob: %d chars (magic base64 of BOD)' % len(blob))

if before == blob:
    print('already seeded - nothing to do')
    raise SystemExit(0)
if dry:
    print('DRY RUN: would set values[%s] = <blob> and updaterNames; not writing'
          % GIFT_ID)
    raise SystemExit(0)

values[GIFT_ID] = blob
doc.setdefault('updaterNames', {})[GIFT_ID] = doc.get('lastModifiedName') or 'playerB'
doc['lastModifiedName'] = doc.get('lastModifiedName') or 'playerB'
doc['lastModifiedDate'] = int(time.time())
srv._save_doc(ACCT, DOC_KEY, doc)
_, path = srv._cloud_path(ACCT, DOC_KEY)
print('WROTE %s' % path)

# Verify by re-reading what the server will actually serve.
raw = srv.Handler._cloud_body(type('H', (), {'_pos_raw': staticmethod(srv.Handler._pos_raw)})(),
                              {'accountID': ACCT}, 'execute', [DOC_KEY], None)
txt = raw.decode('utf-8', 'replace')
print('served body now: %d bytes, contains blob=%s, giftId=%s'
      % (len(raw), GIFT_ID and (blob[:16] in txt), GIFT_ID in txt))
