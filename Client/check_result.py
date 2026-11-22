"""Verdict helper: summarize the LAST game session from server + client logs.

After a hypothesis test run (game launched, exchange opened once, game closed),
run this to get a one-line verdict:
    python check_result.py

It finds the last '=== new session' marker in the client TLS proxy log, then
counts, for that window:
  - client side: PEM_read_bio_X509 failures (the regression marker)
  - server side: completed TLS handshakes vs tls=False plain connections
"""
import glob
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # Darksiders2Server
WS = os.path.dirname(ROOT)                                           # BrokenCopies root
REQ = os.path.join(WS, 'server', 'logs', 'requests.log')
GAMELOGS = glob.glob(r'E:\SteamLibrary\steamapps\common\Darksiders 2\ds2_tls*.log')

SESSION_RE = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})[^\]]*\] === new session')


def last_session_ts():
    best = ''
    for path in GAMELOGS:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                m = SESSION_RE.search(line)
                if m and m.group(1) > best:
                    best = m.group(1)
    return best


def main():
    ts = last_session_ts()
    if not ts:
        print('no game session found in proxy logs')
        return
    print('last game session: %s' % ts)

    pem = 0
    for path in GAMELOGS:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                if line[1:20] >= ts and 'PEM_read_bio_X509 failed' in line:
                    pem += 1
    print('client PEM_read_bio_X509 failures: %d' % pem)

    hs = 0
    plain = 0
    served = 0
    with open(REQ, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if line[1:20] < ts:
                continue
            if '>> TLS handshake OK' in line:
                hs += 1
            if 'tls=False' in line:
                plain += 1
            if line[1:20] >= ts and ' <- 200 ' in line:
                served += 1
    print('server TLS handshakes: %d, plain (tls=False) connections: %d, requests served: %d'
          % (hs, plain, served))

    print()
    if hs > 0 and pem == 0:
        print('VERDICT: TLS WORKS - hypothesis CLEARED the failure')
    elif pem > 0 or (plain > 0 and hs == 0):
        print('VERDICT: TLS STILL BROKEN - regression reproduces')
    else:
        print('VERDICT: INCONCLUSIVE - was the game actually launched and logged in?')


if __name__ == '__main__':
    main()