"""Confirm the armed docshape.txt / docslot.txt active lines, exactly as the
server parses them (strip '#' comments, drop blanks), and render the docslot
override dict so we know which record slots the next login GET,GET will carry.
No server import needed - pure file parse.
"""
import io
import json
import os

ROOT = os.path.join('e:', os.sep, 'tmp', 'BrokenCopies', 'Darksiders 2 server')
SERVER = os.path.join(ROOT, 'server')


def active(path):
    with io.open(path, encoding='utf-8') as fh:
        lines = [l.strip().split('#')[0].strip() for l in fh]
    return [s for s in lines if s]


def parse_slot(spec):
    out = {}
    for item in spec.replace(',', ' ').split():
        if '=' not in item:
            continue
        slot, _, raw = item.partition('=')
        try:
            idx = int(slot)
        except ValueError:
            continue
        try:
            out[idx] = json.loads(raw)
        except ValueError:
            out[idx] = raw
    return out


def main():
    ds = os.path.join(SERVER, 'docshape.txt')
    dl = os.path.join(SERVER, 'docslot.txt')
    shape = active(ds)
    slots = active(dl)
    print('docshape.txt ACTIVE (%d):' % len(shape))
    for s in shape:
        print('   ', s)
    print('docslot.txt  ACTIVE (%d):' % len(slots))
    for s in slots:
        print('   ', s, '->', parse_slot(s))

    # simulate the record the server builds for the login GET,GET gift doc
    store_doc = {'15717548710165239937': 'Qk9E/QQBAQAaAAAAJgAAANQC...'}  # no meta keys
    text = '[{"key":"15717548710165239937","data":1}]'
    rec = [
        int(store_doc.get('docStatus') or 0),
        text,
        int(store_doc.get('docPolicyBits') or 0),
        int(store_doc.get('lastModifiedDate') or 0),
        store_doc.get('lastModifiedName') or '',
    ]
    print('\nBASE record (no override):', rec)
    if slots:
        for idx, val in parse_slot(slots[0]).items():
            if 0 <= idx < len(rec):
                rec[idx] = val
        print('AFTER docslot override :', rec)
    print('\nslot0 status =', rec[0], '| slot3 time =', rec[3],
          '| slot4 name =', repr(rec[4]))


if __name__ == '__main__':
    main()
