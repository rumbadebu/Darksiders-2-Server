"""Compare exe builds: patched out/ vs pristine backup vs frozen debug copies.
Checks the markers that our patching pipeline installs."""
import hashlib
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
TARGETS = [
    (os.path.join(BASE, 'out', 'Darksiders2.exe'), 'out-patched'),
    (os.path.join(BASE, 'out', 'Darksiders2.exe.orig'), 'out-orig'),
    (os.path.join(BASE, 'debug', 'Darksiders2.exe.original'), 'debug-orig'),
    (os.path.join(BASE, 'debug', 'Darksiders2.exe.patched'), 'debug-patched'),
]

cli_key = b'-----BEGIN RSA PRIVATE KEY-----'


def info(path):
    b = open(path, 'rb').read()
    return {
        'md5': hashlib.md5(b).hexdigest(),
        'size': len(b),
        'patched_gds(gds.thq)': b'gds.thqconnect.com\x00' in b,
        'orig_gds(ds2.gds.thq)': b'ds2.gds.thqconnect.com\x00' in b,
        'cert_pems': b.count(b'-----BEGIN CERTIFICATE-----'),
        'rsa_keys': b.count(cli_key),
        'clientCA_dn': b'THQ Connect Client CA' in b,
    }


for path, tag in TARGETS:
    try:
        d = info(path)
        print('%-9s %s' % (tag, json.dumps(d)))
    except OSError as e:
        print('%-9s MISSING: %s' % (tag, e))
    sys.stdout.flush()
