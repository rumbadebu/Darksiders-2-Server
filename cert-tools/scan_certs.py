"""Extract all embedded PEM certificates/keys and find more protocol strings."""
import re

data = open('Darksiders2.exe', 'rb').read()

print('=== PEM blocks ===')
for m in re.finditer(rb'-----BEGIN [A-Z ]+-----.*?-----END [A-Z ]+-----', data, re.S):
    block = m.group()
    print('offset 0x%x len %d type %r' % (m.start(), len(block),
          block.split(b'\n')[0].decode()))
    print(block.decode('ascii'))
    print('---')

# More protocol strings
markers = [b'errorCode', b'errorMessage', b'"result"', b'result', b'success',
           b'authToken', b'ticket', b'platform', b'steamid', b'locale',
           b'/version', b'longpoll', b'LongPoll', b'tunnel']
for mk in markers:
    hits = [m.start() for m in re.finditer(re.escape(mk), data)]
    print('%r -> %d hits %s' % (mk, len(hits), ['0x%x' % h for h in hits[:8]]))