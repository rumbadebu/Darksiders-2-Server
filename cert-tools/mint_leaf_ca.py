"""
Mint ROOT-CA-SIGNED identity certs for the exe's leaf slots.

Evidence (2026-08-28):
- serving the self-signed leaf clone  -> handshake FAILS unknown_ca
  => the game DOES chain-verify against the Root CA slot (our local CA).
- serving Root-CA-signed server.crt   -> mTLS completes, but the game then
  closes cleanly without reading app data => a post-handshake identity
  check failed (server.crt had a CN-only subject, EC key, wrong serial).

Fix: one cert satisfying BOTH layers:
  - subject DN / serial = original leaf (full DN *.thqconnect.com)
  - issuer = our local Root CA (chains to the patched root slot)
  - key = leaf{n}_clone.key (reused; server key path unchanged)
  - currently-valid dates (chain verification checks expiry), SHA-256 sig
The SAME pem is patched into the leaf slot, so a byte/field pin compares
against exactly what we present.

Outputs: out/leaf{n}_ca.pem/.der  (slot budgets: leaf1 870, leaf2 860)
"""
import os
import sys
from datetime import timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CERT_DIR = os.path.join(HERE, 'out')
TOOLS = HERE
SLOTS = {'leaf1': 870, 'leaf2': 860}


def rewrap(pem, width=100):
    lines = pem.decode('ascii').splitlines()
    b64 = ''.join(lines[1:-1])
    body = '\n'.join(b64[i:i + width] for i in range(0, len(b64), width))
    return (lines[0] + '\n' + body + '\n' + lines[-1] + '\n').encode('ascii')


def mint(name):
    orig = x509.load_der_x509_certificate(
        open(os.path.join(TOOLS, '%s_orig.der' % name), 'rb').read())
    ca_key = serialization.load_pem_private_key(
        open(os.path.join(CERT_DIR, 'ca.key'), 'rb').read(), password=None)
    ca_cert = x509.load_pem_x509_certificate(
        open(os.path.join(CERT_DIR, 'ca.pem'), 'rb').read())
    key = serialization.load_pem_private_key(
        open(os.path.join(CERT_DIR, '%s_clone.key' % name), 'rb').read(),
        password=None)

    try:
        nb = orig.not_valid_before_utc
    except AttributeError:
        nb = orig.not_valid_before
    end = nb + timedelta(days=10200)               # ~2040, currently valid

    cert = (x509.CertificateBuilder()
            .subject_name(orig.subject)            # EXACT original identity
            .issuer_name(ca_cert.subject)          # chained to our root slot
            .public_key(key.public_key())
            .serial_number(orig.serial_number)     # EXACT original serial
            .not_valid_before(nb)
            .not_valid_after(end)
            .sign(ca_key, hashes.SHA256(), padding.PKCS1v15()))

    pem = rewrap(cert.public_bytes(serialization.Encoding.PEM))
    der = cert.public_bytes(serialization.Encoding.DER)
    fit = SLOTS[name]
    if len(pem) > fit:
        raise RuntimeError('%s_ca PEM %d > slot %d' % (name, len(pem), fit))
    open(os.path.join(CERT_DIR, '%s_ca.pem' % name), 'wb').write(pem)
    open(os.path.join(CERT_DIR, '%s_ca.der' % name), 'wb').write(der)
    print('%s_ca: PEM %d/%d, serial 0x%X, chained to local Root CA'
          % (name, len(pem), fit, orig.serial_number))
    print('   subject: %s' % orig.subject.rfc4514_string())
    print('   issuer : %s' % ca_cert.subject.rfc4514_string())


def main():
    for name in ('leaf1', 'leaf2'):
        for req in ('%s_orig.der' % name, 'ca.key', 'ca.pem',
                    '%s_clone.key' % name):
            if not os.path.exists(os.path.join(
                    TOOLS if req.endswith('_orig.der') else CERT_DIR, req)):
                print('MISSING input: %s' % req)
                sys.exit(1)
        mint(name)
    print('leaf{n}_ca certs written - rerun patch_exe.py to embed them')


if __name__ == '__main__':
    main()
