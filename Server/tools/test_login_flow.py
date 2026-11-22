"""Full login + cloud + news flow through the real DNS names (hosts -> loopback).

Three different wire formats meet here, all decoded on 2026-09-24:

    login/*            positional, inside ONE outer array (0xafa77d checker)
                       [ status, [url...], [email...], [username...], age,
                         [ [[name...],accountID,profileID], ... ], *tail ]
                       initSession appends the sessionID as its tail slot;
                       platformLogin answers [result,tunnels,certificates,
                       gameSettings,globalSettings].

    cloud/execute      [ resultCode, [ [status, document, docPolicyBits,
      cloud/getKeys     lastModifiedTime, lastModifiedProfileName], ... ] ]

    news/getNews       [ result, [ ...items... ] ]

Both cloud and news are POSITIONAL (2026-09-24): the client pushes field NAMES
("resultCode", "data", "status", "document", "result", "news", ...) but its
value primitive (0xaf803e) never compares them - they are diagnostics. Sending
JSON objects with those keys makes the walk die in the scalar scanner, which is
what "response was not formatted correctly" was. See tools/sim_reader.py for the
offline replay of that walk and tools/test_cloud_exchange.py for the guards.

The item-exchange path is the interesting one:

    MAP_SET docKeys=GameToWeb  (the game writes its save stats document)
    MAP_GET docKeys=GameToWeb  (the document must come back byte-identical)

Run the server first (`2 - START SERVER.bat`), then inside the repo:

    python tools/test_login_flow.py

NOTE: if server/oracle/<name>.json exists it is served verbatim instead of the
handler body, so the login prints below can show the oracle, not the code.
"""
import http.client
import json
import ssl

ctx = ssl.create_default_context(cafile='server/certs/ca.pem')
try:
    ctx.minimum_version = ssl.TLSVersion.TLSv1
    ctx.set_ciphers('DEFAULT@SECLEVEL=0')
except Exception:
    pass

c = http.client.HTTPSConnection('prod.thqconnect.com', 8443, timeout=10,
                                context=ctx)


def call(method, path, body=None):
    c.request(method, path, body=body)
    raw = c.getresponse().read()
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def pos_of(body):
    """Positional login body -> (status, usernames, users), tolerating junk."""
    if not isinstance(body, list):
        return None, [], []
    status = body[0] if body else None
    names = body[3] if len(body) > 3 and isinstance(body[3], list) else []
    users = body[5] if len(body) > 5 and isinstance(body[5], list) else []
    return status, names, users


def docs_of(body):
    """CallCloud body -> its document list (`[status, [docs]]`)."""
    if isinstance(body, list) and len(body) > 1 and isinstance(body[1], list):
        return body[1]
    return []


def news_of(body):
    """News body -> (result, items); the pair is POSITIONAL `[result, news]`."""
    if isinstance(body, list) and len(body) > 1:
        items = body[1] if isinstance(body[1], list) else []
        return body[0], items
    return None, []


body = call('POST', '/thqconnect/login/initSession',
            body='appID=thqc-darksiders2-pc&platform=PC_STEAM')
sid = body[-1] if isinstance(body, list) and body else ''
print('initSession   sessionID =', sid, ' slots =', len(body)
      if isinstance(body, list) else body)

body = call('POST', '/thqconnect/login/platformLogin',
            body='sessionID=' + sid + '&platform=PC_STEAM'
                 '&platformAccountName=76561197960287930&device=PC_STEAM')
print('platformLogin body      =', json.dumps(body)[:120])

body = call('POST', '/thqconnect/login/ssoStatus', body='sessionID=' + sid)
status, names, users = pos_of(body)
print('ssoStatus     status    =', status, ' username =', names)
print('ssoStatus     users     =', json.dumps(users)[:120])

stats = ("<Stats> \n\t<ComboNumber Value='18'/>\n"
         "\t<TotalKills Value='12'/>\n</Stats>")
body = call('POST', '/thqconnect/cloud/execute',
            body='sessionID=' + sid + '&ops=MAP_SET&docKeys=GameToWeb'
                 '&keys=GameToWeb&values=' + stats)
print('cloud MAP_SET  status   =', body[0] if isinstance(body, list) else body,
      ' docs =', len(docs_of(body)))

body = call('POST', '/thqconnect/cloud/execute',
            body='sessionID=' + sid + '&ops=MAP_GET&docKeys=GameToWeb'
                 '&keys=GameToWeb&policy=0&worldPerms=-1&friendPerms=-1')
docs = docs_of(body)
print('cloud MAP_GET  docs     =', len(docs))
for d in docs:
    if isinstance(d, list) and len(d) == 5:
        print('   doc slots = status=%r policy=%r time=%r name=%r'
              % (d[0], d[2], d[3], d[4]))
        print('       document =', repr(d[1])[:70])
    else:
        print('   doc =', json.dumps(d)[:110], '<- NOT the positional record')

body = call('POST', '/thqconnect/cloud/execute',
            body='sessionID=' + sid + '&ops=MAP_GET&docKeys=TomeGift'
                 '&keys=&values=&policy=0&worldPerms=-1&friendPerms=-1')
print('exchange read  docs     =', len(docs_of(body)), '(TomeGift)')

body = call('POST', '/thqconnect/cloud/getKeys', body='sessionID=' + sid)
print('cloud getKeys  body     =', json.dumps(body))

body = call('GET', '/thqconnect/news/getNews?limit=5&version=1&platform=PC_STEAM'
                   '&title=Darksiders2&language=English')
result, items = news_of(body)
print('news           result   =', result, ' items =', len(items),
      ' root =', type(body).__name__)
