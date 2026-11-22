"""Regression tests for tools/gift_capture.py (the cloud-document capture tool).

The tool is what turns a session's `requests.log` back into the exact bytes the
game uploaded (`ops=MAP_SET&docKeys=TomeGift&keys=TomeGift&values=<...>`), and
what seeds that text into an account's store to simulate a received gift. Both
halves are pure text handling, so they are testable without the server or the
game running:

  * wire decoding - the log carries a GET as its request line and a POST as the
    body hex, and the game percent-encodes the comma separators
    (`docKeys=TomeGift%2cGameToWeb`), so slots must be DECODED first and split
    on ',' after, the way the server's own parse_qs + split(',') reads them;
  * log scanning - a GET reaches the log twice (request line + the whole TLS
    record the hook captured), which must not become two entries;
  * seeding - a seeded document comes back through `doc_content()` with the
    permissions a registration leaves behind, and clearing it empties the
    document without dropping those permissions.

Offline: CLOUD_DIR is redirected to a temp dir, so the real store is untouched.

    python tools/test_gift_capture.py
"""
import io
import json
import os
import shutil
import sys
import tempfile
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
sys.path.insert(0, os.path.join(ROOT, 'tools'))
import ds2_server as srv          # noqa: E402
import gift_capture as cap        # noqa: E402

srv.log = lambda msg: None        # keep server/logs/requests.log clean
# same trap as in test_cloud_exchange: the served-body previews must not depend on
# whether the shell that launched the suite had `DS2_EMPTY_DOC=1` exported
srv.EMPTY_DOC_RECORD = False
srv.DOC_MAP = False

ACCT = 100000002                   # 100000002 = the doc-store directory
STATS = ("<Stats> \n\t<ComboNumber Value='18'/>\n"
         "\t<TotalKills Value='12'/>\n</Stats>")
GIFT = "<TomeGift><Tome Id='7' Owner='76561198000000001'/></TomeGift>"
# A real captured gift blob (base64 BOD frame), used by the attribution checks:
# its GifterName field says 'playerA'. Loaded from the cleared-gifts backup so
# the fixture is a blob the game actually wrote, and needs no log parsing.
def _captured_blob():
    path = os.path.join(ROOT, 'server', 'data', 'cloud_backup_20261005',
                        '100000002', 'TomeGift.json')
    try:
        doc = json.load(open(path))
    except (IOError, OSError, ValueError):
        return ''
    for value in (doc.get('values') or {}).values():
        if value.startswith('Qk9E'):
            return value
    return ''


BLOB_GIFTER = _captured_blob()

_fails = []
_checks = [0]


def check(name, ok, detail=''):
    _checks[0] += 1
    print('%s  %s' % ('PASS' if ok else 'FAIL', name))
    if not ok:
        _fails.append(name)
        if detail:
            print('      %s' % detail)


def qs(**params):
    """A query string / body exactly as the game writes it (quote_plus values)."""
    return '&'.join('%s=%s' % (k, urllib.parse.quote_plus(str(v)))
                    for k, v in params.items())


def game_entries():
    """What tools/gift_capture.py extracts from GAME, one log line at a time."""
    return [e for e in (cap.game_entry(line) for line in GAME.splitlines())
            if e is not None]


# --- wire decoding ---------------------------------------------------------

# 09:34:51, the login registration: both docKeys and both op slots in one value
REG = qs(profileNames='/steam/76561198000000001,/steam/76561198000000001',
         ops='GET,GET', docKeys='TomeGift,GameToWeb', keys=',', values=',',
         policy='0,0', worldPerms='-1,-1', friendPerms='-1,-1', version=1)
check('slots split after decoding: the separators arrive as %2c',
      cap.slots(REG, 'docKeys') == ['TomeGift', 'GameToWeb']
      and cap.slots(REG, 'ops') == ['GET', 'GET']
      and cap.slots(REG, 'keys') == ['', ''], repr(cap.slots(REG, 'docKeys')))
check('doc_keys falls back to the singular docKey',
      cap.doc_keys(qs(docKey='TomeGift', ops='GET')) == ['TomeGift'])
check('a blank value is not a document',
      cap.docs_of(REG) == {}, repr(cap.docs_of(REG)))

# 09:36:17, the write that stored the Stats document
WRITE = qs(sessionID='deadbeef', ops='MAP_SET', docKeys='GameToWeb',
           keys='GameToWeb', values=STATS, policy=0, worldPerms=-1,
           friendPerms=-1)
check('a MAP_SET body yields its document verbatim',
      cap.docs_of(WRITE) == {'GameToWeb': STATS},
      repr(cap.docs_of(WRITE)))

# 09:35:02, the traffic the cloud filter must NOT show: a getURL, and a THQServer
# extension call - which is what the gift send rides on (0x5ade5b drives
# CallExtensionAsync), so it carries `requestExtensions`, not `values`. Only
# `--any` / `--find` see these.
EXT = qs(action='callExtension', requestExtensions='GiftService',
         data='R0lGVA==')

# The client side of the same session, as the loader logs it into ds2_tls.log
# (the game folder). Only three of these lines are interesting: the request the
# client made, its verdict on a good reply, and its verdict on a bad one.
GAME = ('[2026-09-27 20:13:34.011] [GAME] SteamAuthManager::GenerateAuthToken - '
        'request auth ticket with ticketHandle[77]\n'
        '[2026-09-27 20:13:34.417] [GAME] Running '
        'https://thqc-darksiders2-pc.thqconnect.com:8443'
        '/thqconnect/cloud/execute?%s\n'
        '[2026-09-27 20:13:34.555] [GAME] CallCloudAsync - response good: '
        '6 bytes, [0,[]]\n'
        '[2026-09-27 20:13:34.557] [GAME] AsyncJobDriver::DeleteJob[15] '
        'CallCloudAsync\n'
        '[2026-09-27 20:14:00.001] [GAME] CallExtensionAsync - response was not '
        'formatted correctly\n'
        '[2026-09-27 20:14:01.001] [GAME] CallCloudAsync - (used 3/4) extra data '
        'ignored: [0,[]]\n'
        '[2026-09-27 20:21:12.627] [GAME] AccountInternal::Disconnect - '
        'disconnect from server\n' % REG)
check('the client log yields the request line and the reply verdicts',
      [(r['method'], r['path']) for r in game_entries()]
      == [('SENT', '/thqconnect/cloud/execute'), ('RECV', 'CallCloudAsync'),
          ('RECV', 'CallExtensionAsync'), ('RECV', 'CallCloudAsync')],
      repr([(r['method'], r['path']) for r in game_entries()]))
check('a `Running <url>` line keeps its query, minus the host',
      game_entries()[0]['query'] == REG)
check('a good verdict keeps its body, a bad one its reason',
      game_entries()[1]['verdict'] == 'response good'
      and game_entries()[1]['body'] == '[0,[]]'
      and game_entries()[2]['verdict'] == 'response was not formatted correctly'
      and game_entries()[2]['body'] == ''
      and 'extra data' in game_entries()[3]['verdict'],
      repr([(r['verdict'], r['body']) for r in game_entries()]))

# --- single captures -------------------------------------------------------

GET_LINE = "b'GET /thqconnect/cloud/execute?%s HTTP/1.1\\r\\n'" % REG
check('a request line capture decodes to its query string',
      cap.request_text(cap.blob_of(GET_LINE)) == ('GET', REG))

WHOLE = ("GET /thqconnect/cloud/execute?%s HTTP/1.1\r\n"
         "Host: prod.thqconnect.com:8443\r\n"
         "Accept: */*\r\n\r\n" % REG)
check('a whole-TLS-record capture decodes to the same query string',
      cap.request_text(WHOLE.encode('utf-8')) == ('GET', REG))
check('a POST body capture is used as-is',
      cap.request_text(WRITE.encode('utf-8')) == ('POST', WRITE))
check('a POST request line carries no parameters',
      cap.request_text(b'POST /thqconnect/cloud/execute HTTP/1.1\r\n')
      == ('POST', ''))
check('junk payloads are dropped, not crashed on',
      cap.blob_of('zz not hex or repr') is None
      and cap.blob_of("b'incomplete") is None)


# --- the log ---------------------------------------------------------------

LOG = ('[2026-09-25 09:34:51] Running .../cloud/execute\n'
       '[2026-09-25 09:34:51] raw[tls] line %d bytes: %s\n'
       '[2026-09-25 09:34:51] raw[tls] recv %d bytes: %s\n'
       "[2026-09-25 09:34:51] raw[tls] line 32 bytes: b'Host: "
       "prod.thqconnect.com:8443\\r\\n'\n"
       '[2026-09-25 09:35:02] raw[tls] line 43 bytes: '
       "b'GET /thqconnect/url/getURL?v=1 HTTP/1.1\\r\\n'\n"
       '[2026-09-25 09:35:02] raw[tls] line 44 bytes: '
       "b'POST /thqconnect/cloud/execute HTTP/1.1\\r\\n'\n"
       '[2026-09-25 09:35:02] raw[tls] recv %d bytes: %s\n'
       '[2026-09-25 09:36:17] raw[tls] line 41 bytes: '
       "b'POST /thqconnect/cloud/execute HTTP/1.1\\r\\n'\n"
       '[2026-09-25 09:36:17] raw[tls] recv %d bytes: %s\n'
       '[2026-09-25 09:37:12] raw[plain] recv %d bytes: %s\n'
       '   (a log line without a timestamp keeps the previous one)\n'
       '[2026-09-25 09:37:12] raw[tls] recv 4 bytes: 6e6f706521\n'
       % (len(GET_LINE), GET_LINE, len(WHOLE), WHOLE.encode('utf-8').hex(),
          len(EXT), EXT.encode('utf-8').hex(),
          len(WRITE), WRITE.encode('utf-8').hex(),
          len(WRITE), WRITE.encode('utf-8').hex()))

tmp = tempfile.mkdtemp(prefix='ds2_giftcapture_')
try:
    log = os.path.join(tmp, 'requests.log')
    with io.open(log, 'w', encoding='utf-8') as fh:
        fh.write(LOG)
    reqs = cap.cloud_requests(log)

    check('the duplicate GET capture is one entry, not two',
          [r['method'] for r in reqs] == ['GET', 'POST', 'POST'],
          repr([(r['ts'], r['method']) for r in reqs]))
    check('the read decodes (ops, both docKeys, the profile)',
          reqs[0]['ops'] == 'GET,GET'
          and reqs[0]['keys'] == ['TomeGift', 'GameToWeb']
          and reqs[0]['profile'] == '/steam/76561198000000001'
          and reqs[0]['ts'] == '2026-09-25 09:34:51'
          and reqs[0]['docs'] == {}, repr(reqs[0]))
    check('the write carries its document',
          reqs[1]['docs'] == {'GameToWeb': STATS}
          and reqs[1]['keys'] == ['GameToWeb'],
          repr(reqs[1]['docs']))
    check('a raw[plain] capture (port 80) is replayed too',
          reqs[2]['ts'] == '2026-09-25 09:37:12'
          and reqs[2]['docs'] == {'GameToWeb': STATS},
          repr((reqs[2]['ts'], reqs[2]['docs'].keys())))
    check('a capture without ops/docKeys is ignored',
          all('ops=' in r['raw'] and 'docKeys=' in r['raw'] for r in reqs))
    check('the header line and `nope!` are not requests',
          all(r['method'] in ('GET', 'POST') for r in reqs))

    # --- the whole log, any endpoint (--any / --find) ----------------------
    every = cap.log_requests(log)
    check('log_requests keeps the traffic the cloud filter drops',
          [r['path'] for r in every] == ['/thqconnect/cloud/execute'] * 2
          + ['/thqconnect/url/getURL'] + ['/thqconnect/cloud/execute'] * 4
          + [''], repr([(r['method'], r['path']) for r in every]))
    check('a request line gives its method, path and query',
          every[0]['method'] == 'GET'
          and every[0]['path'] == '/thqconnect/cloud/execute'
          and every[0]['query'] == REG and every[0]['body'] == '',
          repr(every[0])[:160])
    check('a whole-record capture is a request too, not a body',
          every[1]['path'] == '/thqconnect/cloud/execute'
          and every[1]['query'] == REG, repr(every[1])[:160])
    check('a bare payload is the body of the POST line before it',
          every[4]['method'] == 'POST' and every[4]['body'] == EXT
          and every[4]['query'] == ''
          and every[4]['path'] == '/thqconnect/cloud/execute',
          repr(every[4])[:160])
    check('a body with no POST line before it keeps no path',
          every[-1]['path'] == '' and every[-1]['body'] == WRITE
          and every[-1]['ts'] == '2026-09-25 09:37:12', repr(every[-1])[:160])
    check('the cloud view is unchanged by the wider one',
          [r['method'] for r in reqs] == ['GET', 'POST', 'POST']
          and len(cap.cloud_requests(log)) == 3)

    # --- seeding (the received-gift simulation) ----------------------------
    srv.CLOUD_DIR = os.path.join(tmp, 'cloud')
    path = cap.seed(ACCT, 'TomeGift', GIFT, 'seed')
    doc = srv._load_doc(ACCT, 'TomeGift')
    check('a seeded document is served back verbatim',
          srv.doc_content(doc, 'TomeGift') == GIFT,
          repr(srv.doc_content(doc, 'TomeGift')))
    check('seeding a fresh account registers the exchange permissions',
          doc.get('docPolicyBits') == 1 and doc.get('worldPerms') == 2,
          repr((doc.get('docPolicyBits'), doc.get('worldPerms'))))
    check('the seeded account is the one asked for',
          path == srv._cloud_path(ACCT, 'TomeGift')[1], path)

    cap.seed(ACCT, 'TomeGift', None, 'clear')
    doc = srv._load_doc(ACCT, 'TomeGift')
    check('clearing removes the document',
          srv.doc_content(doc, 'TomeGift') is None)
    check('clearing keeps the registered permissions',
          doc.get('docPolicyBits') == 1 and doc.get('worldPerms') == 2,
          repr((doc.get('docPolicyBits'), doc.get('worldPerms'))))

    # The preview is the server's own body builder, so it must agree with the
    # positional record the client's 0xb32450 reads - and pass its walk.
    raw = cap.served_body(ACCT, 'TomeGift')
    check('a cleared account previews the accepted empty body',
          json.loads(raw) == [0, []], raw.decode('utf-8'))
    check('the empty preview passes the client walk',
          cap.walk_ok(raw)[0] is True)

    cap.seed(ACCT, 'TomeGift', GIFT, 'seed')
    raw = cap.served_body(ACCT, 'TomeGift')
    body = json.loads(raw)
    rec = body[1][0] if body[1] else []
    check('a seeded document previews the 5-slot positional record',
          body[0] == 0 and len(body[1]) == 1 and len(rec) == 5
          and rec[0] == 0 and rec[1] == GIFT and rec[2] == 1
          and rec[4] == 'seed', repr(rec)[:120])
    check('the seeded preview passes the client walk',
          cap.walk_ok(raw)[0] is True, raw.decode('utf-8'))
    logged = []
    keep_log = srv.log
    srv.log = logged.append
    try:
        cap.served_body(ACCT, 'TomeGift')
        check('the preview is not written to the request log',
              logged == [] and srv.log == logged.append, repr(logged))
    finally:
        srv.log = keep_log

    # --- the live log, when it is there ------------------------------------
    live = os.path.join(ROOT, 'server', 'logs', 'requests.log')
    if os.path.isfile(live):
        live_reqs = cap.cloud_requests(live)
        got = [r for r in live_reqs if r['docs'].get('GameToWeb')]
        check("the live log replays the game's own Stats write",
              bool(got) and got[-1]['docs']['GameToWeb'].startswith('<Stats>'),
              '%d capture(s) with a GameToWeb document' % len(got))
        check('the live log scan finds no duplicate request',
              len(live_reqs) == len({(r['ts'], r['raw']) for r in live_reqs}),
              '%d entries' % len(live_reqs))
    else:
        print('SKIP  no %s to replay' % live)

    # --- the document MAP (DS2_DOC_MAP, opt-in) ----------------------------
    # 2026-09-28 23:23: the client accepted the record and then logged
    # `Error: Unable to process string: Qk9E...` - its CloudInternal parser
    # (0xb08b07) requires the document to start with '[' or '{', i.e. it is the
    # document's MAP as JSON, not one stored value. With DOC_MAP the whole
    # key->value map is served, so a gift written into the recipient's doc comes
    # back in the shape the reader parses.
    tmp3 = tempfile.mkdtemp(prefix='ds2_docmap_')
    real_cloud_dir = srv.CLOUD_DIR
    srv.CLOUD_DIR = tmp3
    try:
        params = {'profileNames': '/steam/76561198000000001',
                  'ops': 'MAP_SET', 'docKeys': 'GameToWeb', 'keys': 'GameToWeb',
                  'values': STATS}
        actor3 = srv.account_from_request(params)
        gift_blob = 'Qk9E/QQBAQAeAAAAJgAAAJ4DAAB42nVSW0hUURQ9Z5xR'
        srv.cloud_execute('MAP_SET', params, actor3)
        srv.cloud_execute('MAP_SET', dict(params, docKeys='TomeGift',
                                          keys='5394434090946791192',
                                          values=gift_blob), actor3)
        acct3 = actor3['accountID']
        plain_raw = cap.served_body(acct3, 'TomeGift')
        plain_doc = json.loads(plain_raw)[1][0][1]
        check('default: a bare value is served (the shape the client refused)',
              plain_doc == gift_blob, repr(plain_doc)[:60])
        # the document shape comes from server/docshape.txt, which the operator may
        # have switched while testing: pin it to `pairs` for the checks below
        shape_file = os.path.join(tmp3, 'docshape.txt')
        keep_shape_file = srv.DOC_SHAPE_FILE
        srv.DOC_SHAPE_FILE = shape_file
        with io.open(shape_file, 'w', encoding='utf-8') as fh:
            fh.write('pairs\n')
        srv.DOC_MAP = True
        try:
            map_raw = cap.served_body(acct3, 'TomeGift')
            map_doc = json.loads(map_raw)[1][0][1]
            check('DOC_MAP serves the document as key/value pairs by default',
                  json.loads(map_doc) == [['5394434090946791192', gift_blob]],
                  repr(map_doc)[:90])
            check('the map body still passes the client walk',
                  cap.walk_ok(map_raw)[0] is True, map_raw.decode('utf-8')[:120])
            never_raw = cap.served_body(acct3, 'NeverWritten')
            never_doc = json.loads(never_raw)[1][0][1]
            check('a doc with no real entries is served as the empty string',
                  never_doc == '', repr(never_doc))
            empty_val = cap.served_body(acct3, 'GameToWeb')      # holds STATS, not ''
            pairs = json.loads(json.loads(empty_val)[1][0][1])
            check('a doc with entries still serves all of them',
                  ['GameToWeb', STATS] in pairs, repr(pairs)[:80])

            # the record's other four slots (server/docslot.txt, round-robin)
            slot_file = os.path.join(tmp3, 'docslot.txt')
            keep_slot_file = srv.DOC_SLOT_FILE
            srv.DOC_SLOT_FILE = slot_file
            try:
                with io.open(slot_file, 'w', encoding='utf-8') as fh:
                    fh.write('# comment\n0=1\n0=2 4="playerB"\n0=0 3=0\n')
                for _ in range(3):
                    rec = json.loads(cap.served_body(acct3, 'TomeGift'))[1][0]
                    check('slot override %s applied' % (rec[0],),
                          rec[0] in (1, 2, 0), repr(rec)[:70])
                    if rec[0] == 2:
                        check('a slot override also reaches slot 4',
                              rec[4] == 'playerB', repr(rec)[:70])
                check('slot 1 (the document) is never overridden by the file',
                      json.loads(json.loads(
                          cap.served_body(acct3, 'TomeGift'))[1][0][1]).__class__
                          is list, 'document slot changed shape')
            finally:
                srv.DOC_SLOT_FILE = keep_slot_file

            # 2026-10-05 (attribution): the sender comes from the gift blob's own
            # GifterName field, so it is correct even though profileNames names
            # only the recipient. `blob` is a real captured gift; GIFT is XML and
            # must report no sender. See gift_gifter_name().
            check('a real gift blob reports a sender',
                  srv.gift_gifter_name(BLOB_GIFTER) == 'playerA',
                  repr(srv.gift_gifter_name(BLOB_GIFTER)))
            check('an XML document reports no sender (falls back)',
                  srv.gift_gifter_name(GIFT) is None,
                  repr(srv.gift_gifter_name(GIFT)))
            check('an empty value reports no sender',
                  srv.gift_gifter_name('') is None, 'not None')
            check('a truncated blob reports no sender instead of raising',
                  srv.gift_gifter_name('Qk9E/QQBA') is None, 'raised')

            # 2026-10-05: DS2_TRACE is a permanent switch, off by default.
            check('tracing is off unless DS2_TRACE=1',
                  srv.TRACE_CLOUD is False, repr(srv.TRACE_CLOUD))
            check('the trace file is only opened when tracing is on',
                  srv.TRACE_CLOUD is False
                  or os.path.basename(srv._trace_path) == 'trace.log',
                  repr(srv._trace_path))
            check('trace() is a no-op when off (no trace.log written)',
                  srv.trace('x') is None or not os.path.exists(srv._trace_path),
                  'trace.log appeared while tracing was off')

            vals2 = {'111': 'a', '222': 'b'}
            lit1 = srv.render_doc(vals2, '[{"key":"key","data":0}]')
            lit2 = srv.render_doc(vals2, '[{"key":"key","data":0},'
                                         '{"key":"key","data":0}]')
            check("docshape a literal entry (the client's own `key`) is "
                  'emitted once',
                  lit1 == '[{"key":"key","data":0}]', repr(lit1))
            check('docshape a two-entry literal keeps both entries',
                  lit2 == '[{"key":"key","data":0},{"key":"key","data":0}]',
                  repr(lit2))
            check('docshape a literal entry is NOT nested or wrapped',
                  lit1.count('[') == 1, 'container was inferred, not literal')
            check('docshape %K% still renders one entry per value',
                  srv.render_doc(vals2, '{"key":"%K%","data":0}')
                  == '[{"key":"111","data":0},{"key":"222","data":0}]',
                  repr(srv.render_doc(vals2, '{"key":"%K%","data":0}')))
            check('docshape %N% numbers the entries',
                  srv.render_doc(vals2, '{"key":"key%N%","data":0}')
                  == '[{"key":"key1","data":0},{"key":"key2","data":0}]',
                  repr(srv.render_doc(vals2, '{"key":"key%N%","data":0}')))
            # a spec that spells out its own container must not be re-wrapped
            check('docshape an explicit [%E%] container is not nested',
                  srv.render_doc(vals2, '[%E%] => {"key":"%K%","data":0}')
                  == '[{"key":"111","data":0},{"key":"222","data":0}]',
                  repr(srv.render_doc(vals2,
                                      '[%E%] => {"key":"%K%","data":0}')))
            check('docshape a bracketed %K% spec is not double-wrapped',
                  srv.render_doc(vals2, '[{"key":"%K%","data":0}]')
                  == '[{"key":"111","data":0},{"key":"222","data":0}]',
                  repr(srv.render_doc(vals2, '[{"key":"%K%","data":0}]')))

            # 2026-10-05: DS2_COLLECT honours the game's placeholder-key collect.
            check('collecting placeholder removes is off by default',
                  srv.COLLECT_REMOVES is False, repr(srv.COLLECT_REMOVES))
            check('the placeholder the game sends is the literal `key`',
                  srv.COLLECT_PLACEHOLDER_KEYS == ('key',),
                  repr(srv.COLLECT_PLACEHOLDER_KEYS))
            # a collect must NOT re-stamp the sender. Opening the inbox issues
            # MAP_REMOVEs, and each one used to overwrite lastModifiedName - so a
            # correct sender never survived the client reading its own inbox.
            # COLLECT_REMOVES stays off here: the empty-the-inbox test below
            # reuses this same account and would otherwise be the thing that
            # changes it.
            acct5 = 60707070
            actor5 = {'accountID': acct5, 'accountName': 'Player70707'}
            srv.cloud_execute('MAP_SET', {
                'docKeys': 'TomeGift', 'keys': '7000000000000000009',
                'values': BLOB_GIFTER, 'policy': '1',
                'worldPerms': '2', 'friendPerms': '0'}, actor5)
            doc5 = srv._load_doc(acct5, 'TomeGift')
            doc5['lastModifiedName'] = 'KEEPME001'
            srv._save_doc(acct5, 'TomeGift', doc5)
            srv.cloud_execute('MAP_REMOVE', {
                'docKeys': 'TomeGift', 'keys': 'key', 'values': '',
                'policy': '0', 'worldPerms': '-1', 'friendPerms': '-1'},
                actor5)
            check('a collect does NOT overwrite lastModifiedName',
                  srv._load_doc(acct5, 'TomeGift')['lastModifiedName']
                  == 'KEEPME001',
                  repr(srv._load_doc(acct5, 'TomeGift')['lastModifiedName']))
            # and with it ON, a placeholder remove really empties the inbox
            srv.COLLECT_REMOVES = True
            try:
                acct4 = 60808080
                srv.cloud_execute('MAP_SET', {
                    'docKeys': 'TomeGift', 'keys': 'TomeGift,7000000000000000001,7000000000000000002',
                    'values': ',%s,%s' % (BLOB_GIFTER, BLOB_GIFTER),
                    'policy': '1', 'worldPerms': '2', 'friendPerms': '0'},
                    {'accountID': acct4, 'accountName': 'Player80808'})
                before = len([k for k in srv._load_doc(acct4, 'TomeGift')['values']
                              if k not in ('TomeGift', '')])
                # the exact shape the game sends: keys=key,key
                srv.cloud_execute('MAP_REMOVE,MAP_REMOVE', {
                    'docKeys': 'TomeGift,TomeGift', 'keys': 'key,key', 'values': ',',
                    'policy': '0,0', 'worldPerms': '-1,-1', 'friendPerms': '-1,-1'},
                    {'accountID': acct4, 'accountName': 'Player80808'})
                after = [k for k in srv._load_doc(acct4, 'TomeGift')['values']
                         if k not in ('TomeGift', '')]
                check('the collect emptied an inbox of %d gift(s)' % before,
                      before == 2 and after == [], 'left %r' % (after,))
                check('... and kept the TomeGift sentinel key',
                      'TomeGift' in srv._load_doc(acct4, 'TomeGift')['values'],
                      'sentinel lost')
            finally:
                srv.COLLECT_REMOVES = False

            # the shape is read per request from docshape.txt, so it can be
            # A/B-ed while the server and the game are both running
            shape_file = os.path.join(tmp3, 'docshape.txt')
            keep_shape_file = srv.DOC_SHAPE_FILE
            srv.DOC_SHAPE_FILE = shape_file
            try:
                for shape, head in (('pairs', '[["5394434090946791192",'),
                                    ('pair_list', '[["5394434090946791192",["'),
                                    ('map', '{"5394434090946791192":'),
                                    ('map_list', '{"5394434090946791192":["'),
                                    ('"%K%":{"Val":["%V%"]}',
                                     '{"5394434090946791192":{"Val":["'),
                                    ('key_data',
                                     '[{"key":"5394434090946791192","data":"'),
                                    ('key_data_list',
                                     '[{"key":"5394434090946791192","data":["'),
                                    ('key_data_obj',
                                     '{"5394434090946791192":{"key":'),
                                    ('obj_key_data',
                                     '[{"Object":{"key":"5394434090946791192"')):
                    with io.open(shape_file, 'w', encoding='utf-8') as fh:
                        fh.write('# comment\n%s\n' % shape)
                    doc = json.loads(cap.served_body(acct3, 'TomeGift'))[1][0][1]
                    check('docshape %s renders the document as expected' % shape,
                          doc.startswith(head), repr(doc)[:70])
                with io.open(shape_file, 'w', encoding='utf-8') as fh:
                    fh.write('nonsense\n')
                doc = json.loads(cap.served_body(acct3, 'TomeGift'))[1][0][1]
                check('an unknown docshape falls back to pairs',
                      doc.startswith('[["5394434090946791192",'), repr(doc)[:40])
                os.remove(shape_file)
                doc = json.loads(cap.served_body(acct3, 'TomeGift'))[1][0][1]
                check('a missing docshape file defaults to pairs',
                      doc.startswith('[["5394434090946791192",'), repr(doc)[:40])
            finally:
                srv.DOC_SHAPE_FILE = keep_shape_file
        finally:
            srv.DOC_MAP = False
    finally:
        srv.DOC_SHAPE_FILE = keep_shape_file      # the operator's real file
        srv.CLOUD_DIR = real_cloud_dir
        shutil.rmtree(tmp3, ignore_errors=True)

    # --- the gift grammar, from the live log -------------------------------
    # 2026-09-28 21:47:47: the first gift the game itself wrote, so the document
    # it uploaded is the grammar of the exchange (`VICTORY_RU.md`). If a capture
    # is in the log, it must still be base64(BOD + zlib(object graph)).
    if os.path.isfile(live):
        import gift_doc_decode as dec                    # noqa: E402
        got = [r for r in cap.cloud_requests(live) if r['docs'].get('TomeGift')]
        if got:
            magic, header, plain, strings = dec.layers(got[-1]['docs']['TomeGift'])
            check('the captured gift document is base64(BOD + zlib)',
                  magic == b'BOD\xfd' and header is not None and plain,
                  repr((magic, header, None if plain is None else len(plain))))
            check('... and it carries the gift object graph the UI renders',
                  strings and 'Gift' in strings and 'Items' in strings
                  and 'GifterName' in strings,
                  repr((strings or [])[:8]))
        else:
            print('SKIP  no TomeGift document captured yet')
    else:
        print('SKIP  no %s to replay' % live)

    # --- the command line --------------------------------------------------
    # `--raw` takes no value, so it must not swallow the next option: with the
    # old parser `--raw --log <path>` skipped `--log` and failed on the path
    # ("unknown argument"), which is why this is pinned.
    def cli(*args):
        argv, sys.argv = sys.argv, ['gift_capture.py'] + list(args)
        out = io.StringIO()
        keep, sys.stdout = sys.stdout, out
        try:
            return cap.main(), out.getvalue()
        finally:
            sys.stdout, sys.argv = keep, argv

    rc, out = cli('--raw', '--log', log)
    check('--raw does not swallow the next option',
          rc == 0 and '--- 3 cloud/execute request(s)' in out
          and '\n       docKeys=' in out,
          'rc=%r %r' % (rc, out[:200]))
    rc, out = cli('--any', '--log', log)
    check('--any lists every endpoint, cloud or not',
          rc == 0 and '/thqconnect/url/getURL' in out
          and out.count('/thqconnect/cloud/execute') >= 4
          and '--- 8 entry(s)' in out, 'rc=%r %r' % (rc, out[:400]))
    check('--any tallies the endpoints it saw',
          '2 x GET  /thqconnect/cloud/execute' in out
          and '1 x GET  /thqconnect/url/getURL' in out
          and '4 x POST /thqconnect/cloud/execute' in out
          and '1 x POST (no path)' in out, repr(out[-400:]))
    rc, out = cli('--find', 'requestExtensions', '--log', log)
    check('--find prints the newest matching request, decoded',
          rc == 0 and 'GiftService' in out and 'R0lGVA==' in out
          and 'url/getURL' not in out, 'rc=%r %r' % (rc, out[:200]))
    rc, out = cli('--find', '<TomeGift', '--log', log)
    check('--find with no match fails instead of printing junk',
          rc == 1 and 'no entry carrying' in out, 'rc=%r %r' % (rc, out[:120]))

    # --- the other side of the wire (--game-log) ---------------------------
    game_log = os.path.join(tmp, 'ds2_tls.log')
    with io.open(game_log, 'w', encoding='utf-8') as fh:
        fh.write(GAME)
    rc, out = cli('--game-log', game_log)
    check('--game-log lists the client side without needing --any',
          rc == 0 and 'SENT /thqconnect/cloud/execute' in out
          and 'RECV CallCloudAsync' in out and 'response good' in out,
          'rc=%r %r' % (rc, out[:400]))
    check('the client tally keeps the verdict, so a regression is visible',
          '1 x RECV CallCloudAsync: response good' in out
          and '1 x RECV CallExtensionAsync: response was not formatted correctly'
          in out, repr(out[-300:]))
    rc, out = cli('--game-log', game_log, '--find', 'TomeGift')
    check('--find works on the game log: the request the client made',
          rc == 0 and 'SENT /thqconnect/cloud/execute' in out
          and 'docKeys=TomeGift,GameToWeb' in out and '%2c' not in out,
          'rc=%r %r' % (rc, out[:200]))
    rc, out = cli('--game-log', game_log, '--find', 'response good')
    check('--find finds a verdict too, and prints its body',
          rc == 0 and 'verdict: response good' in out and '[0,[]]' in out,
          'rc=%r %r' % (rc, out[:200]))
    rc, out = cli('--game-log', os.path.join(tmp, 'absent.log'))
    check('a missing game log is a failure, not a traceback',
          rc == 1 and 'no such game log' in out, 'rc=%r %r' % (rc, out[:120]))
    rc, _ = cli('--game-log', game_log, '--doc', 'TomeGift')
    check('--doc is refused for the game log (it seeds the store)',
          rc == 2, 'rc=%r' % rc)
    rc, _ = cli('--doc')
    check('an option without its value is a usage error', rc == 2, 'rc=%r' % rc)
    rc, _ = cli('--clear')
    check('--clear without an account is a usage error', rc == 2, 'rc=%r' % rc)
    rc, _ = cli('--nope')
    check('an unknown option is a usage error', rc == 2, 'rc=%r' % rc)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if _fails:
    print('%d of %d checks FAILED: %s'
          % (len(_fails), _checks[0], '; '.join(_fails)))
    sys.exit(1)
print('all %d checks passed' % _checks[0])
