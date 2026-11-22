"""
Mint a complete, coherent DS2 cert set into cert-tools\\out\\.

Run from anywhere:  py mint_certs.py     (all paths relative to this file)

Phases:
  [1/5] BACKUP  - if cert-tools\\out has content, move it all into
                  cert-tools\\backups\\out_<timestamp>\\  (then out is empty)
  [2/5] CLEAR   - ensure an empty out\\ exists
  [3/5] MINT    - run the cert-tools scripts in succession:
                  build_certs -> mint_root2 -> clone_leaf -> mint_leaf_ca
                  -> mint_login_ca -> build_client_kit
                  (aborts on first failure)
  [4/5] VERIFY  - parse every created cert: sizes fit their exe slots,
                  keys match certs, wire identities chain-verify against
                  the root, login cert carries v3 extensions
  [5/5] OUTPUT  - assemble out\\server-set\\ (everything the server loads,
                  incl. leaf{n}_slot.pem = wire identity padded to slot size)

Nothing is copied to the server or game directories - deploy manually.
Exit code 0 = PASS, 1 = FAIL.  See Docs\\PATCHING_GUIDE.md for the recipe.
"""
import datetime
import os
import re
import shutil
import subprocess
import sys

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

HERE = os.path.dirname(os.path.abspath(__file__))          # Darksiders2Server
CT = os.path.join(HERE, 'cert-tools')
OUT = os.path.join(CT, 'out')
BACKUPS = os.path.join(CT, 'backups')

MINT_SCRIPTS = ('build_certs.py', 'mint_root2.py', 'clone_leaf.py',
                'mint_leaf_ca.py', 'mint_login_ca.py', 'build_client_kit.py')
# slot budgets: root 1248, leaf1 870, leaf2 860, clientcert 898, clientkey 886
BUDGETS = {'ca.pem': 1248, 'ca_login.pem': 870,
           'leaf1_ca.pem': 870, 'leaf2_ca.pem': 860,
           'ds2_client.crt': 898, 'ds2_client.key': 886}

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
    # obj may be a Certificate, a PublicKey, or a PrivateKey
    pk = None
    if hasattr(obj, 'public_key'):
        try:
            pk = obj.public_key()
        except Exception:
            pk = None
    if pk is None:
        pk = obj  # already a public key
    # pk may be a key interface or a raw backend object
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


def main():
    stamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')

    # [1/5] BACKUP ---------------------------------------------------------
    print('[1/5] backup')
    if os.path.isdir(OUT) and os.listdir(OUT):
        dest = os.path.join(BACKUPS, 'out_%s' % stamp)
        os.makedirs(dest, exist_ok=True)
        for name in os.listdir(OUT):
            shutil.move(os.path.join(OUT, name), os.path.join(dest, name))
        print('  moved %d item(s) -> %s' % (len(os.listdir(dest)), dest))
    else:
        print('  out\\ empty or absent - nothing to back up')

    # [2/5] CLEAR ----------------------------------------------------------
    print('[2/5] clear')
    if os.path.isdir(OUT):
        shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    print('  out\\ ready (%s)' % OUT)

    # [3/5] MINT -----------------------------------------------------------
    print('[3/5] mint (running %d scripts in succession)' % len(MINT_SCRIPTS))
    for script in MINT_SCRIPTS:
        path = os.path.join(CT, script)
        if not os.path.exists(path):
            fail('mint script missing: %s' % script)
            return finish()
        print('  -> %s' % script)
        rc = subprocess.run([sys.executable, path], cwd=CT).returncode
        if rc != 0:
            fail('%s exited with code %d - aborting' % (script, rc))
            return finish()

    # [4/5] VERIFY ---------------------------------------------------------
    print('[4/5] verify')
    required = list(BUDGETS) + ['ca.key', 'ca_login.key', 'server.key',
                                'client_local_ca.key', 'ds2_client.key',
                                'leaf1_clone.pem', 'leaf1_clone.key',
                                'leaf2_clone.pem', 'leaf2_clone.key']
    for name in required:
        if not os.path.exists(os.path.join(OUT, name)):
            fail('missing output: %s' % name)
    # orig leaf DERs live in cert-tools/ (inputs to mint_leaf_ca), not out/
    for name in ('leaf1_orig.der', 'leaf2_orig.der'):
        if not os.path.exists(os.path.join(CT, name)):
            fail('missing input: %s' % name)
    if failures:
        return finish()

    for name, budget in BUDGETS.items():
        size = os.path.getsize(os.path.join(OUT, name))
        if size > budget:
            fail('%s = %d B exceeds slot budget %d B' % (name, size, budget))
        else:
            print('  %-18s %3d/%d B fits' % (name, size, budget))

    ca = load_cert(os.path.join(OUT, 'ca.pem'))
    ca_key = load_key(os.path.join(OUT, 'ca.key'))
    if cert_keymod(ca) != key_keymod(ca_key):
        fail('ca.pem / ca.key mismatch')

    login = load_cert(os.path.join(OUT, 'ca_login.pem'))
    login_key = load_key(os.path.join(OUT, 'ca_login.key'))
    ext_names = [e.oid._name for e in login.extensions]
    if 'basicConstraints' not in ext_names:
        fail('ca_login lacks basicConstraints (v3 ext REQUIRED in leaf1 slot)')
    if cert_keymod(login) != key_keymod(login_key):
        fail('ca_login.pem / ca_login.key mismatch')
    if login.issuer != login.subject:
        fail('ca_login is not self-signed')
    print('  ca_login: self-signed, exts=%s' % ext_names)

    for name in ('leaf1', 'leaf2'):
        wire = load_cert(os.path.join(OUT, '%s_ca.pem' % name))
        orig = x509.load_der_x509_certificate(
            open(os.path.join(CT, '%s_orig.der' % name), 'rb').read())
        clone_key = load_key(os.path.join(OUT, '%s_clone.key' % name))
        if wire.issuer != ca.subject:
            fail('%s_ca issuer != ca.pem subject' % name)
        else:
            try:
                verify_sig(wire, ca_key)
                print('  %s_ca: chains to ca.pem (signature OK)' % name)
            except Exception as e:
                fail('%s_ca signature does NOT verify: %r' % (name, e))
        if wire.subject != orig.subject or wire.serial_number != orig.serial_number:
            fail('%s_ca identity != original leaf (subject/serial)' % name)
        if cert_keymod(wire) != key_keymod(clone_key):
            fail('%s_ca pubkey != %s_clone.key (server TLS key)' % (name, name))
    print('  wire identities: original subject+serial, clone-key paired')

    srv = load_cert(os.path.join(OUT, 'server.crt'))
    if cert_keymod(srv) != key_keymod(load_key(os.path.join(OUT, 'server.key'))):
        fail('server.crt / server.key mismatch')

    cli_ca = load_cert(os.path.join(OUT, 'client_local_ca.pem'))
    cli = load_cert(os.path.join(OUT, 'ds2_client.crt'))
    if cli.issuer != cli_ca.subject:
        fail('ds2_client.crt not issued by client_local_ca')
    else:
        try:
            verify_sig(cli, load_key(os.path.join(OUT, 'client_local_ca.key')))
            print('  client kit: chain OK (client_local_ca -> CN=DS2)')
        except Exception as e:
            fail('ds2_client signature does NOT verify: %r' % e)
    if cert_keymod(cli) != key_keymod(load_key(os.path.join(OUT, 'ds2_client.key'))):
        fail('ds2_client.crt / ds2_client.key mismatch')

    # [5/5] OUTPUT: server-set --------------------------------------------
    if failures:
        return finish()
    print('[5/5] output: assemble out\\server-set\\')
    sdir = os.path.join(OUT, 'server-set')
    os.makedirs(sdir, exist_ok=True)
    for name in ('ca.pem', 'ca.key', 'server.crt', 'server.key',
                 'server-chain.pem', 'client_local_ca.pem',
                 'client_local_ca.key', 'ds2_client.crt', 'ds2_client.key',
                 'leaf1_ca.pem', 'leaf2_ca.pem',
                 'leaf1_clone.pem', 'leaf1_clone.key',
                 'leaf2_clone.pem', 'leaf2_clone.key'):
        src = os.path.join(OUT, name)
        if os.path.exists(src):
            shutil.copy2(src, sdir)
    for name, size in (('leaf1', 870), ('leaf2', 860)):
        wire = open(os.path.join(OUT, '%s_ca.pem' % name), 'rb').read().strip()
        if len(wire) > size:
            fail('%s_ca wire %d > slot %d' % (name, len(wire), size))
            continue
        padded = wire + b'\n' * (size - len(wire))
        open(os.path.join(sdir, '%s_slot.pem' % name), 'wb').write(padded)
        print('  server-set\\%s_slot.pem = padded wire (%d + %d pad)'
              % (name, len(wire), size - len(wire)))

    return finish()


def finish():
    print()
    if failures:
        print('RESULT: FAIL (%d problem(s))' % len(failures))
        return 1
    print('RESULT: PASS - fresh cert set in cert-tools\\out, '
          'server kit in cert-tools\\out\\server-set')
    print('next  : py patch_game.py')
    return 0


if __name__ == '__main__':
    sys.exit(main())
