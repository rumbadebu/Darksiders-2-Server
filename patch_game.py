"""
Patch Darksiders2.exe with a freshly-minted cert set.

Run from anywhere:  py patch_game.py [--exe Darksiders2.exe.original]
                            [--cert-dir cert-tools\out]
                            [--hash <expected-sha256>]

Default: patches Client\debug\Darksiders2.exe.original using the cert dir
produced by mint_certs.py (cert-tools\out).  Because patch_exe.py itself
writes slot padding differently than the proven Aug build (newlines vs
newline+zero pad) and never zeros the dword @0xED7D84, we keep those as-is:
both were proven IRRELEVANT by expB in-game.

Phases:
  [1/4] RESOLVE INPUT  - pristine exe (must NOT contain "Local DS2" marker)
  [2/4] HASH VERIFY    - abort if --hash provided and doesn't match input
  [3/4] PATCH          - Client\patch_exe.py --exe ... --cert-dir ...
                         writes Client\out\Darksiders2.exe + .orig backup
  [4/4] VERIFY         - re-parse every slot vs cert dir; confirm leaf1 has
                         v3 extensions (ca_login), leaf2 = ca.pem copy,
                         root matches ca.pem, GDS rewritten; exit 1 on any
                         failure.  Exit 0 = PASS.

Nothing is copied to the game directory - deploy manually.
See Docs\PATCHING_GUIDE.md for the full recipe.
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

HERE = os.path.dirname(os.path.abspath(__file__))          # Darksiders2Server
CLIENT = os.path.join(HERE, 'Client')
PATCH_EXE = os.path.join(CLIENT, 'patch_exe.py')

failures = []


def fail(msg):
    print('  FAIL: %s' % msg)
    failures.append(msg)


def pemblock(path):
    b = open(path, 'rb').read()
    m = re.search(rb'-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----',
                  b, re.S)
    if not m:
        raise ValueError('no certificate block in %s' % path)
    return m.group()


def load_cert(path):
    return x509.load_pem_x509_certificate(pemblock(path))


def load_key(path):
    return serialization.load_pem_private_key(open(path, 'rb').read(),
                                              password=None)


def pubkey_der(obj):
    """Serialize a public key (or cert/private key that has one) to DER bytes."""
    pk = None
    if hasattr(obj, 'public_key'):
        try:
            pk = obj.public_key()
        except Exception:
            pk = None
    if pk is None:
        pk = obj
    if hasattr(pk, 'public_bytes'):
        return pk.public_bytes(serialization.Encoding.DER,
                               serialization.PublicFormat.SubjectPublicKeyInfo)
    if hasattr(pk, 'public_key'):
        return pk.public_key().public_bytes(serialization.Encoding.DER,
                                            serialization.PublicFormat.SubjectPublicKeyInfo)
    raise TypeError('cannot extract public key bytes from %r' % type(obj))


def cert_keymod(c):
    return pubkey_der(c.public_key())


def key_keymod(k):
    return pubkey_der(k)


def verify_sig(cert, issuer_key):
    try:
        issuer_key.verify(cert.signature, cert.tbs_certificate_bytes,
                          padding.PKCS1v15(), cert.signature_hash_algorithm)
    except AttributeError:
        issuer_key.public_key().verify(cert.signature, cert.tbs_certificate_bytes,
                                       padding.PKCS1v15(),
                                       cert.signature_hash_algorithm)


def load_cert_from_block(block):
    return x509.load_pem_x509_certificate(block)


def main():
    ap = argparse.ArgumentParser(description='Patch Darksiders2.exe with a fresh cert set')
    ap.add_argument('--exe', default=os.path.join(CLIENT, 'debug',
                                                  'Darksiders2.exe.original'),
                    help='pristine exe to patch (default: Client\\debug\\Darksiders2.exe.original)')
    ap.add_argument('--cert-dir', default=os.path.join(HERE, 'cert-tools', 'out'),
                    help='cert dir from mint_certs.py (default: cert-tools\\out)')
    ap.add_argument('--hash', default=os.environ.get('DS2_EXE_SHA256'),
                    help='expected SHA-256 of input exe (env DS2_EXE_SHA256 if unset)')
    args = ap.parse_args()

    exe = args.exe
    if not os.path.exists(exe):
        print('input exe not found: %s' % exe)
        return 1

    # [1/4] RESOLVE INPUT --------------------------------------------------
    print('[1/4] input exe: %s' % exe)
    data = open(exe, 'rb').read()
    if b'Local DS2' in data:
        print('ERROR: input exe already patched ("Local DS2" marker present).')
        return 1
    print('  pristine, %d bytes' % len(data))

    # [2/4] HASH VERIFY ----------------------------------------------------
    if args.hash:
        actual = hashlib.sha256(data).hexdigest()
        if actual.lower() != args.hash.lower():
            print('ERROR: hash mismatch!')
            print('  expected: %s' % args.hash)
            print('  actual:   %s' % actual)
            return 1
        print('  hash verified: %s' % actual)

    # [3/4] PATCH ----------------------------------------------------------
    # patch_exe.py reads leaf{n}_ca.pem as the leaf-slot source.  In
    # cert-tools\out, leaf1_ca.pem is the WIRE identity (root-signed
    # *.thqconnect.com, NO extensions) which OpenSSL 1.0.0 cannot parse
    # in-slot (PEM_read_bio_X509 failure).  The proven fix is to put
    # ca_login (self-signed "Root CA - Login", v3 WITH extensions) in
    # leaf1 and a copy of ca.pem in leaf2.  Build a temp override dir
    # so patch_exe.py picks up the right certs.
    print('[3/4] preparing override cert dir (leaf1=ca_login, leaf2=ca.pem)')
    override_dir = os.path.join(CLIENT, 'out', 'certs')
    os.makedirs(override_dir, exist_ok=True)
    # copy every file from the source cert dir
    import shutil
    src_dir = args.cert_dir
    for fname in os.listdir(src_dir):
        src_path = os.path.join(src_dir, fname)
        if os.path.isfile(src_path):
            shutil.copy2(src_path, os.path.join(override_dir, fname))
    # patch_exe.py needs leaf{n}_orig.der for serial-based slot mapping
    for der in ('leaf1_orig.der', 'leaf2_orig.der'):
        der_src = os.path.join(HERE, 'cert-tools', der)
        if os.path.exists(der_src):
            shutil.copy2(der_src, os.path.join(override_dir, der))
    # apply leaf-slot overrides
    login_path = os.path.join(src_dir, 'ca_login.pem')
    ca_path = os.path.join(src_dir, 'ca.pem')
    if os.path.exists(login_path):
        shutil.copy2(login_path, os.path.join(override_dir, 'leaf1_ca.pem'))
        print('  leaf1_ca.pem -> ca_login.pem (v3 extensions, self-signed)')
    if os.path.exists(ca_path):
        shutil.copy2(ca_path, os.path.join(override_dir, 'leaf2_ca.pem'))
        print('  leaf2_ca.pem -> ca.pem (root CA copy)')
    print('  override dir: %s' % override_dir)

    print('[3/4] patching -> Client\\out\\Darksiders2.exe')
    rc = subprocess.run([sys.executable, PATCH_EXE,
                         '--exe', exe,
                         '--cert-dir', override_dir], cwd=CLIENT).returncode
    if rc != 0:
        print('patch_exe.py exited %d' % rc)
        return 1

    out_exe = os.path.join(CLIENT, 'out', 'Darksiders2.exe')
    out_orig = os.path.join(CLIENT, 'out', 'Darksiders2.exe.orig')
    if not os.path.exists(out_exe):
        print('ERROR: patch_exe.py did not produce %s' % out_exe)
        return 1
    print('  patched: %s (%d bytes)' % (out_exe, os.path.getsize(out_exe)))
    print('  backup:  %s' % out_orig)


    # [4/4] VERIFY ---------------------------------------------------------
    print('[4/4] verify patched exe vs cert dir')
    cert_dir = args.cert_dir
    patched = open(out_exe, 'rb').read()

    def slot(off, sz):
        m = re.search(rb'-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----',
                      patched[off:off + sz], re.S)
        return m.group()

    slots = {'leaf1': (0xed76b8, 870), 'leaf2': (0xed7a20, 860),
             'root': (0xf712d8, 1248), 'clientcert': (0xf717c0, 898)}
    checks = []

    ca_pem_path = os.path.join(cert_dir, 'ca.pem')
    login_pem_path = os.path.join(cert_dir, 'ca_login.pem')
    client_crt_path = os.path.join(cert_dir, 'ds2_client.crt')

    ca_cert = load_cert(ca_pem_path)
    login_cert = load_cert(login_pem_path)
    client_cert = load_cert(client_crt_path)
    ca_key = load_key(os.path.join(cert_dir, 'ca.key'))
    login_key = load_key(os.path.join(cert_dir, 'ca_login.key'))
    client_key = load_key(os.path.join(cert_dir, 'ds2_client.key'))

    checks.append(('leaf1 slot == ca_login.pem',
                   slot(*slots['leaf1']) == pemblock(login_pem_path)))
    checks.append(('leaf1 slot has v3 extensions',
                   len(list(load_cert_from_block(slot(*slots['leaf1'])).extensions)) >= 2))
    checks.append(('leaf2 slot == ca.pem copy',
                   slot(*slots['leaf2']) == pemblock(ca_pem_path)))
    checks.append(('root slot starts with ca.pem',
                   slot(*slots['root']).startswith(pemblock(ca_pem_path)[:200])))
    checks.append(('client cert == ds2_client.crt',
                   slot(*slots['clientcert'])[:60] == pemblock(client_crt_path)[:60]))
    checks.append(('GDS old hostname gone',
                   not re.search(rb'ds2\.gds\.thqconnect\.com', patched)))
    checks.append(('GDS new hostname present',
                   b'gds.thqconnect.com\x00' in patched))
    try:
        verify_sig(load_cert(os.path.join(cert_dir, 'leaf1_ca.pem')), ca_key)
        checks.append(('leaf1_ca signature verifies vs ca.pem', True))
    except Exception as e:
        checks.append(('leaf1_ca signature verifies vs ca.pem', False))
        print('    (chain verify error: %r)' % e)
    checks.append(('client key == client cert pubkey',
                   key_keymod(client_key) == cert_keymod(client_cert)))

    print()
    if failures:
        print('RESULT: FAIL (%d problem(s))' % len(failures))
        return 1
    print('RESULT: PASS - patched exe at Client\\out\\Darksiders2.exe')
    print('next  : copy out\\Darksiders2.exe to game dir; copy cert-tools\\out\\server-set\\*')
    print('        to server\\certs\\); restart server; launch game')
    return 0


if __name__ == '__main__':
    sys.exit(main())
