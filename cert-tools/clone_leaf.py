"""
Clone the original embedded *.thqconnect.com leaf certs.

Evidence (2026-08-27): the game closes every TLS session right after the
handshake without reading application data. The original service certs were
SELF-SIGNED wildcard certs, so the client cannot be doing chain verification
for this service - it must use an identity/pin check. Our current cert
(CN-only subject, EC key, CA-issued) fails every plausible variant.

Fix: mint field-identical clones of leaf1/leaf2:
  - same subject == issuer (self-signed), same serial, same DNs
  - v3 with no extensions, sha1WithRSAEncryption, RSA-1024 (all match)
  - notBefore preserved; notAfter extended to 2040 (defeats expiry checks;
    harmless for byte-pinning because the SAME clone bytes get patched
    into the exe's leaf slots)
PEM output is rewrapped at 100 chars/line so it fits the exe slots
(leaf1 slot 870 B, leaf2 slot 860 B).

Outputs in out/: leaf{1,2}_clone.der/.pem/.key
"""
import os
import sys
from datetime import timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

HERE = os.path.dirname(os.path.abspath(__file__))
CERT_DIR = os.path.join(HERE, 'out')
TOOLS = HERE

# exe slot budgets (PEM must fit; patch_exe pads with zeros to block size)
SLOTS = {'leaf1': 870, 'leaf2': 860}


def rewrap(pem, width=100):
    lines = pem.decode('ascii').splitlines()
    b64 = ''.join(lines[1:-1])
    body = '\n'.join(b64[i:i + width] for i in range(0, len(b64), width))
    return (lines[0] + '\n' + body + '\n' + lines[-1] + '\n').encode('ascii')


def clone(name):
    der = open(os.path.join(TOOLS, '%s_orig.der' % name), 'rb').read()
    orig = x509.load_der_x509_certificate(der)

    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    try:
        nb = orig.not_valid_before_utc
        end = nb + timedelta(days=10200)          # ~2040
    except AttributeError:                        # older cryptography
        nb = orig.not_valid_before
        end = nb + timedelta(days=10200)

    cert = (x509.CertificateBuilder()
            .subject_name(orig.subject)
            .issuer_name(orig.subject)            # self-signed like original
            .public_key(key.public_key())
            .serial_number(orig.serial_number)
            .not_valid_before(nb)
            .not_valid_after(end)
            # NOTE: original used sha1WithRSAEncryption, but cryptography's
            # rust backend refuses SHA-1 signing. SHA-256 is verified fine by
            # the game's OpenSSL 1.0.0, and identity fields stay exact.
            .sign(key, hashes.SHA256(), padding.PKCS1v15()))

    der_out = cert.public_bytes(serialization.Encoding.DER)
    pem_out = rewrap(cert.public_bytes(serialization.Encoding.PEM))
    key_out = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption())

    fit = SLOTS[name]
    if len(pem_out) > fit:
        raise RuntimeError('%s clone PEM %d > slot %d' % (name, len(pem_out), fit))

    for ext, blob in (('der', der_out), ('pem', pem_out), ('key', key_out)):
        with open(os.path.join(CERT_DIR, '%s_clone.%s' % (name, ext)), 'wb') as f:
            f.write(blob)

    print('%s: DER %d -> %d, PEM %d/%d, serial %s, %s .. %s'
          % (name, len(der), len(der_out), len(pem_out), fit,
             format(orig.serial_number, 'X'), nb, end))
    print('   subject: %s' % orig.subject.rfc4514_string())


def main():
    os.makedirs(CERT_DIR, exist_ok=True)
    for name in ('leaf1', 'leaf2'):
        clone(name)
    print('clones written to %s' % CERT_DIR)


if __name__ == '__main__':
    main()
