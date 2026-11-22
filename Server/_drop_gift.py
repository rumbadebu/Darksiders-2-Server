#!/usr/bin/env python3
"""Drop ONE gift from a mailbox, for the count-isolation test.

Removes the OLD axe gift (15141810279299964802, sent 2026-10-05, never
successfully received, structurally a weapon with an anomalous 00 vtag) from
playerB's mailbox, leaving the FRESH boots gift (17892332919421272059,
structurally identical to the known-good gloves) alone. Reopening the exchange
then tells us whether the 2-gift crash was the COUNT (boots alone still crashes)
or the OLD AXE (boots alone renders).

Backup already taken: server/data/cloud_backup_20261014_214017.
No server restart needed (_load_doc re-reads per request).

    python _drop_gift.py            # remove the axe
    python _drop_gift.py --restore  # put it back from the backup
"""
import json
import os
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(ROOT, 'server'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import ds2_server as srv                                     # noqa: E402

ACCT = '100000002'                       # playerB
DOC_KEY = 'TomeGift'
DROP = '15141810279299964802'           # the old axe gift
BACKUP = os.path.join(ROOT, 'server', 'data', 'cloud_backup_20261014_214017',
                      ACCT, DOC_KEY + '.json')

restore = '--restore' in sys.argv

if restore:
    live = os.path.join(ROOT, 'server', 'data', 'cloud', ACCT, DOC_KEY + '.json')
    shutil.copyfile(BACKUP, live)
    print('RESTORED %s from backup' % live)
    d = json.load(open(live, 'r', encoding='utf-8'))
    print('mailbox now holds gifts: %s'
          % [k for k in (d.get('values') or {}) if k != DOC_KEY])
    raise SystemExit(0)

doc = srv._load_doc(ACCT, DOC_KEY)
values = doc.setdefault('values', {})
gifts = [k for k in values if k != DOC_KEY]
print('mailbox before: gifts=%s' % gifts)
if DROP not in values:
    print('gift %s already absent - nothing to drop' % DROP)
    raise SystemExit(0)
values.pop(DROP, None)
upd = doc.setdefault('updaterNames', {})
upd.pop(DROP, None)
doc['lastModifiedDate'] = int(time.time())
srv._save_doc(ACCT, DOC_KEY, doc)
after = [k for k in values if k != DOC_KEY]
print('mailbox after : gifts=%s' % after)

raw = srv.Handler._cloud_body(type('H', (), {'_pos_raw': staticmethod(srv.Handler._pos_raw)})(),
                              {'accountID': ACCT}, 'execute', [DOC_KEY], None)
txt = raw.decode('utf-8', 'replace')
print('served body now: %d bytes, has_axe=%s, has_boots=%s'
      % (len(raw), DROP in txt, '17892332919421272059' in txt))
