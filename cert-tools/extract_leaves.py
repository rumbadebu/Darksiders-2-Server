import struct
src = open('Darksiders2.exe.orig','rb').read()
for name, off, ln in [('leaf1', 0xed76b8, 870), ('leaf2', 0xed7a20, 860)]:
    blob = src[off:off+ln]
    m = blob.find(b'-----BEGIN CERTIFICATE-----')
    e = blob.find(b'-----END CERTIFICATE-----') + len(b'-----END CERTIFICATE-----')
    pem = blob[m:e]
    import base64, re
    b64 = b''.join(pem.splitlines()[1:-1])
    der = base64.b64decode(b64)
    open('tools\\%s_orig.der' % name, 'wb').write(der)
    print(name, 'DER', len(der))
