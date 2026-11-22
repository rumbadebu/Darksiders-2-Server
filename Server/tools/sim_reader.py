"""Offline simulator of the client's response format-checker (x86-32 port).

Why this exists: the format gate lives inside the game
(Darksiders2.exe.orig), so a wrong response body costs a full game launch to
discover. Every primitive below was disassembled from the exe (VAs in
[brackets]) and is replayed here over a candidate body, so a response shape can
be validated - and its walk compared line-by-line with the live hook trace
(`>> ARG expect(...)` in ds2_tls.log) - BEFORE launching the game.

    primitive    VA        behaviour
    ---------    --------  --------------------------------------------------
    peek         0xaf7d65  next byte (0 at end of data)
    consume      0xaf7d27  advance n chars (fails past the end)
    skip_ws      0xaf7d86  skip spaces/tabs/CR/LF, always succeeds
    is_ws        0xaf7c12  whitespace test
    expect_opt   0xaf7db8  take `ch` if present and record the branch flag; a
                           MISMATCH IS NOT A FAILURE (logged as "expect")
    expect_req   0xaf7def  take `ch`, mismatch -> failure (logged as "check")
    open         0xaf7e16  container: '[' or '{' (neither -> failure)
    close        0xaf7eb2  matching ']' / '}' + separator
    sep          0xaf7e6b  after a value: ',' or a lenient ':' probe
    scan         0xaf7f06  scalar token scanner: optional '[' prefix, optional
                           '"' prefix, then digits/'.'/'-'
    named_int    0xaf8243  scan + range check + dword  (via 0xaf80e2)
    named_i8     0xaf8223  scan + byte                 (via 0xaf8092)
    named_i64    0xaf803e  scan + _atoi64 -> 8 bytes (calls 0xaf7f06 directly)
    named_str    0xaf8288  string reader: optional '[' / '"' prefix,
                           escape-aware (cmp al,0x5c at af83c6, \\uXXXX af83df)

KEY FINDING (2026-09-24, proven in-game): the pushed field-name literals
("result", "news", "resultCode", "data", "status", ...) are DEAD METADATA.
`0xaf803e` uses only its parser/out arguments and never compares a key, so every
response is read POSITIONALLY - a `{"status":0,...}` element can never parse.
Keep response bodies as bare positional arrays.

Usage:
    python tools/sim_reader.py                    # self-test (recorded bodies)
    python tools/sim_reader.py --trace '<body>' cloud|news
"""
import io
import json
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

WS = b' \t\r\n'
EOF = 0


class Reject(Exception):
    """Raised with the offset where the walk died."""

    def __init__(self, off, why):
        Exception.__init__(self, '%s @+%x' % (why, off))
        self.off = off
        self.why = why


class Sim(object):
    def __init__(self, body, sid='00000000'):
        self.b = body
        self.n = len(body)
        self.p = 0
        self.sid = sid
        self.trace = []
        self.ctx = None

    # --- primitives -------------------------------------------------------
    def peek(self):
        return self.b[self.p] if self.p < self.n else EOF

    def consume(self, cnt):
        if self.p + cnt > self.n:
            return False
        self.p += cnt
        return True

    def skip_ws(self, ret):
        while self.p < self.n and self.b[self.p] in WS:
            self.p += 1
        return True

    def log(self, kind, want, got, ret):
        self.trace.append('>> ARG %s(%s) want=0x%02x %s got=%s @+%x/%x ret=%s'
                          % (kind, self.sid, want, _ch(want),
                             ('0x%02x %s' % (got, _ch(got))) if got else 'EOF',
                             self.p, self.n, ret))

    def expect(self, kind, ch, flag_holder, ret, flag_ix):
        got = self.peek()
        self.log(kind, ch, got, ret)
        if kind == 'check':                      # 0xaf7def: required
            if got != ch:
                return False
            return self.consume(1)
        if got == ch:                            # 0xaf7db8: optional + flag
            flag_holder[flag_ix] = 1
            return self.consume(1)
        flag_holder[flag_ix] = 0
        return True

    # --- containers -------------------------------------------------------
    def open(self, ret_open='af7e47', ret_open2='af7e5a'):
        """0xaf7e16 - '[' (branch flag) or '{' (branch flag).

        The `ret=` values are the inner call sites (0xaf7e42 / 0xaf7e55 call
        `expect`), which is what the live hook trace prints.
        """
        self.skip_ws('af7e1e')
        ctx = {'sq': 0, 'cu': 0, 'parent': self.ctx}
        self.ctx = ctx
        if not self.expect('expect', 0x5b, ctx, ret_open, 'sq'):
            self.ctx = ctx['parent']
            return False
        if ctx['sq']:
            return True
        if not self.expect('expect', 0x7b, ctx, ret_open2, 'cu'):
            self.ctx = ctx['parent']
            return False
        if not ctx['cu']:
            self.ctx = ctx['parent']
            return False
        return True

    def sep(self, ret='af7eb0'):
        """0xaf7e6b - after a value: ',' or a lenient ':' probe."""
        if not self.skip_ws('af7e72'):
            return False
        if self.peek() == 0x2c:
            if not self.consume(1):
                return False
            return self.skip_ws('af7e97')
        self.expect('expect', 0x3a, {}, ret, 'x')
        return True

    def close(self, ret_sq='af7edf', ret_cu='af7ef1'):
        """0xaf7eb2 - matching ']' / '}' then a separator."""
        ctx = self.ctx
        self.ctx = ctx['parent']
        if not self.skip_ws('af7ec4'):
            return False
        if ctx['sq']:
            if not self.expect('check', 0x5d, ctx, ret_sq, 'sq'):
                return False
        if ctx['cu']:
            if not self.expect('check', 0x7d, ctx, ret_cu, 'cu'):
                return False
        return self.sep()                          # 0xaf7e6b's ':' probe (ret af7eb0)


    # --- values -----------------------------------------------------------
    def scan(self, ret_lb='af7f47'):
        """0xaf7f06 - scalar token text (optionally '['- and/or '"'-wrapped)."""
        if self.p >= self.n:
            raise Reject(self.p, 'scan: no data')
        self.skip_ws('af7f2e')
        probe = self.p                              # the trace's @+NN offset
        sq = {}
        if not self.expect('expect', 0x5b, sq, ret_lb, 'x'):
            raise Reject(probe, 'scan: bracket probe')
        self.skip_ws('af7f50')
        if self.peek() == 0x22:
            if not self.consume(1):
                raise Reject(probe, 'scan: open quote')
            quoted = True
        else:
            quoted = False
        self.skip_ws('af7f78')
        out = bytearray()
        while True:
            ch = self.peek()
            if ch and (0x30 <= ch <= 0x39 or ch == 0x2e):
                out += bytes([ch])
                if not self.consume(1):
                    raise Reject(probe, 'scan: digit')
                continue
            if ch == 0x2d and not out:            # leading '-'
                out += bytes([ch])
                if not self.consume(1):
                    raise Reject(probe, 'scan: sign')
                continue
            break
        if not out:
            raise Reject(probe, 'scan: not a scalar (got %s)' % _ch(ch))
        if quoted:
            self.skip_ws('af7feb')
            if self.peek() == 0x22 and not self.consume(1):
                raise Reject(probe, 'scan: close quote')
        if sq.get('x'):
            self.skip_ws('af8011')
            if not self.expect('check', 0x5d, {}, 'af801e', 'x'):
                raise Reject(probe, 'scan: close bracket')
        self.skip_ws('af8027')
        if not self.sep():
            raise Reject(probe, 'scan: separator')
        return int(out)

    def value_int(self, ret_lb='af7f47'):
        """0xaf8243 (dword) / 0xaf8223 (byte): scalar + range check."""
        return self.scan(ret_lb)

    def value_i64(self, ret_lb='af7f47'):
        """0xaf803e: scalar -> _atoi64."""
        return self.scan(ret_lb)

    def value_str(self, ret_lb='af82e2'):
        """0xaf8288 - string reader (escape-aware, stops at the delimiter)."""
        self.skip_ws('af82c6')
        sq = {}
        if not self.expect('expect', 0x5b, sq, ret_lb, 'x'):
            raise Reject(self.p, 'str: bracket probe')
        self.skip_ws('af82eb')
        quoted = {}
        if not self.expect('expect', 0x22, quoted, 'af830e', 'x'):
            raise Reject(self.p, 'str: quote probe')
        out = bytearray()
        if quoted.get('x'):
            while True:
                ch = self.peek()
                if ch in (0, 0x22):
                    break
                if ch == 0x5c:                    # escape
                    if not self.consume(1):
                        raise Reject(self.p, 'str: escape')
                    nxt = self.peek()
                    if nxt == 0x75:               # \uXXXX
                        if not self.consume(5):
                            raise Reject(self.p, 'str: unicode')
                        continue
                    if nxt == ord('n'):
                        out += b'\n'
                    elif nxt == ord('t'):
                        out += b'\t'
                    elif nxt == ord('r'):
                        out += b'\r'
                    elif nxt:
                        out += bytes([nxt])
                    if not self.consume(1):
                        raise Reject(self.p, 'str: escape char')
                    continue
                out += bytes([ch])
                if not self.consume(1):
                    raise Reject(self.p, 'str: char')
            if self.peek() == 0x22 and not self.consume(1):
                raise Reject(self.p, 'str: close quote')
        else:                                     # bare token to a delimiter
            while self.peek() not in (0, 0x22, 0x2c, 0x3a, 0x5d, 0x7d):
                out += bytes([self.peek()])
                if not self.consume(1):
                    break
            out = out.strip()
        if sq.get('x'):
            self.skip_ws('af85fb')
            if not self.expect('check', 0x5d, {}, 'af8603', 'x'):
                raise Reject(self.p, 'str: close bracket')
            self.skip_ws('af860b')
        if not self.sep():
            raise Reject(self.p, 'str: separator')
        return out.decode('latin-1')

    # --- the checkers -----------------------------------------------------
    def doc_record(self):
        """0xb32450 - one document: [status, document, policy, time, name]."""
        if not self.open():
            raise Reject(self.p, 'doc: open')
        self.value_int()                          # status (0xb3240c, byte)
        self.value_str()                          # document
        self.value_int()                           # docPolicyBits
        self.value_i64()                          # lastModifiedTime
        self.value_str()                          # lastModifiedProfileName
        if not self.close():
            raise Reject(self.p, 'doc: close')
        return True

    def doc_list(self):
        """0xb325d2 - the `data` list: '[' / '{' then doc records.

        Brackets are consumed BY THIS READER (no 0xaf7e16), and a ']' only ends
        a '['-opened list ('}' a '{'-opened one): see the flag test at 0xb326c3.
        """
        sq, cu = {}, {}
        self.skip_ws('b325fc')
        if not self.expect('expect', 0x5b, sq, 'b32619', 'x'):
            raise Reject(self.p, 'data: bracket')
        if not sq.get('x'):
            if not self.expect('expect', 0x7b, cu, 'b32629', 'x'):
                raise Reject(self.p, 'data: brace')
            if not cu.get('x'):
                raise Reject(self.p, 'data: not a container')
        opened = '[' if sq.get('x') else '{'
        closer = 0x5d if opened == '[' else 0x7d
        while True:
            if self.p >= self.n:
                raise Reject(self.p, 'data: truncated')
            ch = self.peek()
            if ch in (0x5d, 0x7d):                # ']' or '}'
                if ch != closer:
                    raise Reject(self.p, 'data: bracket mismatch')
                if not self.consume(1):
                    raise Reject(self.p, 'data: end')
                break
            self.doc_record()
        if not self.sep():
            raise Reject(self.p, 'data: separator')
        return True

    def check_cloud(self):
        """0xb3280a - CallCloud: [ resultCode, data ]."""
        if not self.open():
            raise Reject(self.p, 'cloud: open')
        self.value_int()                          # resultCode
        self.doc_list()                           # data
        if not self.close():
            raise Reject(self.p, 'cloud: close')
        return True

    def check_news(self):
        """0xb32012 - getNews: [ result, news[] ]."""
        if not self.open():
            raise Reject(self.p, 'news: open')
        self.value_int()                          # result
        sq, cu = {}, {}                           # 0xb31f0a - the news list
        self.skip_ws('b31f34')
        if not self.expect('expect', 0x5b, sq, 'b31f51', 'x'):
            raise Reject(self.p, 'news: bracket')
        if not sq.get('x'):
            if not self.expect('expect', 0x7b, cu, 'b31f61', 'x'):
                raise Reject(self.p, 'news: brace')
            if not cu.get('x'):
                raise Reject(self.p, 'news: not a container')
        closer = 0x5d if sq.get('x') else 0x7d
        while True:
            if self.p >= self.n:
                raise Reject(self.p, 'news: truncated')
            ch = self.peek()
            if ch in (0x5d, 0x7d):
                if ch != closer:
                    raise Reject(self.p, 'news: bracket mismatch')
                if not self.consume(1):
                    raise Reject(self.p, 'news: end')
                break
            raise Reject(self.p, 'news: item reader 0xb31c90 not modelled '
                                 '(serve an EMPTY news list)')
        if not self.sep():
            raise Reject(self.p, 'news: separator')
        if not self.close():
            raise Reject(self.p, 'news: close')
        return True


def run(body, checker, sid='00000000'):
    """-> (ok, walk, error-or-None)"""
    sim = Sim(body, sid)
    try:
        if checker == 'cloud':
            sim.check_cloud()
        else:
            sim.check_news()
        return True, sim.trace, None
    except Reject as e:
        return False, sim.trace, e


def _ch(b):
    return repr(chr(b)) if b else "''"

# ---------------------------------------------------------------------------
# self-test: bodies captured from the live game (ds2_tls.log, 2026-09-24)
# ---------------------------------------------------------------------------
def _store_doc():
    """The real Stats document the game wrote (read-only)."""
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, '..', 'server', 'data', 'cloud', '100000002',
                        'GameToWeb.json')
    try:
        with open(path, 'r', encoding='utf-8') as f:
            doc = json.load(f)
        return doc.get('values', {}).get('GameToWeb') or ''
    except (OSError, ValueError):
        return ''


def main():
    if '--trace' in sys.argv:
        i = sys.argv.index('--trace')
        body = sys.argv[i + 1].encode('utf-8')
        kind = sys.argv[i + 2]
        ok, walk, err = run(body, kind)
        print('\n'.join(walk))
        print('=> %s' % ('ACCEPT' if ok else 'REJECT: %s' % err))
        return 0 if ok else 1

    doc = _store_doc() or "<Stats> ... </Stats>"
    keyed = json.dumps([0, [{'status': 0, 'document': doc, 'docPolicyBits': 1,
                             'lastModifiedTime': 1790218098,
                             'lastModifiedProfileName': 'Player42002'}]],
                       separators=(',', ':'))
    good = json.dumps([0, [[0, doc, 1, 1790218098, 'Player42002']]],
                      separators=(',', ':'))
    cases = [
        # label, body, checker, expect_ok, expect_dying_offset
        ('cloud `[0,[]]` (PROVEN accepted in-game 05:47:01)',
         b'[0,[]]', 'cloud', True, None),
        ('cloud + POSITIONAL doc record (the FIX)',
         good.encode('utf-8'), 'cloud', True, None),
        ('cloud + KEYED doc record (failed in-game 05:48:18)',
         keyed.encode('utf-8'), 'cloud', False, 5),
        ('cloud + old user record `[[["X"],aid,pid]]` (model-derived offset)',
         b'[0,[[["X"],100000002,100000002]]]', 'cloud', False, 5),
        ('news `[0,[]]` (the FIX)', b'[0,[]]', 'news', True, None),
        ('news named object `[{"result":0,"news":[]}]` (failed 05:48:17)',
         b'[{"result":0,"news":[]}]', 'news', False, 1),
        ('login-shaped body must NOT fit the news checker',
         b'[0,[],[],[],[]]', 'news', False, None),
    ]
    fails = 0
    for label, body, kind, want_ok, want_off in cases:
        ok, walk, err = run(body, kind)
        verdict = 'ACCEPT' if ok else 'REJECT @+%x (%s)' % (err.off, err.why)
        good_case = (ok == want_ok) and (want_off is None or
                                         (err is not None and err.off == want_off))
        if not good_case:
            fails += 1
        print('%-4s %-58s %s' % ('ok' if good_case else 'FAIL', label, verdict))
        if not good_case:
            print('     want %s%s'
                  % ('ACCEPT' if want_ok else 'REJECT',
                     '' if want_off is None else ' @+%x' % want_off))
            for ln in walk[-5:]:
                print('       ' + ln)
    print()
    if fails:
        print('%d case(s) mismatched' % fails)
        return 1
    print('all %d cases as expected (exit 0)' % len(cases))
    return 0


if __name__ == '__main__':
    sys.exit(main())

