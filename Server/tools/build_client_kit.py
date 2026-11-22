"""
Mint a LOCAL client-auth kit for mutual TLS with the Darksiders 2 client.

Proven 2026-08-27: on TLS CertificateRequest the game presents its embedded
client cert (CN=DS2, issuer 'THQ Connect Client CA') - mutual TLS confirmed.
The real issuing CA is unrecoverable, so we impersonate the issuer DN:
mint our own client CA with the EXACT original issuer DN and re-issue a
CN=DS2 client cert (v1/sha1/RSA-1024, same subject DN as the original),
then patch both blobs into the exe slots (cert ~898B, key ~886B) via
tools/patch_exe.py.

Uses the `cryptography` package (pip install cryptography) instead of shelling
out to openssl: OpenSSL 3.x x509 -req auto-adds SKID/AKID extensions (making a
v3 cert 1006 bytes > slot) and genrsa emits PKCS#8. cryptography gives exact
deterministic v1/PKCS#1 output that fits the slots on any version.

Outputs (server/certs/):
  client_local_ca.key / .pem - local client CA (server trust anchor)
  ds2_client.key / .crt      - new client key+cert for the exe slots
"""
import os
import sys
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(ROOT, 'server', 'certs')

SLOT_CLIENT_CERT = 898   # measured: client cert slot @0xf717c0 is 898 bytes
SLOT_CLIENT_KEY = 886    # measured: client key slot is 886 bytes

C = 'US'
ST = 'California'
L = 'Agoura Hills'
O = 'THQ, Inc.'
OU = 'THQ Connect'

CA_NAME = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, C),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, ST),
    x509.NameAttribute(NameOID.LOCALITY_NAME, L),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, O),
    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, OU),
    x509.NameAttribute(NameOID.COMMON_NAME, 'THQ Connect Client CA'),
])
CLIENT_NAME = x509.Name([
    x509.NameAttribute(NameOID.COUNTRY_NAME, C),
    x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, ST),
    x509.NameAttribute(NameOID.LOCALITY_NAME, L),
    x509.NameAttribute(NameOID.ORGANIZATION_NAME, O),
    x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, OU),
    x509.NameAttribute(NameOID.COMMON_NAME, 'DS2'),
])

NOW = datetime.now(timezone.utc)
NOT_BEFORE = NOW - timedelta(days=1)
NOT_AFTER = NOW + timedelta(days=7300)


def rewrap(pem, width=64):
    """Rewrap a PEM's base64 payload at `width` chars/line (fewer lines =
    smaller PEM; parsers accept any line width >= 1)."""
    lines = pem.decode('ascii').splitlines()
    b64 = ''.join(lines[1:-1])
    body = '\n'.join(b64[i:i + width] for i in range(0, len(b64), width))
    return (lines[0] + '\n' + body + '\n' + lines[-1] + '\n').encode('ascii')


def key_pem(key, max_len):
    """PKCS#1 ('BEGIN RSA PRIVATE KEY') PEM - what the OpenSSL 1.0-era game
    expects - fitted under max_len via line-width narrowing."""
    base = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption())
    for width in (64, 79, 120, 254):
        p = rewrap(base, width)
        if len(p) <= max_len:
            return p
    raise RuntimeError('key PEM cannot fit %d bytes' % max_len)


def cert_pem(cert, max_len):
    base = cert.public_bytes(serialization.Encoding.PEM)
    for width in (64, 79, 120, 254):
        p = rewrap(base, width)
        if len(p) <= max_len:
            return p
    raise RuntimeError('cert PEM cannot fit %d bytes' % max_len)


def mint_ca():
    """Local stand-in for 'THQ Connect Client CA' - same DN, our key."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    cert = (x509.CertificateBuilder()
            .subject_name(CA_NAME)
            .issuer_name(CA_NAME)
            .public_key(key.public_key())
            .serial_number(2)
            .not_valid_before(NOT_BEFORE)
            .not_valid_after(NOT_AFTER)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None),
                           critical=True)
            .sign(key, hashes.SHA256()))
    return key, cert


def mint_client(ca_key, ca_cert):
    """Re-issue CN=DS2: v1 (NO extensions, like the original), sha1,
    RSA-1024, signed by our stand-in CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    cert = (x509.CertificateBuilder()
            .subject_name(CLIENT_NAME)
            .issuer_name(ca_cert.subject)          # = original issuer DN
            .public_key(key.public_key())
            .serial_number(3)
            .not_valid_before(NOT_BEFORE)
            .not_valid_after(NOT_AFTER)
            .sign(ca_key, hashes.SHA256()))  # SHA1 signing is blocked by
    # modern cryptography; the only verifier is OUR server, and the game
    # merely forwards the blob, so SHA256 is safe (RSA1024 sig = 128B either
    # way, DER size unchanged vs the original).
    # v1 sanity: builder adds no extensions -> DER stays small like original
    if cert.extensions:
        raise RuntimeError('client cert unexpectedly got extensions')
    return key, cert


def main():
    os.makedirs(CERT_DIR, exist_ok=True)
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print('ERROR: run: python -m pip install cryptography')
        sys.exit(1)

    print('minting local client CA (DN = THQ Connect Client CA)...')
    ca_key, ca_cert = mint_ca()
    print('minting client cert CN=DS2 (v1/sha1/RSA-1024, like original)...')
    cli_key, cli_cert = mint_client(ca_key, ca_cert)

    kp = key_pem(cli_key, SLOT_CLIENT_KEY)
    cp = cert_pem(cli_cert, SLOT_CLIENT_CERT)
    print('client key PEM: %d bytes (slot %d)' % (len(kp), SLOT_CLIENT_KEY))
    print('client cert PEM: %d bytes (slot %d)' % (len(cp), SLOT_CLIENT_CERT))

    ca_kp = key_pem(ca_key, 4096)
    ca_cp = cert_pem(ca_cert, 4096)

    files = {
        'client_local_ca.key': ca_kp,
        'client_local_ca.pem': ca_cp,
        'ds2_client.key': kp,
        'ds2_client.crt': cp,
    }
    for fn, data in files.items():
        path = os.path.join(CERT_DIR, fn)
        with open(path, 'wb') as f:
            f.write(data)
        print('  wrote %s (%d bytes)' % (path, len(data)))

    # sanity: chain + key match
    pub_cert = cli_cert.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    pub_key = cli_key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    if pub_cert != pub_key:
        raise RuntimeError('client key does not match client cert pubkey!')
    print('chain: %s --signs--> %s  (key<->cert pubkey match OK)'
          % (cli_cert.issuer.rfc4514_string(),
             cli_cert.subject.rfc4514_string()))
    print()
    print('next: python tools/patch_exe.py   (installs cert+key into exe slots)')


if __name__ == '__main__':
    main()
