"""Reframe a DS2 log so it is readable in ONE batch, with a SWITCHABLE
log-level that hides noise without ever denying it.  (tools/reframe.py)

Problem this solves:
   * server logs (server/logs/round_*_stdout.txt) and the client log
     (ds2_tls.log) contain single lines up to ~5.7 KB (raw[tls] hex dumps,
     full POST bodies), which blow past the read window;
   * ds2_tls.log is ~7.4 M lines and can never load whole;
   * most lines are noise (SteamAPI_RunCallbacks pumps, select fdset churn,
     socket recv/send dumps) that drown the gift protocol.

What it does:
   * every emitted physical line STARTS with a normalised timestamp
     `YYYY-MM-DD HH:MM:SS`, even continuation lines of a wrapped record;
   * a logical line longer than --maxlen is truncated head+tail with a
     `[+N chars]` marker; a giant raw[tls] hex dump is previewed;
   * content is wrapped at --wrap columns so no physical line is too wide;
   * --since / --until (HH:MM:SS or full YYYY-MM-DD HH:MM:SS) and --grep
     narrow the stream; --tail keeps the last N matching logical lines;
   * --level selects how much noise is folded away (see below);
   * output is capped at --maxlines (default 2000) so it always fits one read.

LOG LEVELS (--level):
   full   (fallback) NOTHING is hidden. Every line passes through. This is the
          safety net - if a level drops something you need, re-run at `full`.
   info   hide the loud housekeeping pumps that carry no protocol meaning:
          `SteamAPI_RunCallbacks`, the `select ...` fdset/timeout/ret churn,
          `scan pass` bookkeeping, and bare socket `recv`/`send` byte dumps.
          Handshakes, PARSE, Running, Error, cloud ops, ARG/PEEK probes,
          greeting and connect lines are KEPT.
   gift   `info` PLUS only the gift-relevant signal: PARSE markers, `[GAME]
          Running` requests, `Error`/`Unable`, cloud op/execute lines, and the
          ARG/PEEK argument probes. Connection/handshake plumbing is folded.

Nothing is ever deleted: whatever a level folds is COUNTED and reported in the
header (`folded=...`) so you can always see the shape of what was hidden and
recover it at `full`.

Usage:
   python tools/reframe.py <logfile> [--level info] [--since 21:00] \
       [--until 21:40] [--grep cloud] [--tail 60] [--wrap 150] [--maxlen 400]

Examples:
   python tools/reframe.py server/logs/round_1021_stdout.txt --level gift
   python tools/reframe.py "E:/SteamLibrary/steamapps/common/Darksiders 2/ds2_tls.log" --since 19:45 --until 21:40 --level gift
"""
import argparse
import re
import sys
from collections import Counter, deque

TS_RE = re.compile(r'^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)(?:\.\d+)?\]\s*(.*)$')
HEX_RE = re.compile(r'raw\[tls\] recv \d+ bytes: ([0-9a-f]+)')


def _cat_info(body_l):
    """Foldable at level `info`: loud housekeeping with no protocol meaning."""
    if 'steamapi_runcallbacks' in body_l:
        return 'pump:RunCallbacks'
    if body_l.startswith('>> h_select') or body_l.startswith('select '):
        return 'select'
    if 'scan pass' in body_l:
        return 'scan'
    if re.match(r'--\s*(recv|send) sock', body_l):
        return 'socket'
    if re.match(r'>>\s*h_(recv|send) sock', body_l):
        return 'socket'
    return None


def _cat_gift(body_l):
    """Foldable at level `gift`: everything in `info` plus connection plumbing."""
    c = _cat_info(body_l)
    if c:
        return c
    if 'clienthello' in body_l or 'client hello' in body_l or 'serverhello' in body_l:
        return 'handshake'
    if 'handshake' in body_l:
        return 'handshake'
    if body_l.startswith('== connect') or body_l.startswith('== close'):
        return 'conn'
    if 'greeting' in body_l or '[thqc' in body_l:
        return 'greeting'
    return None


CLASSIFIERS = {'full': lambda b: None, 'info': _cat_info, 'gift': _cat_gift}
LEVELS = ('full', 'info', 'gift')


def _bound(when):
    """Normalise a window bound to a comparable form.

    Returns None, or a tuple:
      * ('time', 'HH:MM:SS')  for a time-only bound (`19:56`, `19:56:05`) -
        matched against the HH:MM:SS part of EVERY line, so it works across a
        multi-day log (the client ds2_tls.log spans weeks);
      * ('date', 'YYYY-MM-DD HH:MM:SS') for a full bound - matched whole.

    Time-only is the common case (`--since 19:45 --until 21:40`); it must NOT
    be pinned to the log's first date or a multi-day log yields matched=0.
    """
    if when is None:
        return None
    b = when.strip()
    if len(b) == 5 and b[2] == ':':
        return ('time', b + ':00')
    if len(b) == 8 and b[2] == ':' and b[5] == ':':
        return ('time', b)
    return ('date', b)


def _ge_since(ts, bound):
    """True when a line's ts is at/after the `since` bound."""
    kind, val = bound
    return ts[11:19] >= val if kind == 'time' else ts >= val


def _le_until(ts, bound):
    """True when a line's ts is at/before the `until` bound."""
    kind, val = bound
    return ts[11:19] <= val if kind == 'time' else ts <= val


def reframe(path, since, until, grep, tail, wrap, maxlen, maxlines, level):
    """Read one log, apply the switchable level filter, and shape it so it fits
    one read batch. Returns the full text (header + lines)."""
    classify = CLASSIFIERS[level]
    last_ts = '???????????????????'
    grep_re = re.compile(grep) if grep else None
    keep = deque(maxlen=tail if tail else maxlines)
    matched = 0
    truncated = 0
    folded = Counter()
    # window bounds are date-agnostic for time-only form (see _bound), so they
    # are computed once up front - NOT pinned to the log's first date.
    since_b = _bound(since)
    until_b = _bound(until)

    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for raw in f:
            raw = raw.rstrip('\n').rstrip('\r')
            m = TS_RE.match(raw)
            if m:
                ts, body = m.group(1), m.group(2)
                last_ts = ts
            else:
                ts, body = last_ts, raw

            if since_b and not _ge_since(ts, since_b):
                continue
            if until_b and not _le_until(ts, until_b):
                continue
            if grep_re and not grep_re.search(raw):
                continue

            matched += 1

            # switchable noise folding (COUNTED, never silently dropped)
            if level != 'full':
                cat = classify(body.lower())
                if cat is not None:
                    folded[cat] += 1
                    continue

            # collapse a giant hex dump to a preview (never useful in full)
            hm = HEX_RE.search(body)
            if hm and len(hm.group(1)) > 64:
                preview = hm.group(1)[:64]
                body = body[:hm.start(1)] + preview + '...[+%d hex]' % (len(hm.group(1)) - 64)

            # truncate any still-too-long logical line head+tail
            if len(body) > maxlen:
                half = maxlen // 2
                body = body[:half] + ' [+%d chars] ' % (len(body) - maxlen) + body[-half:]
                truncated += 1

            # wrap content; EVERY physical line carries the timestamp prefix
            if wrap and len(body) > wrap:
                chunks = [body[i:i + wrap] for i in range(0, len(body), wrap)]
            else:
                chunks = [body]
            rec = '\n'.join('%s %s' % (ts, c) for c in chunks)
            keep.append(rec)

    out_lines = list(keep)
    if len(out_lines) > maxlines:
        skipped = len(out_lines) - maxlines
        out_lines = out_lines[-maxlines:]
        out_lines.insert(0, '%s ...[+%d earlier lines omitted]' % ('?' * 19, skipped))
    shown = sum(r.count('\n') + 1 for r in out_lines)

    fold_desc = ''
    if level != 'full':
        fold_desc = ' folded=%d %s' % (
            sum(folded.values()),
            dict(folded.most_common()) if folded else {})
    header = ('# reframed %s\n# level=%s matched=%d shown_physical=%d truncated=%d '
              'window=[%s..%s] grep=%r%s\n' % (
                  path, level, matched, shown, truncated, since or '-', until or '-',
                  grep or '-', fold_desc))
    return header + '\n'.join(out_lines) + '\n'


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument('logfile')
    ap.add_argument('--since')
    ap.add_argument('--until')
    ap.add_argument('--grep')
    ap.add_argument('--tail', type=int, default=0)
    ap.add_argument('--wrap', type=int, default=150)
    ap.add_argument('--maxlen', type=int, default=400)
    ap.add_argument('--maxlines', type=int, default=2000)
    ap.add_argument('--level', choices=LEVELS, default='full')
    ap.add_argument('--out')
    a = ap.parse_args(argv)

    text = reframe(a.logfile, a.since, a.until, a.grep, a.tail,
                   a.wrap, a.maxlen, a.maxlines, a.level)
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print('wrote', a.out)
    else:
        sys.stdout.write(text)


if __name__ == '__main__':
    main(sys.argv[1:])

