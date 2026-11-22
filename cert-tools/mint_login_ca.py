"""
Mint the LOGIN root CA that goes into the exe leaf1 SLOT (proven 2026-10-17).

Why (bisect 2026-10-17): the game's OpenSSL 1.0.0 cannot PEM_read_bio_X509
the extension-less leaf_ca certs when they sit in the leaf1/leaf2 slots
(TLS aborts before ClientHello). The WORKING Aug 28 exe instead embeds in
leaf1 a self-signed "login root" WITH v3 extensions:

    subject = CN=THQ Connect Root CA - Login, O=THQ Inc., C=US
    self-signed, serial 0x5A11C0DE, RSA-1024, SHA-256
    extensions: basicConstraints critical CA:TRUE + subjectKeyIdentifier
    PEM <= 830 B (fits the 870-byte leaf1 slot with headroom)

Leaf2 slot gets a byte copy of ca.pem (see PATCHING_GUIDE.md) - no tool
needed for that. The server's WIRE identity stays leaf1_ca (root-signed
*.thqconnect.com) served as leaf1_slot.pem; the login cert never goes on
the wire.

Serial note: the serial in the leaf slots is not pinned by the game
(proven: expB passes with served cert != embedded cert). Keep 0x5A11C0DE
for era fidelity or change SERIAL for a visibly fresh set.

Outputs: out/ca_login.key / out/ca_login.pem
Usage:   python mint_login_ca.py   (run from cert-tools/)
"""
import os
from datetime import datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa

HERE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(HERE, 'out')

SUBJECT = x509.Name([
    x509.NameAttribute(x509.NameOID.COUNTRY_NAME, 'US'),
    x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, 'THQ Inc.'),
    x509.NameAttribute(x509.NameOID.COMMON_NAME, 'THQ Connect Root CA - Login'),
])
SERIAL = 0x5A11C0DE          # proven value; free to change (not pinned)
SLOT_SIZE = 870              # exe leaf1 slot bytes


def main():
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    now = datetime.utcnow()
    cert = (x509.CertificateBuilder()
            .subject_name(SUBJECT)
            .issuer_name(SUBJECT)
            .public_key(key.public_key())
            .serial_number(SERIAL)
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=7300))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                           critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(
                key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))

    pem = cert.public_bytes(serialization.Encoding.PEM)
    if len(pem) > SLOT_SIZE:
        print('ERROR: login cert PEM %d B exceeds leaf1 slot (%d B)'
              % (len(pem), SLOT_SIZE))
        raise SystemExit(1)

    os.makedirs(CERT_DIR, exist_ok=True)
    open(os.path.join(CERT_DIR, 'ca_login.key'), 'wb').write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    open(os.path.join(CERT_DIR, 'ca_login.pem'), 'wb').write(pem)
    print('subject: %s' % cert.subject.rfc4514_string())
    print('serial:  0x%X  exts: %s'
          % (cert.serial_number,
             [e.oid._name for e in cert.extensions]))
    print('PEM: %d B <= slot %d B - OK' % (len(pem), SLOT_SIZE))
    print('out/ca_login.key / out/ca_login.pem written')
    print('reminder: copy ca_login.pem -> <certdir>\\leaf1_ca.pem and '
          'ca.pem -> <certdir>\\leaf2_ca.pem before running patch_exe.py')


if __name__ == '__main__':
    main()
