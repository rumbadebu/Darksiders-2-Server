"""Verify the actual login hostname end-to-end."""
import http.client
import ssl

ctx = ssl.create_default_context(cafile='server/certs/ca.pem')
ctx.minimum_version = ssl.TLSVersion.TLSv1
ctx.set_ciphers('DEFAULT@SECLEVEL=0')

c = http.client.HTTPSConnection('thqc-darksiders2-pc.thqconnect.com', 8443,
                                timeout=10, context=ctx)
c.request('GET', '/version')
print('version     :', c.getresponse().read().decode()[:130])

c.request('POST', '/thqconnect/login/initSession',
          body='appID=thqc-darksiders2-pc&platform=PC_STEAM')
print('initSession :', c.getresponse().read().decode()[:130])