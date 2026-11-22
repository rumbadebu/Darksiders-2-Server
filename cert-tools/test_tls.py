"""Test the local server like the game would: legacy TLS + hostname verification."""
import http.client
import json
import os
import socket
import ssl

HERE = os.path.dirname(os.path.abspath(__file__))
CA = os.path.join(HERE, 'out', 'ca.pem')

tests = [
    ('prod.thqconnect.com', 8443, '/version', None),
    ('ds2.gds.thqconnect.com', 443, '/version', None),
    ('ds2.gds.thqconnect.com', 443, '/gds/rpc', '{}'),
    ('prod.thqconnect.com', 8443, '/thqconnect/login/initSession',
     'appID=thqc-darksiders2-pc&platform=PC_STEAM'),
]

for host, port, path, body in tests:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except Exception:
        pass
    try:
        ctx.set_ciphers('DEFAULT@SECLEVEL=0')
    except ssl.SSLError:
        pass
    ctx.load_verify_locations(CA)
    ctx.check_hostname = True
    conn = http.client.HTTPSConnection('127.0.0.1', port, context=ctx, timeout=10)
    # connect manually so SNI/hostname checks use the real name
    sock = ctx.wrap_socket(socket.create_connection(('127.0.0.1', port), timeout=10),
                           server_hostname=host)
    conn.sock = sock
    method = 'POST' if body else 'GET'
    headers = {'Content-Type': 'application/x-www-form-urlencoded'} if body else {}
    conn.request(method, path, body=body, headers=headers)
    resp = conn.getresponse()
    data = resp.read().decode()
    print('%-28s:%-5d %-35s -> %d %s' % (host, port, path, resp.status, data[:120]))
    print('   tls: %s %s' % (sock.version(), sock.cipher()[0]))
    sock.close()