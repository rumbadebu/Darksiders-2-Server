"""Splice fixed-size cert/key slots between Darksiders2.exe builds.

Both patched builds share the same layout (same size, same slot offsets), so a
slot region can be copied byte-for-byte from a donor exe into a base exe to
produce a hybrid for bisecting which embedded cert breaks the game's bundled
OpenSSL 1.0.0 ('PEM_read_bio_X509 failed' regression).

Usage:
    python splice_slot.py <base.exe> <donor.exe> <out.exe> <slot> [slot...]

Slots: leaf1 leaf2 root clientcert clientkey
"""
import hashlib
import os
import sys

SLOTS = {'leaf1': (0xed76b8, 870), 'leaf2': (0xed7a20, 860),
         'root': (0xf712d8, 1248), 'clientcert': (0xf717c0, 898),
         'clientkey': (0xf71b48, 886)}


def main():
    if len(sys.argv) < 5:
        sys.exit(__doc__)
    base, donor, out = sys.argv[1:4]
    slots = sys.argv[4:]
    for s in slots:
        if s not in SLOTS:
            sys.exit('unknown slot %r (choose from %s)' % (s, ','.join(SLOTS)))
    b = bytearray(open(base, 'rb').read())
    d = open(donor, 'rb').read()
    if len(b) != len(d):
        sys.exit('exe size mismatch: base=%d donor=%d' % (len(b), len(d)))
    for s in slots:
        off, size = SLOTS[s]
        b[off:off + size] = d[off:off + size]
        print('spliced %-11s @0x%06x +%d (from donor)' % (s, off, size))
    data = bytes(b)
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(data)
    print('wrote %s (%d bytes, sha256=%s)'
          % (out, len(data), hashlib.sha256(data).hexdigest()[:16]))


if __name__ == '__main__':
    main()