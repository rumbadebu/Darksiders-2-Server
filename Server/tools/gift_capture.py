"""Capture (and replay) the game's own cloud-document writes.

WHY: the exchange UI lists whatever the account's `TomeGift` document holds, and
the only trustworthy description of that document's grammar is a write made by
the game itself - `ops=MAP_SET&docKeys=TomeGift&keys=TomeGift&values=<...>`. The
server records every request in full: a GET as its request line
(`raw[tls] line <n> bytes: b'GET /thqconnect/cloud/execute?...'`) and a POST as
the body hex (`raw[tls] recv <n> bytes: <hex>`). So such a write can be read
back verbatim long after the session that produced it, and replayed:

  * list   every cloud/execute request in the log, oldest first, with its
           docKeys/ops and the length of each document it carried;
  * --doc  the newest document for a docKey, verbatim (that IS the grammar);
  * --seed copy such a document into an account's store - how a RECEIVED gift
           is simulated without a second Steam account. The server reads the
           store from disk on every request (`_load_doc`), so the next exchange
           read returns it and nothing has to be restarted. Seeding (and
           clearing) also prints the body the next read will return, built by
           the server's own `_cloud_body`, plus the verdict of the offline port
           of the client's format gate (`tools/sim_reader.py`) - so a seeded
           document can be checked before the game is even launched;
  * --text / --clear: seed a hand-written document, or empty it again, to A/B a
           guessed grammar without touching the game.

Slots are decoded first and split on ',' after, the way the server's own
`parse_qs` + `split(',')` reads them: the game percent-encodes the separators as
well (`docKeys=TomeGift%2cGameToWeb`, `ops=GET%2cGET`, `keys=%2c` for the empty
placeholder slots of a permission registration), so a `%2c` is always a
separator and a literal comma inside a document would be indistinguishable from
one - worth remembering if a gift ever arrives truncated at a comma.

Usage:
    python tools/gift_capture.py                       # every cloud request
    python tools/gift_capture.py --doc TomeGift        # newest TomeGift document
    python tools/gift_capture.py --seed 100000002       # ... stored for that account
    python tools/gift_capture.py --text '<Gift/>' --seed 100000002
    python tools/gift_capture.py --clear 100000002      # back to "no gift stored"
    python tools/gift_capture.py --any                 # EVERY request, any endpoint
    python tools/gift_capture.py --find TomeGift       # the newest request carrying it
    python tools/gift_capture.py --game-log            # the CLIENT's view (ds2_tls.log)
    python tools/gift_capture.py --game-log --find TomeGift
    python tools/gift_capture.py --raw                 # ... with the wire text
    python tools/gift_capture.py --log server/logs/round_0924d_stdout.txt

`--any` / `--find` are the transport-agnostic half, and they are what to reach
for first after sending a gift: the exe's module list has `/thqconnect/cloud/
execute` as its only data-carrying endpoint, but the gift routine at 0x5ade5b
drives the THQServer "extension" API (`CallExtensionAsync`, 0xf8edf0), so the
write may carry parameters the cloud filter would not look at
(`requestExtensions=...` instead of `values=...`). `--find <token>` prints the
newest matching request decoded, whatever its params are; `--any` lists them all.

`--game-log` reads the OTHER side of the wire - the game's own log in the game
folder (`ds2_tls.log`; its path can follow the option, and defaults to the same
one `tools/correlate_fmt.py` uses). One `[GAME] Running <url>` per request plus
one `[GAME] <Job>Async - response ...` verdict per reply, so it answers what the
server log cannot: did the CLIENT accept what it was sent (`response good`) or
reject it (`response was not formatted correctly`), and did it even send
anything (a send that dies at 0x5aaa13 before touching the network leaves no
server-side trace at all).

NOTE for `--text` on Windows: a gift document is JSON-escaped, so its quotes are
double (`\"`), and the shell eats embedded double quotes inside a single-quoted
argument (`'<Tome Id="7"/>'` arrives as `<Tome Id=7/>`). Escape them or use
single quotes inside the markup.
"""
import ast
import io
import os
import re
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'server'))
import ds2_server as srv          # noqa: E402

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8',
                              errors='replace')

LOG = os.path.join(ROOT, 'server', 'logs', 'requests.log')
# The loader's log in the game folder: the client's own view of the wire, one
# `[GAME] Running <url>` per request and one `<Job>Async - response ...` verdict
# per reply (`tools/correlate_fmt.py` reads the same file, same default path).
GAME_LOG = r'E:\SteamLibrary\steamapps\common\Darksiders 2\ds2_tls.log'
DOC_KEY = 'TomeGift'

TS_RE = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]')
# the server logs a capture as `raw[tls] line|recv <n> bytes: <repr|hex>`
# (`raw[plain]` on port 80, and a bare `tls recv` when the hook sees the whole
# TLS record rather than an HTTP line)
CAP_RE = re.compile(r"(?:raw\[(?:tls|plain)\] |tls )?(?:line|recv) \d+ bytes: (.*)$")


def blob_of(payload):
    """Decode one capture payload: a bytes repr (`line`) or hex (`recv`)."""
    payload = payload.rstrip()
    if payload[:2] in ("b'", 'b"'):
        try:
            return ast.literal_eval(payload)
        except (SyntaxError, ValueError):
            return None
    try:
        return bytes.fromhex(payload)
    except ValueError:
        return None


def request_text(blob):
    """(method, parameter text) of a captured request, or None when it has none.

    A POST body is logged on its own (`raw[tls] recv 2791 bytes: <hex>`, the
    whole body), a GET as its request line, and a whole TLS record sometimes
    carries the entire request - request line and headers included.
    """
    text = blob.decode('utf-8', 'replace')
    if not text.startswith(('GET ', 'POST ')):
        return 'POST', text
    head, sep, body = text.partition('\r\n\r\n')
    line = head.split('\r\n', 1)[0].split(' ')
    method = line[0]
    if method == 'GET':
        return method, line[1].partition('?')[2] if len(line) > 1 else ''
    return method, body if sep else ''



# --- wire decoding ---------------------------------------------------------

def raw_field(text, name):
    """Raw (still percent-encoded) value of `name` in a query string / body."""
    for field in text.split('&'):
        key, _, value = field.partition('=')
        if urllib.parse.unquote_plus(key) == name:
            return value
    return None


def slots(text, name):
    """Comma slots of `name`, as the server's parse_qs + split(',') sees them."""
    value = raw_field(text, name)
    if value is None:
        return []
    return urllib.parse.unquote_plus(value).split(',')


def first(text, name):
    s = slots(text, name)
    return s[0] if s else None


def doc_keys(text):
    return slots(text, 'docKeys') or slots(text, 'docKey')


def docs_of(text):
    """{docKey: document text} as the request carried it (blank text dropped)."""
    keys = doc_keys(text)
    out = {}
    for name in ('values', 'document'):
        for key, value in zip(keys, slots(text, name)):
            if value and value.strip():
                out[key] = value
    return out


# --- the log ---------------------------------------------------------------

def log_requests(path):
    """Every request the server logged, oldest first - any endpoint, not just
    cloud/execute.

    A request line arrives as its bytes repr (`raw[tls] line <n> bytes: b'GET
    ...'`), a POST payload as hex (`raw[tls] recv <n> bytes: <hex>`), and
    sometimes the whole TLS record, request line and headers included. A line
    capture yields the method/path/query, a bare payload the body of the POST
    line before it (that is what `--any`/`--find` need: a gift write is not
    necessarily a cloud/execute one).
    """
    out = []
    ts = ''
    with io.open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            m = TS_RE.match(line)
            if m:
                ts = m.group(1)
            m = CAP_RE.search(line.rstrip('\n'))
            if not m:
                continue
            blob = blob_of(m.group(1))
            if blob is None:
                continue
            text = blob.decode('utf-8', 'replace')
            req = {'ts': ts, 'method': '', 'path': '', 'query': '', 'body': '',
                   'raw': text, 'line': False}
            if text.startswith(('GET ', 'POST ')):
                head, sep, body = text.partition('\r\n\r\n')
                parts = head.split('\r\n', 1)[0].split(' ')
                req['method'] = parts[0]
                req['line'] = True
                target = parts[1] if len(parts) > 1 else ''
                req['path'] = target.partition('?')[0]
                req['query'] = target.partition('?')[2]
                req['body'] = body if sep else ''
                out.append(req)
                continue
            if '=' not in text:
                # a header line (`Host: ...`) or junk, not a request body
                continue
            req['method'] = 'POST'
            req['body'] = text
            prev = out[-1] if out else None
            if prev and prev['line'] and prev['method'] == 'POST' and prev['ts'] == ts:
                req['path'] = prev['path']
            out.append(req)
    return out


GAME_RE = re.compile(
    r'\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)?\] \[GAME\] (.*)$')
RUN_RE = re.compile(r'^Running (\S+)$')
RESP_RE = re.compile(r'^(\w+)Async\b.*?-\s*(response\b.*|\(used [^)]*\).*)$')
BODY_RE = re.compile(r'^(response[^:]*):\s*(\d+) bytes,\s*(.*)$')


def game_entry(line):
    """One `[GAME]` line -> a request/verdict entry, or None if it is noise.

    `SENT` = a request line the client logged (`Running <url>`), `RECV` = its
    verdict on a reply, labelled with the Async job name (`path`).
    """
    m = GAME_RE.search(line.rstrip('\n'))
    if not m:
        return None
    ts, text = m.group(1), m.group(2)
    req = {'ts': ts, 'method': '', 'path': '', 'query': '', 'body': '',
           'raw': text, 'line': False, 'verdict': ''}
    run = RUN_RE.match(text)
    if run:
        url = urllib.parse.urlsplit(run.group(1))
        req['method'] = 'SENT'
        req['path'] = url.path
        req['query'] = url.query
        return req
    resp = RESP_RE.match(text)
    if resp:
        detail = resp.group(2).strip()
        body = BODY_RE.match(detail)
        req['method'] = 'RECV'
        req['path'] = resp.group(1) + 'Async'
        req['verdict'] = body.group(1).strip() if body else detail
        req['body'] = body.group(3) if body else ''
        return req
    return None


def game_requests(path):
    """The client's own view of the wire: what the GAME logged it sent and got.

    `ds2_tls.log` (in the game folder; the loader adds the engine's `[GAME]`
    lines to its own hook trace) carries two useful forms, which pair up with
    what `--any`/`--find` show for the server:

        [GAME] Running https://.../thqconnect/cloud/execute?ops=MAP_GET&...
        [GAME] CallCloudAsync - response good: 6 bytes, [0,[]]
        [GAME] CallExtensionAsync - response was not formatted correctly
        [GAME] CallCloudAsync - (used 3/4) extra data ignored: [0,[...]]

    Entries use the same keys as `log_requests` (plus `verdict`), so the views
    and `--find` work on either side of the wire.
    """
    out = []
    with io.open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            entry = game_entry(line)
            if entry is not None:
                out.append(entry)
    return out


def cloud_requests(path):
    """Every cloud/execute request the server logged, oldest first."""
    out = []
    seen = set()
    for req in log_requests(path):
        text = req['query'] if req['method'] == 'GET' else req['body']
        if 'ops=' not in text or 'docKeys=' not in text:
            continue
        if (req['ts'], text) in seen:
            # a GET reaches the log twice: as its request line and again
            # inside the whole TLS record the hook captured
            continue
        seen.add((req['ts'], text))
        out.append({
            'ts': req['ts'],
            'method': req['method'],
            'raw': text,
            'ops': urllib.parse.unquote_plus(raw_field(text, 'ops') or ''),
            'keys': doc_keys(text),
            'profile': first(text, 'profileNames') or '',
            'docs': docs_of(text),
        })
    return out


# --- replay into the store -------------------------------------------------

def seed(account, doc_key, text, writer):
    """Store `text` as `account`'s `doc_key` document (i.e. a received gift)."""
    doc = srv._load_doc(account, doc_key)
    values = doc.setdefault('values', {})
    if text is None:
        values.pop(doc_key, None)
        values.pop('', None)
        upd = doc.setdefault('updaterNames', {})
        for k in (doc_key, ''):
            upd.pop(k, None)
    else:
        values[doc_key] = text
        doc.setdefault('updaterNames', {})[doc_key] = writer
    doc['lastModifiedName'] = writer
    doc['lastModifiedDate'] = int(time.time())
    if 'docPolicyBits' not in doc:
        # what the game's own login registration leaves behind for the exchange
        # docs (`ops=SET,SET&docKeys=TomeGift,GameToWeb&policy=1,1&worldPerms=2,2`,
        # 09:34:52), so a seeded document is visible exactly like one the
        # recipient's own client had registered
        doc['docPolicyBits'] = 1
        doc['worldPerms'] = 2
    srv._save_doc(account, doc_key, doc)
    _, path = srv._cloud_path(account, doc_key)
    return path


class _Handler(object):
    """Handler stand-in: `_cloud_body` only needs `_pos_raw`."""

    _pos_raw = staticmethod(srv.Handler._pos_raw)


def served_body(account, doc_key, ops=None):
    """The exact bytes the server will hand back for that stored document.

    Built by the REAL builder (`Handler._cloud_body`), so the preview cannot
    drift from what the game receives; only the actor is faked (it is read as
    `actor['accountID']`, and only for the every-docKey form).

    2026-10-06 ROUTE SPLIT: the served bytes now depend on the ROUTE - a plain
    `GET` slot gets the RAW stored text, `MAP_GET` gets the rendered index map
    (DOC_MAP on). `ops` is that route's op string (`'GET'`, `'MAP_GET'`, ...
    index-aligned with doc_key like on the wire); the default (None) keeps the
    pre-split preview, which rendered whenever DOC_MAP was on.
    """
    quiet = srv.log
    srv.log = lambda *a, **k: None            # the preview is not a request
    try:
        return srv.Handler._cloud_body(_Handler(), {'accountID': account},
                                       'execute', [doc_key], ops)
    finally:
        srv.log = quiet


def walk_ok(raw, kind='cloud'):
    """Does `raw` pass the client's format gate? -> (ok or None, why).

    Uses `tools/sim_reader.py`, the offline port of the exe's positional walk,
    which is what decides whether the game logs "response was not formatted
    correctly" for a body.
    """
    keep = sys.stdout
    # sim_reader re-wraps sys.stdout around `sys.stdout.buffer` at import time,
    # and dropping the PREVIOUS wrapper then closes that buffer - which would
    # kill this tool's own output. Hand it a throwaway stream instead; its
    # trace lines are not wanted here, only the verdict.
    sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding='utf-8')
    try:
        sys.path.insert(0, os.path.join(ROOT, 'tools'))
        import sim_reader
        return sim_reader.run(raw, kind)[0], ''
    except Exception as exc:                  # noqa: BLE001 - optional check
        return None, str(exc)
    finally:
        sys.stdout = keep


def brief(text, limit=200):
    """`text` shortened to one line, for a listing."""
    text = text.replace('\r', ' ').replace('\n', ' ')
    return text if len(text) <= limit else text[:limit] + ' ...'


def list_all(reqs, raw, label):
    """Print every request/verdict in `reqs`, whatever endpoint it hit."""
    if not reqs:
        print('nothing captured in %s' % label)
        return 1
    for r in reqs:
        if r.get('verdict'):
            detail = r['verdict']
            if r['body']:
                detail += ' (%d bytes, %s)' % (len(r['body']), brief(r['body'], 120))
        else:
            text = r['query'] or r['body']
            what = 'query' if r['query'] else ('body' if text else '')
            detail = ('%s %d chars: %s' % (what, len(text), brief(text))
                      if what else '-')
        print('[%s] %-4s %-34s %s'
              % (r['ts'], r['method'] or '?', r['path'] or '(no path)', detail))
        if raw and not r.get('verdict'):
            text = r['query'] or r['body']
            for field in text.split('&'):
                if field:
                    print('       %s' % field)
    tally = {}
    for r in reqs:
        key = (r['method'] or '?', r['path'] or '(no path)')
        if r.get('verdict'):
            # a verdict is the label a client-log regression is read from
            key = (r['method'], '%s: %s' % (r['path'], brief(r['verdict'], 60)))
        tally[key] = tally.get(key, 0) + 1
    print('--- %d entry(s) in %s ---' % (len(reqs), label))
    for (method, path), count in sorted(tally.items(),
                                        key=lambda kv: (-kv[1], kv[0])):
        print('%5d x %-4s %s' % (count, method, path))
    return 0


def show_find(reqs, token, raw):
    """Print the newest entry carrying `token`, decoded (verbatim with --raw).

    Searched in the encoded text and in its decoded form, so a token can be
    looked for the way it reads inside the document (`<TomeGift`) as well as
    the way it looks on the wire (`%3cTomeGift`).
    """
    hits = [r for r in reqs
            if token in r['query'] + r['body'] + r['raw']
            or token in urllib.parse.unquote_plus(r['query'] + r['body'])]
    if not hits:
        print('no entry carrying %r' % token)
        return 1
    r = hits[-1]
    text = r['query'] or r['body'] or r['raw']
    print('--- newest entry carrying %r: [%s] %s %s ---'
          % (token, r['ts'], r['method'] or '?', r['path'] or '(no path)'))
    print(urllib.parse.unquote_plus(text))
    if r.get('verdict'):
        print('verdict: %s' % r['verdict'])
    if raw:
        print('--- raw ---')
        print(r['raw'])
    return 0


def main():
    argv = list(sys.argv[1:])
    log = LOG
    want = None
    account = None
    text = None
    clear = False
    raw = False
    everything = False
    find = None
    game = None
    i = 0
    while i < len(argv):
        a = argv[i]
        i += 1
        if a == '--raw':
            raw = True
            continue
        if a == '--any':
            everything = True
            continue
        if a == '--game-log':
            # optional value: `--game-log` alone reads the loader's log in the
            # game folder (the same default tools/correlate_fmt.py uses)
            game = GAME_LOG
            if i < len(argv) and argv[i][:2] != '--':
                game = argv[i]
                i += 1
            continue
        if a in ('--log', '--doc', '--text', '--seed', '--find'):
            if i >= len(argv):
                print('%s needs a value' % a)
                return 2
            value = argv[i]
            i += 1
            if a == '--log':
                log = value
            elif a == '--doc':
                want = value
            elif a == '--text':
                text = value
            elif a == '--find':
                find = value
            else:
                account = value
            continue
        if a == '--clear':
            if i >= len(argv) or argv[i][:2] == '--':
                print('--clear needs an account id')
                return 2
            account = argv[i]
            i += 1
            clear = True
            continue
        print('unknown argument %r' % a)
        return 2

    if game is not None:
        if not os.path.isfile(game):
            print('no such game log: %s' % game)
            return 1
        if want is not None or account is not None or clear or text is not None:
            print('--doc/--seed/--text/--clear read the server log; '
                  'use --find with --game-log')
            return 2
        reqs = game_requests(game)
        if find is not None:
            return show_find(reqs, find, raw)
        return list_all(reqs, raw, game)
    if everything:
        return list_all(log_requests(log), raw, log)
    if find is not None:
        return show_find(log_requests(log), find, raw)

    reqs = cloud_requests(log)
    if not reqs:
        print('no cloud/execute request in %s' % log)
        return 1

    doc_key = want or DOC_KEY
    if account is not None or clear:
        if clear:
            _, path = srv._cloud_path(account, doc_key)
            if not os.path.exists(path):
                print('nothing stored for account %s / %s' % (account, doc_key))
                return 0
            text, writer = None, 'clear'
        elif text is None:
            hits = [r for r in reqs if r['docs'].get(doc_key)]
            if not hits:
                print('no captured %s document in %s - send/loot a tome in the '
                      'game, or pass --text' % (doc_key, log))
                return 1
            src = hits[-1]
            text = src['docs'][doc_key]
            writer = src['profile'] or 'capture'
            print('replaying %s from [%s] %s (%d chars)'
                  % (doc_key, src['ts'], src['method'], len(text)))
        else:
            writer = 'seed'
        path = seed(account, doc_key, text, writer)
        print('%s -> %s' % ('cleared' if text is None else 'seeded', path))
        served = served_body(account, doc_key)
        body = served.decode('utf-8')
        print('next read returns: %s'
              % (body if len(body) <= 240 else body[:240] + ' ...'))
        ok, why = walk_ok(served)
        print('client walk       : %s'
              % ('OK (tools/sim_reader.py)' if ok else
                 ('REJECTED %s' % why if ok is False else 'not checked')))
        return 0

    if want:
        hits = [r for r in reqs if r['docs'].get(want)]
        if not hits:
            print('no captured %s document in %s' % (want, log))
            return 1
        print('--- %s, %d chars, from [%s] ---'
              % (want, len(hits[-1]['docs'][want]), hits[-1]['ts']))
        print(hits[-1]['docs'][want])
        return 0

    for r in reqs:
        carried = ', '.join('%s=%d chars' % (k, len(v))
                            for k, v in r['docs'].items() if v) or 'no document'
        print('[%s] %-4s ops=%-8s docKeys=%-20s %s'
              % (r['ts'], r['method'], r['ops'], ','.join(r['keys']) or '-',
                 carried))
        if raw:
            for field in r['raw'].split('&'):
                if urllib.parse.unquote_plus(field.split('=', 1)[0]) not in (
                        'version', 'platform', 'title', 'language', 'auth'):
                    print('       %s' % field)
    print('--- %d cloud/execute request(s) in %s ---' % (len(reqs), log))
    return 0


if __name__ == '__main__':
    sys.exit(main())
