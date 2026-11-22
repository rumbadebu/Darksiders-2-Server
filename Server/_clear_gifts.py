"""Back up the cloud doc store, then clear every gift from every TomeGift doc.

    python tmp_clear_gifts.py backup   # copy the whole store aside, then clear
    python tmp_clear_gifts.py show     # list what gifts each account holds

Only the TomeGift documents are touched - GameToWeb and zone_12_loot are left
alone (those carry save data, not gifts). Each doc is rewritten with its
non-gift keys intact so the store shape the server expects is unchanged; the
`TomeGift` sentinel key is kept so an empty inbox still serializes.
"""
import glob
import json
import os
import shutil
import sys

CLOUD = os.path.join('server', 'data', 'cloud')
BACKUP = os.path.join('server', 'data', 'cloud_backup_20261005')


def gift_keys(doc):
    return [k for k in (doc.get('values') or {}) if k not in ('TomeGift', '')]


def show():
    for path in sorted(glob.glob(os.path.join(CLOUD, '*', 'TomeGift.json'))):
        acct = os.path.basename(os.path.dirname(path))
        keys = gift_keys(json.load(open(path)))
        if keys:
            print('%s  %d gift(s)  %s' % (acct, len(keys), ', '.join(keys)))


def backup():
    if not os.path.isdir(BACKUP):
        shutil.copytree(CLOUD, BACKUP)
        print('backed up %s -> %s' % (CLOUD, BACKUP))
    else:
        print('backup already exists, left alone: %s' % BACKUP)


def clear():
    for path in sorted(glob.glob(os.path.join(CLOUD, '*', 'TomeGift.json'))):
        acct = os.path.basename(os.path.dirname(path))
        doc = json.load(open(path))
        keys = gift_keys(doc)
        if not keys:
            continue
        doc['values'] = {k: v for k, v in doc['values'].items()
                         if k in ('TomeGift', '')}
        doc['updaterNames'] = {k: v for k, v in (doc.get('updaterNames') or {}).items()
                               if k in ('TomeGift', '')}
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(doc, fh, indent=1)
        print('cleared %s: dropped %s' % (acct, ', '.join(keys)))


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'show'
    if what == 'backup':
        backup()
        clear()
        show()
    elif what == 'show':
        show()
    else:
        sys.exit(__doc__)