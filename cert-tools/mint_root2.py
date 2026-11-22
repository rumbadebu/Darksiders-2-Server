"""
Re-mint the LOCAL Root CA with a SHORT subject DN + RSA-1024.

Why (2026-08-28): the identity clone (leaf{n}_ca) must carry the ORIGINAL
leaf's full subject DN (113 B) + serial AND fit its 870/860-byte exe slot.
Projections (re_rootinfo.py): even with the original root's 132-B DN as
issuer, leaf1_ca = PEM 899 > 870. The root slot is NOT pinned (evidence:
our previous root - a different DN than the original - passed the game's
chain verification), so the root DN is free:

    CN=THQ Connect Root CA, O=THQ Inc., C=US    (~70 B vs 132 B)

=> leaf1_ca DER ~562 / PEM ~800  -> fits 870 with headroom
=> leaf2_ca DER ~559 / PEM ~796  -> fits 860

Root: RSA-1024 (era-faithful), v3, basicConstraints critical CA:TRUE,
keyUsage critical keyCertSign+cRLSign, SHA-256 self-signature.
The previous root is backed up once as ca_legacy.key/.pem.
Outputs: out/ca.key / ca.pem
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
    x509.NameAttribute(x509.NameOID.COMMON_NAME, 'THQ Connect Root CA'),
])
SERIAL = 0xDF3422A2551D2C72   # one step beyond the original root's serial


def main():
    ca_key_path = os.path.join(CERT_DIR, 'ca.key')
    ca_pem_path = os.path.join(CERT_DIR, 'ca.pem')
    legacy_key = os.path.join(CERT_DIR, 'ca_legacy.key')
    if not os.path.exists(legacy_key):
        os.replace(ca_key_path, legacy_key)
        os.replace(ca_pem_path, os.path.join(CERT_DIR, 'ca_legacy.pem'))
        print('previous root backed up as ca_legacy.key/.pem')

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
            .add_extension(x509.KeyUsage(digital_signature=False,
                                         content_commitment=False,
                                         key_encipherment=False,
                                         data_encipherment=False,
                                         key_agreement=False,
                                         key_cert_sign=True,
                                         crl_sign=True,
                                         encipher_only=None,
                                         decipher_only=None),
                           critical=True)
            .sign(key, hashes.SHA256()))

    open(ca_key_path, 'wb').write(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption()))
    open(ca_pem_path, 'wb').write(cert.public_bytes(serialization.Encoding.PEM))
    print('new root subject: %s' % cert.subject.rfc4514_string())
    print('ca.key / ca.pem written (short-DN root, RSA-1024, CA:TRUE)')


if __name__ == '__main__':
    main()
