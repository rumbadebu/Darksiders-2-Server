"""
Build a certificate kit for the local Darksiders 2 server.

Generates certs into the `out/` subdirectory next to this script:
  out/ca.key / ca.pem          - local root CA (replaces THQ Connect Root CA in the exe)
  out/server.key / server.crt  - wildcard cert for *.thqconnect.com (+localhost)
  out/server-chain.pem         - server cert + CA (for the TLS server)
  out/client_ca.pem            - original THQ Connect Client CA (extracted from exe)
  out/client_ca.key            - original Client CA private key (extracted from exe)
  out/server-clientca.crt      - alt server cert signed by the embedded Client CA

The PEM blobs must fit into fixed-size slots inside Darksiders2.exe:
  Root CA slot   : 1248 bytes
  server cert #1 :  870 bytes
  server cert #2 :  860 bytes
So certificates are generated with progressively shorter subjects until they fit,
then padded with newlines (OpenSSL ignores trailing whitespace in PEM).
"""
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
EXE = os.path.join(ROOT, 'Darksiders2.exe')
CERT_DIR = os.path.join(HERE, 'out')

OPENSSL_CANDIDATES = [
    r'C:\Program Files\Git\mingw64\bin\openssl.exe',
    r'C:\Program Files\Git\usr\bin\openssl.exe',
    r'C:\Program Files (x86)\Git\mingw64\bin\openssl.exe',
    r'C:\Program Files (x86)\Git\usr\bin\openssl.exe',
    'openssl',  # hope it's on PATH
]

SLOT_ROOT_CA = 1248
SLOT_SERVER_A = 870
SLOT_SERVER_B = 860


def find_openssl():
    for cand in OPENSSL_CANDIDATES:
        try:
            out = subprocess.run([cand, 'version'], capture_output=True, text=True, timeout=10)
            if out.returncode == 0:
                print('using openssl: %s (%s)' % (cand, out.stdout.strip()))
                return cand
        except Exception:
            continue
    print('ERROR: openssl not found. Install Git for Windows or add openssl to PATH.')
    sys.exit(1)


def run_ssl(ssl_exe, args):
    out = subprocess.run([ssl_exe] + args, capture_output=True, text=True)
    if out.returncode != 0:
        msg = 'openssl ' + args[0] + ' failed:' + chr(10) + out.stdout + chr(10) + out.stderr
        raise RuntimeError(msg)
    return out


def pem_len(path):
    return len(open(path, 'rb').read())


def gen_ca(ssl_exe, out_key, out_pem, max_len):
    """Generate a self-signed CA whose PEM fits into max_len bytes.

    The first subject replicates the ORIGINAL THQ Connect Root CA DN exactly -
    the game may compare issuer identities, so we impersonate the original CA.
    """
    # The root replicates the ORIGINAL THQ Connect Root CA DN exactly - the
    # game looks up the issuer by distinguished name, so any other DN makes
    # it fail with "unknown CA". Trust additionally comes from installing
    # this CA into the Windows store (schannel/WinInet clients like EAWebKit).
    subjects = [
        '/C=US/ST=California/L=Agoura Hills/O=THQ Inc./OU=THQ Connect/CN=THQ Connect Root CA',
        '/O=THQ Inc./OU=THQ Connect/CN=THQ Connect Root CA',
        '/O=Local DS2/CN=THQ Connect Root CA',
        '/CN=THQ Connect Root CA',
        '/CN=Local Root CA',
    ]
    for subj in subjects:
        run_ssl(ssl_exe, ['genrsa', '-out', out_key, '1024'])
        run_ssl(ssl_exe, ['req', '-new', '-x509', '-key', out_key, '-out', out_pem,
                          '-days', '7300', '-subj', subj, '-sha256',
                          '-set_serial', '1'])
        n = pem_len(out_pem)
        print('  CA %-40r -> %d bytes (slot %d)' % (subj, n, max_len))
        if n <= max_len:
            return True
    return False


def gen_server_cert(ssl_exe, ca_key, ca_pem, out_key, out_csr, out_crt, ext_file, max_len):
    """Generate a server cert signed by our CA; PEM must fit max_len bytes.

    The game connects to thqc-darksiders2-pc.thqconnect.com:8443 (login) and
    ds2.gds.thqconnect.com:443 (GDS). A single-label wildcard CN does NOT
    match the two-label ds2.gds name, so we need a SAN with both wildcards.
    When a SAN extension is present OpenSSL matches against SAN only.
    """
    san = 'subjectAltName=DNS:*.thqconnect.com,DNS:*.gds.thqconnect.com'
    # ECDSA P-256 certs are ~170 bytes smaller than RSA-1024, letting us fit
    # the full original-style subject DN plus dual-wildcard SAN into the
    # fixed-size exe slots. The game's cipher list includes ECDHE-ECDSA-*,
    # and OpenSSL 1.0.0 fully supports secp256r1 server certificates.
    combos = [
        ('/C=CA/ST=BC/L=Vancouver/O=THQ Inc./OU=THQ Inc./CN=*.thqconnect.com', 'ec', '-sha256'),
        ('/O=THQ Inc./CN=*.thqconnect.com', 'ec', '-sha256'),
        ('/CN=*.thqconnect.com', 'ec', '-sha256'),
        ('/CN=x', 'ec', '-sha256'),
        ('/CN=x', '1024', '-sha256'),
        ('/CN=x', '768', '-sha1'),
    ]
    for subj, kind, dig in combos:
        with open(ext_file, 'w') as f:
            f.write(san + chr(10))
        if kind == 'ec':
            run_ssl(ssl_exe, ['ecparam', '-genkey', '-name', 'prime256v1', '-out', out_key])
        else:
            run_ssl(ssl_exe, ['genrsa', '-out', out_key, bits])
        run_ssl(ssl_exe, ['req', '-new', '-key', out_key, '-out', out_csr, '-subj', subj])
        run_ssl(ssl_exe, ['x509', '-req', '-in', out_csr, '-CA', ca_pem, '-CAkey', ca_key,
                          '-CAcreateserial', '-out', out_crt, '-days', '7300',
                          '-set_serial', '1', dig, '-extfile', ext_file])
        n = pem_len(out_crt)
        print('  SRV subj=%r key=%s dig=%s -> %d bytes (slot %d)' % (subj, kind, dig, n, max_len))
        if n <= max_len:
            return True
    return False


def extract_client_ca():
    """Pull the THQ Connect Client CA cert out of the exe."""
    data = open(EXE, 'rb').read()
    for m in re.finditer(rb'-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', data, re.S):
        block = m.group()
        if b'Q2xpZW50IENB' in block:  # base64 of "Client CA"
            return block.decode('ascii')
    return None


def extract_client_key(path):
    """Pull the embedded client RSA private key out of the exe."""
    data = open(EXE, 'rb').read()
    m = re.search(rb'-----BEGIN RSA PRIVATE KEY-----.*?-----END RSA PRIVATE KEY-----',
                  data, re.S)
    if not m:
        return False
    with open(path, 'wb') as f:
        f.write(m.group())
    return True


def main():
    os.makedirs(CERT_DIR, exist_ok=True)
    ssl_exe = find_openssl()

    ca_key = os.path.join(CERT_DIR, 'ca.key')
    ca_pem = os.path.join(CERT_DIR, 'ca.pem')
    srv_key = os.path.join(CERT_DIR, 'server.key')
    srv_csr = os.path.join(CERT_DIR, 'server.csr')
    srv_crt = os.path.join(CERT_DIR, 'server.crt')
    ext_file = os.path.join(CERT_DIR, 'server.ext')

    print('generating local root CA (original DN)...')
    if not gen_ca(ssl_exe, ca_key, ca_pem, SLOT_ROOT_CA):
        print('ERROR: could not generate a CA PEM that fits the %d-byte slot' % SLOT_ROOT_CA)
        sys.exit(1)

    print('generating server certificate...')
    if not gen_server_cert(ssl_exe, ca_key, ca_pem, srv_key, srv_csr, srv_crt,
                           ext_file, min(SLOT_SERVER_A, SLOT_SERVER_B)):
        print('ERROR: could not generate a server cert PEM that fits the %d-byte slot'
              % min(SLOT_SERVER_A, SLOT_SERVER_B))
        sys.exit(1)

    # chain file: server cert first, then CA
    with open(os.path.join(CERT_DIR, 'server-chain.pem'), 'wb') as f:
        f.write(open(srv_crt, 'rb').read())
        f.write(open(ca_pem, 'rb').read())

    cca = extract_client_ca() if os.path.exists(EXE) else None
    if not os.path.exists(EXE):
        print('note: %s not found - skipping embedded Client CA extraction' % EXE)
    if cca:
        with open(os.path.join(CERT_DIR, 'client_ca.pem'), 'w') as f:
            f.write(cca)
        print('extracted original THQ Connect Client CA -> out/client_ca.pem')
    else:
        print('warning: could not locate Client CA in exe')

    # Hedge: mint an alt server cert signed by the ORIGINAL embedded Client CA
    # key. If the game trusts that CA for server certs instead of the root,
    # switching to this cert fixes trust without any exe dependency
    # (enable via config "use_client_ca_cert": true).
    try:
        key_path = os.path.join(CERT_DIR, 'client_ca.key')
        if cca and extract_client_key(key_path):
            out = os.path.join(CERT_DIR, 'server-clientca.crt')
            run_ssl(ssl_exe, ['x509', '-req', '-in', srv_csr,
                              '-CA', os.path.join(CERT_DIR, 'client_ca.pem'),
                              '-CAkey', key_path, '-CAcreateserial', '-out', out,
                              '-days', '7300', '-set_serial', '1',
                              '-sha1', '-extfile', ext_file])
            print('minted alt server cert signed by embedded Client CA '
                  '-> out/server-clientca.crt')
    except Exception as e:
        print('note: client-CA-signed alt cert not generated: %s' % e)

    print()
    print('certificate kit ready in out/:')
    for fn in sorted(os.listdir(CERT_DIR)):
        print('  %-24s %6d bytes' % (fn, os.path.getsize(os.path.join(CERT_DIR, fn))))


if __name__ == '__main__':
    main()