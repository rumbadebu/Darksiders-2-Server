"""Force TLSv1.0 exactly like the game's OpenSSL 1.0.0 client."""
import os
import socket
import ssl

CRLF = bytes([13, 10])

HERE = os.path.dirname(os.path.abspath(__file__))
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
ctx.minimum_version = ssl.TLSVersion.TLSv1
ctx.maximum_version = ssl.TLSVersion.TLSv1
ctx.set_ciphers('DEFAULT@SECLEVEL=0')
ctx.load_verify_locations(os.path.join(HERE, 'out', 'ca.pem'))
ctx.load_verify_locations(os.path.join(HERE, 'out', 'client_ca.pem'))
ctx.check_hostname = True

for host, port in [('prod.thqconnect.com', 8443), ('ds2.gds.thqconnect.com', 443)]:
    s = ctx.wrap_socket(socket.create_connection(('127.0.0.1', port), timeout=10),
                        server_hostname=host)
    print('%s:%d TLS1.0 handshake OK: %s %s' % (host, port, s.version(), s.cipher()))
    req = (b'GET /version HTTP/1.1' + CRLF +
           b'Host: ' + host.encode() + CRLF +
           b'Connection: close' + CRLF + CRLF)
    s.sendall(req)
    data = b''
    while True:
        chunk = s.recv(4096)
        if not chunk:
            break
        data += chunk
    text = data.decode(errors='replace').replace(chr(13) + chr(10), ' | ')
    print('   ', text[:170])
    s.close()