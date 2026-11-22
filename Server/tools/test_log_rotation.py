"""Regression test for the size-based log rotation (server/ds2_server.py).

Covers _rotate_if_big: a file at/over LOG_MAX_BYTES is renamed to
<stem>_<n>.log, a banner is returned, repeated calls keep incrementing the
index, and a small file is left alone. Run:
    python tools/test_log_rotation.py
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'server'))
import ds2_server as srv  # noqa: E402


def main():
    ok = 0
    fail = 0

    def check(name, cond):
        nonlocal ok, fail
        if cond:
            ok += 1
            print('PASS %s' % name)
        else:
            fail += 1
            print('FAIL %s' % name)

    srv.LOG_MAX_BYTES = 200  # tiny limit: no big files needed
    d = tempfile.mkdtemp(prefix='ds2_logrot_')
    try:
        p = os.path.join(d, 'requests.log')
        with open(p, 'w') as f:
            f.write('x' * 250)
        rot = srv._rotate_if_big(p)
        check('big file rotates',
              rot is not None and 'requests_1.log' in rot)
        check('rotated file exists',
              os.path.exists(os.path.join(d, 'requests_1.log')))
        check('primary gone after rotate', not os.path.exists(p))

        with open(p, 'w') as f:
            f.write('x' * 250)
        rot2 = srv._rotate_if_big(p)
        check('second rotation increments index',
              rot2 is not None and 'requests_2.log' in rot2)

        with open(p, 'w') as f:
            f.write('small')
        check('small file not rotated', srv._rotate_if_big(p) is None)
        check('small file kept', os.path.exists(p))
    finally:
        shutil.rmtree(d, ignore_errors=True)

    print('%d passed, %d failed' % (ok, fail))
    return 1 if fail else 0


if __name__ == '__main__':
    sys.exit(main())
