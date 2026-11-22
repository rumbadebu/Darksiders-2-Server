import sys, os, json
sys.path.insert(0, os.path.join('e:', os.sep, 'tmp', 'BrokenCopies', 'Darksiders 2 server', 'server'))
import ds2_server as S

# load the REAL recipient TomeGift doc
doc = json.load(open('server/data/cloud/100000001/TomeGift.json'))
# build the values map the way the server does (gift-id -> blob, skip meta)
values = doc.get('values') or {}
print('gift values keys:', list(values.keys())[:3], '...')
spec = S.doc_shape()
print('doc_shape() ->', spec)
body = S.render_doc(values, spec)
print('rendered MAP_GET body (first 200):', body[:200])
print('blob in body:', 'Qk9E' in body)
print('data is int (no quotes around 1):', '"data":1' in body)
