"""Regression tests for the item-exchange cloud path + the getNews body.

Two defects, both fixed 2026-09-24 (see CONTINUE_HERE.md):

  1. IDENTITY - `account_from_request()` matched none of the parameters the
     cloud endpoints actually send, so every request fell through to the
     params-hash fallback and hashed the per-request `auth` ticket blob. The
     call that WRITES a document and the call that READS it were mapped to
     different accounts - visible in the store: 23 junk dirs, each holding the
     210-byte `{"": ""}` permission registration, while the real Stats
     document landed in a hash-named dir. Fixed by resolving
     `profileNames=/steam/<steam64>` first (and `auth` second - the ticket does
     embed the SteamID at offset 12).

  2. DELIVERY - `_cloud_body()` returned a hardcoded `[0, []]`, so a stored
     document was never handed back and the exchange UI had nothing to list.
     It now serves each stored document as the POSITIONAL record its reader
     (`0xb32450`) walks:

         [ status, document, docPolicyBits, lastModifiedTime,
           lastModifiedProfileName ]

     and not as a JSON object - see below.

  3. WRONG SHAPE - both this body and getNews were first sent with KEY NAMES
     (`{"status":0,"document":...}`, `[{"result":0,"news":[]}]`), which the
     client's walk CANNOT parse: the pushed name literals are dead metadata
     (`0xaf803e` uses only its parser/out arguments), every field reader is
     positional, and the scalar scanner (`0xaf7f06`) sees a key's `"` as a
     quoted NUMBER and then dies on the first letter. The live trace of the
     1598-byte keyed body:

         want=0x5b '[' got=0x5b '[' @+0/63e ret=af7e47   top-level open
         want=0x5b '[' got=0x30 '0' @+1/63e ret=af7f47   resultCode 0
         want=0x5b '[' got=0x5b '[' @+3/63e ret=b32619   data list open
         want=0x5b '[' got=0x7b '{' @+4/63e ret=af7e47   element: not '['
         want=0x7b '{' got=0x7b '{' @+4/63e ret=af7e5a   ... '{' branch
         want=0x5b '[' got=0x22 '"' @+5/63e ret=af7f47   <- dies on the key
         CallCloudAsync - response was not formatted correctly

     and getNews died the same way at +1 on `[{"result":0,"news":[]}]`. Both
     handlers now emit bare positional arrays; `tools/sim_reader.py` replays this
     walk offline (it reproduces all three recorded traces line for line).

     getNews is `[ result, [ ...items... ] ]`. The old belief that its checker
     looked up the NAMES `result`/`news` came from seeing those literals pushed
     at 0xb32072/0xb32088 - they are diagnostics. The checker (`0xb32012`, one
     caller 0xb20c71) is:

         open '[' or '{' -> int result -> list news -> close

Offline: CLOUD_DIR is redirected to a temp dir and `log()` is silenced, so
neither the server nor the game has to run.

    python tools/test_cloud_exchange.py
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import ds2_server as srv          # noqa: E402

srv.log = lambda msg: None        # keep server/logs/requests.log clean
# 2026-09-28: pin the DEFAULT off, whatever the ambient shell says - `DS2_EMPTY_DOC=1`
# in the environment (e.g. exported for the live server) would otherwise flip this
# suite's default-behaviour checks. The empty-record section below toggles it itself.
srv.EMPTY_DOC_RECORD = False
# ... and the same for the document-map mode (`DS2_DOC_MAP=1` for the live server)
srv.DOC_MAP = False

STEAM64 = 76561198000000001       # the SteamID the game logs in as
ACCT = STEAM64 & 0xFFFFFFFF       # 100000002 - the doc-store directory
PROFILE = '/steam/%d' % STEAM64

# Verbatim prefixes of the `auth` ticket blobs from logs/requests.log
# (2026-09-24 01:22:04 and 04:48:54). Steam re-encrypts them per request, so
# they differ every time - which is why hashing the whole parameter set (the
# pre-fix fallback) can never identify an account. 20 bytes is all the
# `parse_steam_ticket` offset-12 read looks at.
AUTH_1 = '140000006d5d66613b3d25cbf24ca504010010019bf0b16a18'
AUTH_2 = '1400000047cefa2ab1316b7cf24ca504010010018e04b36a18'

STATS_FALLBACK = ("<Stats> \n\t<ComboNumber Value='18'/>\n"
                  "\t<TotalKills Value='12'/>\n</Stats>")

_fails = []
_checks = [0]


def check(name, ok, detail=''):
    _checks[0] += 1
    print('%s  %s' % ('PASS' if ok else 'FAIL', name))
    if not ok:
        _fails.append(name)
        if detail:
            print('      %s' % detail)



def real_stats_doc():
    """The `<Stats>` document the game wrote (2026-09-24 04:49:29), if kept.

    Returns (account_dir_name, text) or None. Read-only probe of the real
    store so the round trip can use the game's own bytes.
    """
    base = os.path.join(ROOT, 'server', 'data', 'cloud')
    if not os.path.isdir(base):
        return None
    for acct in sorted(os.listdir(base)):
        path = os.path.join(base, acct, 'GameToWeb.json')
        if not os.path.isfile(path):
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        text = srv.doc_content(doc, 'GameToWeb')
        if text and text.startswith('<Stats>'):
            return acct, text
    return None


def old_account_id(params):
    """The pre-fix resolution: hash every parameter (auth blob included).

    Kept as regression evidence - it shows the two captures below used to land
    on two different accounts, which is the item-exchange bug.
    """
    seed = json.dumps(params, sort_keys=True, default=str).encode()
    steamid = int.from_bytes(hashlib.md5(seed).digest()[:6], 'little') | (1 << 31)
    return steamid & 0xFFFFFFFF


class Stub(object):
    """Handler stand-in: the cloud/news handlers only need these members."""

    _pos_raw = staticmethod(srv.Handler._pos_raw)
    _cloud_body = srv.Handler._cloud_body
    _actor = srv.Handler._actor
    _oracle = staticmethod(lambda name: None)

    class server(object):              # noqa: N801 - stands in for HTTPServer
        config = {'news': []}


stub = Stub()

# Verbatim request shapes from logs/requests.log. Note the WRITE (POST
# 04:49:29) carries profileNames but no auth at all, while the READ (GET
# 04:49:58) carries both - the two used to hash to different accounts.
WRITE = {'profileNames': PROFILE, 'ops': 'MAP_SET', 'docKeys': 'GameToWeb',
         'keys': 'GameToWeb', 'values': STATS_FALLBACK, 'version': '1',
         'platform': 'PC_STEAM', 'title': 'Darksiders2',
         'language': 'English'}
READ = {'profileNames': PROFILE, 'ops': 'MAP_GET', 'docKeys': 'TomeGift',
        'keys': '', 'values': '', 'policy': '0', 'worldPerms': '-1',
        'friendPerms': '-1', 'version': '1', 'platform': 'PC_STEAM',
        'title': 'Darksiders2', 'language': 'English', 'auth': AUTH_2}
LOGIN_REG = {'profileNames': PROFILE + ',' + PROFILE, 'ops': 'SET,SET',
             'docKeys': 'TomeGift,GameToWeb', 'keys': ',', 'values': ',',
             'policy': '1,1', 'worldPerms': '2,2', 'friendPerms': '0,0'}

_sim = None


def _sim_ok(body, kind):
    """Run the offline port of the client's reader (tools/sim_reader.py).

    That model replays the exe's primitives (0xaf7e16/0xaf7f06/0xaf7eb2/...)
    and reproduces every recorded in-game trace line for line, so it is a
    faithful stand-in for the format gate the game applies.
    """
    global _sim
    if _sim is None:
        sys.path.insert(0, os.path.join(ROOT, 'tools'))
        import sim_reader
        _sim = sim_reader
    if isinstance(body, str):
        body = body.encode('utf-8')
    return _sim.run(body, kind)[0]


def _keyed_rejected(record):
    """The same document as a keyed OBJECT must fail the client's walk.

    Regression guard for the 2026-09-24 bug: the names are dead metadata, so a
    keyed element dies in the scalar scanner on the first letter of the key.
    """
    keys = ('status', 'document', 'docPolicyBits', 'lastModifiedTime',
            'lastModifiedProfileName')
    bad = json.dumps([0, [dict(zip(keys, record))]], separators=(',', ':'))
    good = json.dumps([0, [list(record)]], separators=(',', ':'))
    return (not _sim_ok(bad, 'cloud')) and _sim_ok(good, 'cloud')


print('--- identity: the write and the read must share one account ---')
a_write = srv.account_from_request(WRITE)
a_read = srv.account_from_request(READ)
check('profileNames=/steam/<steam64> -> accountID %d' % ACCT,
      a_write['accountID'] == ACCT and a_write['steamid'] == STEAM64,
      repr(a_write))
check('the write and the read resolve to the SAME account',
      a_write['accountID'] == a_read['accountID'] == ACCT,
      '%d vs %d' % (a_write['accountID'], a_read['accountID']))
check('the old fallback mapped these two captures to different accounts',
      old_account_id(WRITE) != old_account_id(READ),
      '%d vs %d' % (old_account_id(WRITE), old_account_id(READ)))
check('a repeated profileNames list still yields one account',
      srv.account_from_request(dict(READ, profileNames=PROFILE + ',' + PROFILE)
                               )['accountID'] == ACCT)
check('the auth ticket embeds the SteamID, so it is a valid second source',
      srv.account_from_request({'auth': AUTH_1})['accountID'] == ACCT,
      repr(srv.account_from_request({'auth': AUTH_1})))
check("the game's real parameter set never reaches the hash fallback",
      srv.account_from_request({'auth': AUTH_1,
                                'profileNames': PROFILE})['accountID'] == ACCT
      and old_account_id({'auth': AUTH_1, 'profileNames': PROFILE}) != ACCT,
      'the fallback would give %d'
      % old_account_id({'auth': AUTH_1, 'profileNames': PROFILE}))
check('profileNames outranks platformAccountName/username',
      srv.account_from_request({'profileNames': PROFILE,
                                'platformAccountName': '12345',
                                'username': '12345'})['accountID'] == ACCT)
check('an explicit numeric id is used when profileNames is absent',
      srv.account_from_request({'steamid': str(STEAM64)})['accountID'] == ACCT)
check('a different profile maps to a different account',
      srv.account_from_request({'profileNames': '/steam/76561197960267999'}
                               )['accountID'] != ACCT)

print()
print('--- document round trip (the fix that makes the exchange list content) ---')
found = real_stats_doc()
check('the store still holds the real <Stats> document from 04:49:29',
      found is not None, 'server/data/cloud/*/GameToWeb.json')
stats = found[1] if found else STATS_FALLBACK

tmp = tempfile.mkdtemp(prefix='ds2_cloud_test_')
real_cloud_dir = srv.CLOUD_DIR
srv.CLOUD_DIR = tmp                  # redirect the store; real data untouched
try:
    actor = srv.account_from_request(WRITE)
    srv.cloud_execute(WRITE['ops'], dict(WRITE, values=stats), actor)
    srv.cloud_execute(LOGIN_REG['ops'], LOGIN_REG, actor)

    path = os.path.join(tmp, str(ACCT), 'GameToWeb.json')
    check('MAP_SET stores under the profileNames account (%d)' % ACCT,
          os.path.isfile(path), 'no file at %s' % path)
    if os.path.isfile(path):
        with open(path, 'r', encoding='utf-8') as f:
            stored = json.load(f)
        check('the stored document is the bytes the game sent',
              srv.doc_content(stored, 'GameToWeb') == stats)
        check('the world permission from the registration is kept',
              stored.get('worldPerms') == 2, repr(stored.get('worldPerms')))
    check('the registration persists no junk value under an empty key',
          srv.doc_content(srv._load_doc(ACCT, 'TomeGift'), 'TomeGift') is None
          and '' not in srv._load_doc(ACCT, 'TomeGift')['values'],
          repr(srv._load_doc(ACCT, 'TomeGift')))
    check('the registration itself is kept (policy + perms), as a write',
          srv._load_doc(ACCT, 'TomeGift').get('docPolicyBits') == 1
          and srv._load_doc(ACCT, 'TomeGift').get('worldPerms') == 2,
          repr(srv._load_doc(ACCT, 'TomeGift')))
    check('negative perm slots never clobber stored permissions',
          srv._load_doc(ACCT, 'GameToWeb').get('worldPerms') == 2)

    body = json.loads(stub._cloud_body(actor, 'getkeys'))
    check('cloud getKeys body stays [0,[]]', body == [0, []], repr(body))

    # The spawn's read: MAP_GET TomeGift. No gift document is stored, so the
    # honest answer is the (accepted) empty list, not a fabricated document.
    raw = stub._cloud_body(actor, 'execute', srv.requested_doc_keys(READ))
    check('MAP_GET TomeGift with nothing stored -> [0,[]]',
          json.loads(raw) == [0, []], raw.decode('utf-8'))

    # The FUTURE path: as soon as the player publishes a gift, THAT document
    # must come back in the same positional record and pass the client walk.
    # Today the game sends MAP_SET/MAP_GET; the 2026-09-11 capture used the
    # bare `ops=GET,GET&docKeys=TomeGift,GameToWeb` form, so test the bare op.
    gift = "<TomeGift><Tome Id='7' Owner='76561198000000001'/></TomeGift>"
    gift_w = dict(WRITE, docKeys='TomeGift', keys='TomeGift', values=gift)
    srv.cloud_execute(gift_w['ops'], gift_w, actor)
    gift_r = dict(READ, ops='GET')
    gbody = stub._cloud_body(actor, 'execute', srv.requested_doc_keys(gift_r))
    gdoc = json.loads(gbody)
    glist = gdoc[1] if isinstance(gdoc, list) and len(gdoc) > 1 else []
    check('a PUBLISHED gift document is served (bare ops=GET capture form)',
          len(glist) == 1 and glist[0][1] == gift, gbody.decode('utf-8'))
    check('the served gift body passes the client walk',
          _sim_ok(gbody, 'cloud'), gbody.decode('utf-8'))

    # The same read for the docKey that DOES hold a document must deliver it.
    get_write = dict(READ, docKeys='GameToWeb')
    srv.cloud_execute(get_write['ops'], get_write, actor)
    doc = json.loads(stub._cloud_body(actor, 'execute',
                                      srv.requested_doc_keys(get_write)))
    served = doc[1] if isinstance(doc, list) and len(doc) > 1 else []
    check('a stored document is served back (hardcoded [] before the fix)',
          len(served) == 1, raw.decode('utf-8'))
    if served:
        check('document element is the POSITIONAL record 0xb32450 reads',
              isinstance(served[0], list) and len(served[0]) == 5,
              repr(served[0]))
        check('slot order: status, document, docPolicyBits, '
              'lastModifiedTime, lastModifiedProfileName',
              [type(v).__name__ for v in served[0]]
              == ['int', 'str', 'int', 'int', 'str'],
              repr([type(v).__name__ for v in served[0]]))
        check('document text is the stored XML, still JSON-escaped for the '
              'escape-aware string reader (af8288)',
              json.loads(json.dumps(served[0]))[1] == stats)
        check('a keyed element would fail the client walk (regression guard)',
              _keyed_rejected(served[0]))

    # --- 2026-10-06: the ROUTE SPLIT (plain GET = raw text, MAP_GET = map) --
    # The client's own history decides which shape each route may carry: all 54
    # `Unable to process string` verdicts in ds2_tls.log sit on a MAP_GET (its
    # map parser 0xb08b07 wants '['/'{' and rejects a stored value), while
    # ops=GET,GET carried the RAW blob/XML on 09-28 with `response good` and
    # zero rejections ever on a plain GET. DOC_MAP used to intercept BOTH
    # routes, so the gift blob (GifterName=...) never reached the client's
    # base64 dispatcher - the wrong sender name in the Tome UI. The handler
    # now passes `ops` into `_cloud_body`, which splits the routes.
    keep_map = srv.DOC_MAP
    keep_shape_file = srv.DOC_SHAPE_FILE
    shape_file = os.path.join(tmp, 'docshape.txt')
    # TWO lines, so the rotation counter proves whether a raw GET consumed one.
    with open(shape_file, 'w', encoding='utf-8') as fh:
        fh.write('[%E%] => {"key":"%K%","data":0}\n'
                 '[%E%] => {"key":"%K%","data":1}\n')
    srv.DOC_SHAPE_FILE = shape_file
    srv.DOC_MAP = True
    try:
        spin = srv._docshape_i[0]
        # the login read, through the REAL handler (ops ride along)
        code, raw_get = srv.Handler.h_thqconnect_cloud_execute(
            stub, 'GET', dict(READ, ops='GET,GET',
                              docKeys='TomeGift,GameToWeb'))
        got = json.loads(raw_get)[1]
        check('handler: ops=GET,GET serves BOTH documents RAW (DOC_MAP on)',
              code == 200 and len(got) == 2
              and got[0][1] == gift and got[1][1] == stats,
              repr([d[1][:50] for d in got]))
        check('the raw GET body passes the client walk',
              _sim_ok(raw_get, 'cloud'), raw_get.decode('utf-8')[:150])
        check('a raw GET consumes NO docshape rotation line',
              srv._docshape_i[0] == spin,
              'rotation advanced %d -> %d' % (spin, srv._docshape_i[0]))

        code, raw_map = srv.Handler.h_thqconnect_cloud_execute(
            stub, 'GET', dict(READ))                  # ops=MAP_GET, TomeGift
        got = json.loads(raw_map)[1]
        mtext = got[0][1] if got else None
        mdata = (json.loads(mtext)[0].get('data')
                 if isinstance(mtext, str) and mtext.startswith('[') else None)
        check('handler: ops=MAP_GET serves the rendered index map (DOC_MAP on)',
              code == 200 and mdata in (0, 1),
              repr(mtext))
        check('the map body passes the client walk',
              _sim_ok(raw_map, 'cloud'), raw_map.decode('utf-8')[:150])
        check('a MAP_GET consumes exactly ONE docshape rotation line',
              srv._docshape_i[0] == spin + 1,
              'rotation %d -> %d' % (spin, srv._docshape_i[0]))
        check('the MAP_GET rotation line was the one it used (data matches)',
              mdata == spin % 2, 'data=%r spin=%d' % (mdata, spin))
    finally:
        srv.DOC_MAP = keep_map
        srv.DOC_SHAPE_FILE = keep_shape_file

    # --- 2026-10-08: DS2_GET_MAP - the RECEIVE path on the plain GET route --
    # The client's only base64 decoder (0xb4394c) is called solely from the
    # entry walk at 0xb44f96: entries = {key {ptr,size}, value char* at +0x10},
    # stride 0x30, i.e. the canonical form of a parsed `<giftId> -> base64`
    # map whose values it decodes before `onGiftReceived` -> script addGift
    # -> save. doc_content()'s bare single value has no entries, so the walk
    # never runs - the received gift never enters the save (the inbox then
    # renders the save's own SENT entry under the player's own name). With
    # the switch on, a gift-id keyed doc is served as the map in the chosen
    # shape (pairs default, obj selectable - the obj shape was served
    # in-game 2026-10-09 and produced no save entry); GameToWeb and every
    # single-value doc (doc_map_json -> None) keep serving raw.
    num_id = '5394434090946791192'
    blob = 'Qk9ENjEwOQ=='                          # base64("610:9")
    map_w = dict(WRITE, docKeys='TomeGift', keys=num_id, values=blob)
    srv.cloud_execute(map_w['ops'], map_w, actor)
    keep_get_map = srv.GET_MAP
    spin = srv._docshape_i[0]

    def _get_read():
        code, raw = srv.Handler.h_thqconnect_cloud_execute(
            stub, 'GET', dict(READ, ops='GET,GET',
                              docKeys='TomeGift,GameToWeb'))
        got = json.loads(raw)[1] if code == 200 else []
        return raw, got

    try:
        # --- pairs (the DS2_GET_MAP default): [["<id>","<blob>"], ...] ---
        srv.GET_MAP = 'pairs'
        raw, got = _get_read()
        tg = got[0][1] if len(got) == 2 else None
        parsed = None
        if isinstance(tg, str) and tg.startswith('['):
            try:
                parsed = json.loads(tg)
            except ValueError:
                parsed = None
        check('GET_MAP=pairs: the gift-id keyed doc is served as '
              '[[<id>,<blob>],...]',
              isinstance(parsed, list) and [num_id, blob] in parsed,
              repr(tg)[:120])
        check('GET_MAP=pairs: the docKey sentinel is NOT in the pairs',
              isinstance(parsed, list)
              and all(e[0] != 'TomeGift' for e in parsed
                      if isinstance(e, list)),
              repr(parsed))
        check('GET_MAP: GameToWeb keeps its RAW <Stats> XML',
              len(got) == 2 and isinstance(got[1][1], str)
              and got[1][1].startswith('<Stats>'),
              repr(got[1][1] if len(got) > 1 else got)[:80])
        check('the pairs body passes the client walk',
              _sim_ok(raw, 'cloud'), raw.decode('utf-8')[:150])

        # --- obj (the 2026-10-09 candidate, kept selectable) ---
        srv.GET_MAP = 'obj'
        raw, got = _get_read()
        tg = got[0][1] if len(got) == 2 else None
        parsed = None
        if isinstance(tg, str) and tg.startswith('{'):
            try:
                parsed = json.loads(tg)
            except ValueError:
                parsed = None
        check('GET_MAP=obj: the gift-id keyed doc is served as {id:blob}',
              isinstance(parsed, dict) and parsed.get(num_id) == blob,
              repr(tg)[:120])
        check('the object body passes the client walk',
              _sim_ok(raw, 'cloud'), raw.decode('utf-8')[:150])
        check('GET_MAP consumes NO docshape rotation line',
              srv._docshape_i[0] == spin,
              'rotation advanced %d -> %d' % (spin, srv._docshape_i[0]))

        srv.GET_MAP = ''
        raw, got = _get_read()
        check('GET_MAP off (the default): the plain GET is the raw value '
              'again', len(got) == 2 and got[0][1] == gift,
              repr(got[0][1] if got else None)[:80])
    finally:
        srv.GET_MAP = keep_get_map
finally:
    srv.CLOUD_DIR = real_cloud_dir
    shutil.rmtree(tmp, ignore_errors=True)


print()
print('--- 2026-10-09: doc_map_json(shape) - the MAP_GET receive route ---')
# The MAP_GET route passes MAPGET_MAP explicitly; the plain-GET route passes
# GET_MAP (or None -> GET_MAP default). obj is canonical for the entry walk.
_gmdoc = {'values': {'TomeGift': '', '3326186032734777932': 'Qk9EAAAA',
                     '15253253175946540455': 'Qk9EBBBB'}}
check('doc_map_json(obj) -> the {id:blob} map the entry walk eats',
      srv.doc_map_json(_gmdoc, 'TomeGift', 'obj')
      == '{"3326186032734777932":"Qk9EAAAA","15253253175946540455":"Qk9EBBBB"}',
      repr(srv.doc_map_json(_gmdoc, 'TomeGift', 'obj')))
check('doc_map_json(pairs) -> the positional [id,blob] list',
      srv.doc_map_json(_gmdoc, 'TomeGift', 'pairs')
      == '[["3326186032734777932","Qk9EAAAA"],'
         '["15253253175946540455","Qk9EBBBB"]]',
      repr(srv.doc_map_json(_gmdoc, 'TomeGift', 'pairs')))
check('doc_map_json drops the empty docKey sentinel from both shapes',
      'TomeGift' not in srv.doc_map_json(_gmdoc, 'TomeGift', 'obj')
      and 'TomeGift' not in srv.doc_map_json(_gmdoc, 'TomeGift', 'pairs'))
# A single-value doc (GameToWeb) has no gift-id key -> None -> the caller keeps
# its normal index/docshape render (the blob map must never clobber it).
_gtw = {'values': {'GameToWeb': '<Stats/>'}}
check('doc_map_json: a single-value doc (GameToWeb) -> None '
      '(falls back to the index render)',
      srv.doc_map_json(_gtw, 'GameToWeb', 'obj') is None
      and srv.doc_map_json(_gtw, 'GameToWeb', 'pairs') is None)
# An empty inbox (only the sentinel) -> None, so nothing is served as a map.
_empty = {'values': {'TomeGift': ''}}
check('doc_map_json: an empty inbox (sentinel only) -> None',
      srv.doc_map_json(_empty, 'TomeGift', 'obj') is None
      and srv.doc_map_json(_empty, 'TomeGift', 'pairs') is None)


print()
print('--- getNews: the positional pair [ result, news ] (0xb32012) ---')
code, news_raw = srv.Handler.h_thqconnect_news_getnews(
    stub, 'GET', dict(READ, limit='32'))
news = json.loads(news_raw)
check('news body is [ 0, [] ] - positional, no keys',
      code == 200 and news == [0, []], repr(news))
check('the keyed body the client rejected at +1 is gone',
      news != [{'result': 0, 'news': []}])
check('the simulator accepts the served news body',
      _sim_ok(news_raw, 'news'))
check('the simulator rejects the old keyed news body',
      not _sim_ok('[{"result":0,"news":[]}]', 'news'))
check('param_int handles repeats, blanks and junk',
      (srv.param_int({'limit': '32'}, 'limit', 20) == 32
       and srv.param_int({'limit': ['', '8']}, 'limit', 20) == 20
       and srv.param_int({'limit': ''}, 'limit', 20) == 20
       and srv.param_int({'limit': 'abc'}, 'limit', 20) == 20
       and srv.param_int({}, 'limit', 20) == 20))
odd = 0
for extra in ({'limit': 'abc'}, {'limit': ['8', '4']}, {'limit': ''}, {}):
    try:
        body = json.loads(srv.Handler.h_thqconnect_news_getnews(
            stub, 'GET', dict(READ, **extra))[1])
        if body != [0, []]:
            odd += 1
    except Exception:                                   # noqa: BLE001
        odd += 1
check('the news handler survives odd `limit` values', odd == 0,
      '%d of 4 failed' % odd)
check('the junk {"": ""} registration is invisible to doc_content',
      srv.doc_content({'values': {'': ''}}, 'TomeGift') is None)


print()
print('--- the empty-document record (DS2_EMPTY_DOC, opt-in) ---')
# On the real service a docKey the client REGISTERED but never wrote to still
# exists, so the read answers with a record whose document is empty - while this
# emulator answered `[0,[]]` (no record at all) and the exchange UI reported a
# connection error. Serving the empty record is opt-in (ds2_server.
# EMPTY_DOC_RECORD / DS2_EMPTY_DOC=1) so both answers can be A/B-ed in-game;
# `tools/tmp_seed_gift.py empty-str` previews it too.
tmp2 = tempfile.mkdtemp(prefix='ds2_emptydoc_')
real_cloud_dir = srv.CLOUD_DIR
srv.CLOUD_DIR = tmp2
try:
    actor2 = srv.account_from_request(LOGIN_REG)
    srv.cloud_execute(LOGIN_REG['ops'], LOGIN_REG, actor2)
    off = json.loads(stub._cloud_body(actor2, 'execute',
                                      srv.requested_doc_keys(READ)))
    check('default: registered-but-empty is still no record',
          off == [0, []], repr(off))
    srv.EMPTY_DOC_RECORD = True
    try:
        srv.cloud_execute(LOGIN_REG['ops'], LOGIN_REG, actor2)
        on_raw = stub._cloud_body(actor2, 'execute', srv.requested_doc_keys(READ))
        on = json.loads(on_raw)
        rec = on[1][0] if isinstance(on, list) and len(on) > 1 and on[1] else None
        check('with EMPTY_DOC_RECORD the registration materializes the doc',
              rec is not None and rec[0] == 0 and rec[1] == '',
              on_raw.decode('utf-8'))
        check('... and the record keeps the registered policy/permissions',
              rec is not None and rec[2] == 1 and len(rec) == 5, repr(rec))
        check('... and the empty record passes the client walk',
              _sim_ok(on_raw, 'cloud'), on_raw.decode('utf-8'))
    finally:
        srv.EMPTY_DOC_RECORD = False
    after = json.loads(stub._cloud_body(actor2, 'execute',
                                        srv.requested_doc_keys(READ)))
    check('turning the switch off restores the empty answer',
          after == [0, []], repr(after))
finally:
    srv.CLOUD_DIR = real_cloud_dir
    shutil.rmtree(tmp2, ignore_errors=True)

print()
if _fails:
    print('%d of %d checks FAILED: %s' % (len(_fails), _checks[0],
                                          '; '.join(_fails)))
    sys.exit(1)
print('all %d checks passed' % _checks[0])

