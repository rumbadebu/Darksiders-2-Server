"""
Darksiders 2 - local THQ Connect / GDS server emulator.

Revives the online functionality of Darksiders 2 (2012) after the THQ
servers were shut down. Implements the endpoints discovered in the game
executable:

    /version                              connection health check
    /thqconnect/login/initSession         begin login session
    /thqconnect/login/platformLogin       Steam ticket login -> profile
    /thqconnect/login/readsession         session validity check
    /thqconnect/login/ssoStatus           SSO sign-in status
    /thqconnect/news/getNews              news feed
    /thqconnect/cloud/execute             cloud document ops:
                                          SET_PERM MAP_SET MAP_GET MAP_INC MAP_REMOVE
    /thqconnect/cloud/getKeys             list cloud document keys
    /thqconnect/url/getURL                extension URL lookup
    /thqconnect/tunnel/tunnel             long-poll notification tunnel
    /gds/...                              game data service RPC
    /admin/...                            local management API

All traffic is logged to console and logs/requests.log so the wire format
can be refined iteratively while testing against the real game.

Run:  python server/ds2_server.py
"""
import base64
import hashlib
import json
import os
import re
import select
import socket
import ssl
import struct
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CERT_DIR = os.path.join(ROOT, 'server', 'certs')
DATA_DIR = os.path.join(ROOT, 'server', 'data')
LOG_DIR = os.path.join(ROOT, 'server', 'logs')
CLOUD_DIR = os.path.join(DATA_DIR, 'cloud')
# 2026-09-27: on the real service a docKey the client REGISTERED (`ops=SET,SET&
# keys=&values=` at login) exists even before it holds text, so a read answers
# with a record whose document is empty (`[0,[[0,"",policy,time,name]]]`).
# This emulator answers `[0,[]]` for it (no record at all: `doc_content` treats
# a blank value as "no document"), and the exchange UI reports a connection
# error with that answer - which is why serving the empty record is being A/B-ed
# in-game. Off by default so nothing else changes:
#     $env:DS2_EMPTY_DOC='1'   (or flip ds2_server.EMPTY_DOC_RECORD) + restart
EMPTY_DOC_RECORD = os.environ.get('DS2_EMPTY_DOC') == '1'
# 2026-10-05: verbose per-request tracing for the exchange, kept permanently and
# off by default. The regular log truncates bodies to 2000 chars and the reply to
# 400, which hides the one thing the receive path turns on: what the client asks
# for AFTER a read (e.g. whether it ever names a gift id in `keys=`, or sends
# the placeholder `key` in a MAP_REMOVE). Traced to its own file so it can be
# switched on for one experiment and left off, without editing any code:
#     $env:DS2_TRACE='1'   (or flip ds2_server.TRACE_CLOUD) + restart
TRACE_CLOUD = os.environ.get('DS2_TRACE') == '1'
_trace_path = os.path.join(LOG_DIR, 'trace.log')


def trace(msg):
    """Full-length request/response tracing (see TRACE_CLOUD). No-op if off."""
    if not TRACE_CLOUD:
        return
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with _log_lock:
        try:
            rot = _rotate_if_big(_trace_path)
            with open(_trace_path, 'a', encoding='utf-8') as f:
                if rot:
                    f.write('[%s] %s\n'
                            % (time.strftime('%Y-%m-%d %H:%M:%S'), rot))
                f.write(line + '\n')
        except OSError:
            pass
# 2026-09-28 23:23 - WHY THE RECEIVE PATH FAILED: `[GAME] Error: Unable to process
# string: Qk9E...` right after `CallCloudAsync - response good`. That error comes
# from THQServer::CloudInternal (string pool 0xed928c-0xed93d4: SetDocument,
# RemoveDocument, RemoveMapKey, SetPermission, GetDocument, SetMapKey, IncMapKey,
# <the error>, GetMap), and the parser that raises it (0xb08b07) starts by
# checking for '[' (0x5b) or '{' (0x7b) - i.e. the `document` slot must be the
# document's MAP as JSON text, not a single stored value. Our record served the
# bare value (the gift's base64 blob), which no map parser can read.
# With DS2_DOC_MAP=1 the whole key->value map goes back out instead
# (`{"TomeGift":"","5394434090946791192":"Qk9E..."}`), which is also why the
# client's string reader is escape-aware (0xaf8288 handles \" and \uXXXX): the
# map arrives as a JSON string INSIDE the record's JSON.
DOC_MAP = os.environ.get('DS2_DOC_MAP') == '1'
# 2026-10-08 DS2_GET_MAP - THE RECEIVE PATH. The client's only base64 decoder
# (0xb4394c) has exactly one call site: the entry walk at 0xb44f96, which
# iterates parsed document ENTRIES (stride 0x30 = {key {ptr,size}, value
# char* at +0x10}) and base64-decodes each VALUE - i.e. it eats a parsed
# map of `<giftId> -> base64 blob` and nothing else. doc_content()'s bare
# single value yields no entries, so the walk never runs: the received gift
# never reaches the exe's `onGiftReceived` (0x722ee3) -> script `addGift`
# -> the save. The inbox then renders the save's own SENT entry, and the
# sender name shown is the logged-in player's own.
# With this on, the plain GET route serves the gift MAP in the selected
# shape (see doc_map_json); GameToWeb and every single-value doc keep
# serving raw (doc_map_json returns None for them).
#   DS2_GET_MAP=1 | pairs  -> [["<giftId>","Qk9E..."], ...]   <- the default
#                             (the shape the pre-split docshape rotation
#                             served on login reads)
#   DS2_GET_MAP=obj        -> {"<giftId>":"Qk9E...", ...}
#                             (served in-game 2026-10-09, three launches:
#                             accepted, but NO save entry appeared)
# The object verdict is why pairs is now the default; the plain-GET document
# text never reaches the hooked JSON parser (only MAP_GET inner docs do), so
# the save forensics - not the client log - is the oracle for this switch.
# Off unless DS2_GET_MAP is set; in-game two-account test decides:
#     $env:DS2_GET_MAP='1'   (or flip ds2_server.GET_MAP) + restart
GET_MAP = {'1': 'pairs', 'pairs': 'pairs', 'obj': 'obj'}.get(
    os.environ.get('DS2_GET_MAP', '').strip().lower(), '')
# 2026-10-09 DS2_MAPGET_MAP - serve the gift blob map on the MAP_GET route.
# WHY this and not GET_MAP: the receive path is the MAP_GET route, NOT the plain
# GET. The base64 decoder (0xb4394c) has exactly one call site - the entry walk
# at 0xb44f96 - and it is fed by the CloudInternal map parser (0xb08b07), the
# parser all 54 `Unable to process string` verdicts prove runs on MAP_GET (a raw
# blob there is rejected because 0xb08b07 needs '[' or '{'). The plain GET blob
# never reaches that parser, so GET_MAP (blob on plain GET) can never ingest.
# MAP_GET currently serves the id->int index `[{"key":<id>,"data":0}]`: it
# PARSES, but its value is the integer 0, so the walk decodes nothing and the
# received gift never reaches onGiftReceived -> addGift. Session 9 (round_1016)
# proved it: gloves delivered on GET,GET + MAP_GET index -> inbox EMPTY.
# With this on, a gift map doc is served on MAP_GET in the selected shape
# instead of the integer index, so the walk base64-decodes the blob -> ingests
# -> the inbox renders the row.
#   DS2_MAPGET_MAP=1 | pairs -> [["<giftId>","Qk9E..."], ...]   <- default
#   DS2_MAPGET_MAP=obj       -> {"<giftId>":"Qk9E...", ...}
# SHAPE VERDICT (in-game 2026-10-09): BOTH shapes ERROR on MAP_GET, so this lever
# is now FORCED OFF (MAPGET_MAP = '' below). obj errored in round_1018; pairs
# errored in round_1020 (20:56, inbox access error). The frozen docshape.txt
# baseline (good=103 bad=15) already had the answer: the MAP_GET `data` slot is
# read as INT64 ONLY - `{"key":"%K%","data":{"value":"%V%"}}` and `["%V%"]`
# (every blob-in-document variant) were REJECTED, while int64 `data` parsed
# clean. So a base64 blob can NEVER live in the MAP_GET document, and ANY
# served_mapget shape crashes inbox access. The gift's ingest path is the PLAIN
# GET route (DS2_GET_MAP), where round_1015 proved the attachment lands in the
# save - not MAP_GET. Kept here, off, as a record of the dead end.
MAPGET_MAP = ''  # forced off: see verdict above (blob cannot enter the MAP_GET doc)
# 2026-10-05: honour the collect the client actually sends. Opening the exchange
# makes the game CLEAR what it just read, with one placeholder `key` PER GIFT:
#     keys=key,key,key   docKeys=TomeGift,TomeGift,TomeGift   (3 gifts)
#     keys=key,key       docKeys=TomeGift,TomeGift             (2 gifts)
#     keys=key           docKeys=TomeGift                       (1 gift)
# i.e. `keys` is INDEX-ALIGNED with the gifts shown, one entry per gift - not a
# gift id (the client has never once sent one) and not a fixed pair. Nothing can
# match that literal, so no gift is ever removed, the index never changes, and
# the "you have gifts" notification fires only on the first open.
# With this on, a MAP_REMOVE made only of placeholders clears the gifts the
# client was looking at. Off by default because it DELETES gift entries:
#     $env:DS2_COLLECT='1'   (or flip ds2_server.COLLECT_REMOVES) + restart
COLLECT_REMOVES = os.environ.get('DS2_COLLECT') == '1'
# The literal the client sends instead of a gift id (see above).
COLLECT_PLACEHOLDER_KEYS = ('key',)
# Which shape the document is served in while DOC_MAP is on. Read from
# `server/docshape.txt` on EVERY request, so a shape can be changed while the
# server and the game are both running (edit the file, reopen the exchange):
#     pairs    [["5394434090946791192","Qk9E..."]]   <- positional, engine style
#     map      {"5394434090946791192":"Qk9E..."}
#     map_list {"5394434090946791192":["Qk9E..."]}
# The client trace decides: its parser (0xb08b07) accepts '[' or '{', and inside
# an OBJECT a string is a KEY (it demanded a second ':' after our value), while a
# LIST element is a value. 0xb05f18 interprets each element with a caller tag.
DOC_SHAPE_FILE = os.path.join(ROOT, 'server', 'docshape.txt')
# Slot overrides for the other four record slots 0xb32450 reads - see doc_slot().
# Off unless DS2_DOC_SLOT=1, so the sweep file can never leak into another run
# (or a test process) that did not ask for it.
DOC_SLOT = os.environ.get('DS2_DOC_SLOT') == '1'
DOC_SLOT_FILE = os.path.join(ROOT, 'server', 'docslot.txt') if DOC_SLOT else ''
_docslot_i = [0]
# A shape is a TEMPLATE for one entry (`%K%` = key, `%V%` = value) or one of the
# aliases below. The document is the entry template joined by ',' inside the
# template's own brackets, so a candidate can be tried by editing the file while
# the server AND the game run (no restart):
#     [["%K%","%V%"]]          pairs       (default)
#     [["%K%",["%V%"]]]        pair_list   value as a LIST
#     {"%K%":["%V%"]}          map_list    object, value as a LIST
#     {"%K%":"%V%"}            map
#     {"%K%":{"Val":"%V%"}}    map_val     object value named `Val`
# The client trace decides: at the VALUE slot its reader probes '[' first (only
# `[`, strings and numbers are tried inside a list), while a bare string there
# makes it fall back to reading a KEY and demand another ':' - so the value is a
# container, not a string.
SHAPE_ALIASES = {
    'pairs': '["%K%","%V%"]',
    'pair_list': '["%K%",["%V%"]]',
    'map': '"%K%":"%V%"',
    'map_list': '"%K%":["%V%"]',
    'map_val': '"%K%":{"Val":"%V%"}',
    # 2026-10-01: the element interpreter reads two NAMED members from each entry
    # ("key" @0x13e54a4 and "data" @0x13e90bc) and the parser is called with the
    # type tag "Object" (0x13c5271+3), so an entry is an object with key/data.
    'key_data': '[%E%] => {"key":"%K%","data":"%V%"}',
    'key_data_list': '[%E%] => {"key":"%K%","data":["%V%"]}',
    'key_data_obj': '{%E%} => "%K%":{"key":"%K%","data":"%V%"}',
    # the client's own guest/sender order: the parser is entered as 'Object'
    'obj_key_data': '[%E%] => {"Object":{"key":"%K%","data":"%V%"}}',
}
DOC_SHAPES = tuple(SHAPE_ALIASES)
_docshape_i = [0]                 # rotation cursor (see doc_shape)


def doc_shape():
    """The spec for THIS request.

    `server/docshape.txt` may list SEVERAL specs (one per line, `#` comments):
    they are used round-robin, one per request, so a batch of candidates can be
    A/B-ed by simply reopening the exchange - no restart, no relaunch, no editing
    between attempts. The chosen spec is written to the request log
    (`cloud ... shape=<spec>`), so each attempt can be matched to whether the
    client accepted it.
    """
    try:
        with open(DOC_SHAPE_FILE, 'r', encoding='utf-8') as fh:
            specs = [l.strip().split('#')[0].strip() for l in fh]
    except OSError:
        return 'pairs'
    specs = [s for s in specs if s]
    if not specs:
        return 'pairs'
    i = _docshape_i[0]
    _docshape_i[0] = i + 1
    return specs[i % len(specs)]


def doc_slot(peek=False):
    """Overrides for record slots 0/2/3/4, from server/docslot.txt.

    One line per request, round-robin, so a SINGLE game launch can sweep the
    slots `0xb32450` reads - the payload may live outside slot 1 (the document),
    and the document is proven to be only an id -> int64 index.  A line is a list
    of `slot=value` pairs, e.g. `0=1` or `0=2,4=playerB`; the value is used
    as-is (JSON-encoded by the body writer), so a string needs quotes:
    `4="playerB"`.  `peek=True` does not advance the rotation (for logging).

    Returns {slot_index: value} for the line in effect on this request.
    """
    try:
        with open(DOC_SLOT_FILE, 'r', encoding='utf-8') as fh:
            lines = [l.strip() for l in fh]
    except OSError:
        return {}
    specs = [l.split('#')[0].strip() for l in lines]
    specs = [s for s in specs if s]
    if not specs:
        return {}
    i = _docslot_i[0]
    if not peek:
        _docslot_i[0] = i + 1
    spec = specs[i % len(specs)]
    out = {}
    for item in spec.replace(',', ' ').split():
        if '=' not in item:
            continue
        slot, _, raw = item.partition('=')
        try:
            idx = int(slot)
        except ValueError:
            continue
        try:
            out[idx] = json.loads(raw)          # numbers bare, strings quoted
        except ValueError:
            out[idx] = raw                     # bare text (name, id)
    return out


def render_doc(values, spec):
    """Serialise a document's key->value map using a template.

    `%K%`/`%V%` are replaced per entry with the JSON-escaped key/value *without*
    surrounding quotes (the template supplies them). Two forms:
      * an ENTRY template - entries are comma-joined and the container is inferred
        from its first character (`[` -> list, `"` -> object);
      * `DOC => ENTRY` - `%E%` in DOC receives the joined entries, so nested shapes
        like a list of `{"key":...,"data":...}` objects can be written directly.
    """
    items = sorted((str(k), str(v)) for k, v in values.items() if str(v))
    if not items:
        # a doc with no real entries: the empty document the client accepts
        return ''
    spec = SHAPE_ALIASES.get(spec, spec)
    if '=>' in spec:
        doc_tpl, entry_tpl = [p.strip() for p in spec.split('=>', 1)]
    else:
        doc_tpl, entry_tpl = None, spec
    literal = False
    if '%K%' not in entry_tpl and '%N%' not in entry_tpl:
        # `%K%` (the gift id) or `%N%` (an ordinal, 1..N) makes a spec
        # per-entry. A spec may also hard-code both the key and the value - the
        # client names its own map entry `key` (see the MAP_REMOVE audit in
        # docshape.txt), so a literal spec like `[{"key":"key","data":0}]` has no
        # placeholder. A literal spec is emitted ONCE: the game sends exactly one
        # `key` per TomeGift document, so the document holds a single entry no
        # matter how many gifts are stored. Only a spec with neither a
        # placeholder nor a literal entry falls back to `pairs`.
        probe = entry_tpl.strip()
        # a literal spec supplies the WHOLE document, so it must start AND end
        # with a matching container - `[...]` (the shape the client accepts) or
        # `{...}`. Anything else is a typo'd spec and falls back to `pairs`.
        if not ((probe.startswith('[') and probe.endswith(']'))
                or (probe.startswith('{') and probe.endswith('}'))):
            log('  !! unusable docshape %r, using pairs' % spec)
            doc_tpl, entry_tpl = None, SHAPE_ALIASES['pairs']
        else:
            literal = True
    elif doc_tpl is None and entry_tpl.lstrip().startswith('[{"'):
        # The spec has placeholders AND spells out its own container as a full
        # object list, e.g. `[{"key":"%K%","data":0}]`. Those brackets are the
        # DOCUMENT, not part of one entry, so strip them and emit the entries
        # between - otherwise every entry gets wrapped again and the result nests
        # ([[{...}],[{...}]]), which the client cannot read. Deliberately narrow:
        # a bare pair spec (`"k":v`, the `pairs` alias) must NOT match here, or
        # its quotes would be stripped and the container inference would break.
        stripped = entry_tpl.strip()
        doc_tpl, entry_tpl = '[%E%]', stripped[1:-1].strip()
    entries = ','.join(entry_tpl.replace('%K%', json.dumps(k)[1:-1])
                       .replace('%N%', str(n + 1))
                       .replace('%V%', json.dumps(v)[1:-1])
                       for n, (k, v) in enumerate(items[:1 if literal else None]))
    if doc_tpl is not None:
        return doc_tpl.replace('%E%', entries)
    if literal:
        # the spec already spelled out the whole document (it started with '[' or
        # '{'), so it IS the container - wrapping it again would nest it.
        return entry_tpl
    # container inferred from the entry: `"k":v` entries go in an OBJECT, anything
    # else (a positional pair, or a `{...}` element) goes in a LIST
    if entry_tpl[0] == '"':
        return '{' + entries + '}'
    return '[' + entries + ']'
# 2026-09-19 ORACLE: live-editable response bodies. When
#   server/oracle/<endpoint>.json  exists and is non-empty, its raw bytes are
# served verbatim for that endpoint (no schema wrapping). The game retries
# every ~2-3 s, so a shape can be edited here and observed on the next retry
# without restarting the server or relaunching the game.
ORACLE_DIR = os.path.join(ROOT, 'server', 'oracle')

CONFIG_PATH = os.path.join(ROOT, 'server', 'config.json')
DEFAULT_CONFIG = {
    "http_port": 80,
    "https_ports": [443, 8443],
    "enable_tls": True,
    "use_client_ca_cert": True,
    "verify_client_cert": False,
    "tunnel_poll_timeout_s": 25,
    "default_profile_name": "Death",
    "news": []
}

_log_lock = threading.Lock()
_req_log_path = os.path.join(LOG_DIR, 'requests.log')

# 2026-10-11: size-based rotation for the server logs (requests.log, trace.log)
# so every file stays small enough to read in one bounded pass (reframe.py
# --tail). Env DS2_LOG_MAX_MB overrides the 4 MB default. Rotated files keep
# the name <stem>_<n>.log and the rotation is logged as a banner line in the
# fresh file, so it is always traceable. round_*_stdout.txt (the console
# capture) rotates per launch instead: every server start opens a new one.
LOG_MAX_BYTES = int(os.environ.get('DS2_LOG_MAX_MB') or '4') * 1024 * 1024


def _rotate_if_big(path):
    """Rename path to <stem>_<n>.log when it reached LOG_MAX_BYTES; return the
    banner text for the fresh file, or None. Never raises."""
    try:
        if os.path.getsize(path) < LOG_MAX_BYTES:
            return None
        stem = path[:-4] if path.endswith('.log') else path
        n = 1
        while True:
            cand = '%s_%d.log' % (stem, n)
            if not os.path.exists(cand):
                os.rename(path, cand)
                return '==== log rotated: >= %d bytes -> %s ====' % (
                    LOG_MAX_BYTES, os.path.basename(cand))
            n += 1
    except OSError:
        return None


def log(msg):
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    with _log_lock:
        print(line, flush=True)
        try:
            rot = _rotate_if_big(_req_log_path)
            with open(_req_log_path, 'a', encoding='utf-8') as f:
                if rot:
                    f.write('[%s] %s\n'
                            % (time.strftime('%Y-%m-%d %H:%M:%S'), rot))
                f.write(line + '\n')
        except OSError:
            pass


# ---------------------------------------------------------------------------
# sessions / accounts
# ---------------------------------------------------------------------------

_sessions = {}          # sessionID -> session dict
_sessions_lock = threading.Lock()
_msg_queues = {}        # accountID -> list of pending tunnel messages
_msg_cond = threading.Condition()

# Response-shape sweep counters. The oracle override (server/oracle/<name>.json
# or .0.json/.N.json variants) stays available for future grammar probing; the
# platformLogin sweep was retired 2026-09-21 once its shape was proven.
_CL_SWEEP = [0]         # cloud response variant index
_ORACLE_SEQ = [0]       # served-oracle-body counter (body<->trace correlation)
_SWEEP_LOCK = threading.Lock()  # guards _ORACLE_SEQ (oracle variant sweeps)


def new_session_id():
    return hashlib.sha256(('%f' % time.time()).encode() + os.urandom(16)).hexdigest()[:32]


def gift_gifter_name(value):
    """Pull the sender's display name out of a gift blob, or None.

    2026-10-05: a gift's document value is base64 of`BOD` + frame header +
    zlib(serialized object). The serialized graph is length-prefixed, NOT a
    plain string table - around the sender field it reads:

        0a 00 "GifterName"  0f 01 16 d4 c3 ce 13 7a da 90  09 00 "playerA"
        ^len   ^field name  ^type/flags                      ^u16 len ^VALUE

    so the value is a u16 length prefix followed by that many bytes; the bytes
    before the prefix are the field's type tag and must be skipped.

    This matters because a MAP_SET names only the RECIPIENT (`profileNames` is
    the account being addressed - see account_from_request), so the server had
    no other source for a sender and stamped the recipient's own name into
    `lastModifiedName`, making every gift look self-sent in the inbox.

    Returns None when the value is not a gift blob (e.g. GameToWeb's XML), when
    it carries no sender, or when the name is not printable - callers fall back
    to the previous behaviour in all three cases.
    """
    if not value or not value.startswith('Qk9E'):
        return None                      # not a BOD blob
    try:
        raw = base64.b64decode(value + '=' * (-len(value) % 4))
    except (ValueError, TypeError):
        return None
    if raw[:3] != b'BOD':
        return None
    # Locate the zlib stream rather than trusting a fixed header length: the
    # frame's trailing size dword varies in width between gifts, but the zlib
    # magic is always at the same place in the scheme.
    start = -1
    for magic in (b'x\x9c', b'x\xda', b'x\x01'):
        at = raw.find(magic, 3)
        if at != -1 and (start == -1 or at < start):
            start = at
    if start == -1:
        return None
    try:
        payload = zlib.decompress(raw[start:])
    except zlib.error:
        return None
    at = payload.find(b'GifterName')
    if at == -1:
        return None
    # After the field name: skip the type tag, then find the u16 length prefix
    # whose run of bytes is all printable ASCII (the name).
    limit = min(at + 64, len(payload))
    for pos in range(at + len('GifterName'), limit - 1):
        size = struct.unpack_from('<H', payload, pos)[0]
        if not 1 <= size <= 64 or pos + 2 + size > len(payload):
            continue
        name = payload[pos + 2:pos + 2 + size]
        if all(32 <= b < 127 for b in name):
            return name.decode('ascii')
    return None


def parse_steam_ticket(hex_ticket):
    """Best-effort extraction of the SteamID from a hex-encoded auth ticket."""
    try:
        raw = bytes.fromhex(hex_ticket)
    except (ValueError, TypeError):
        return None
    # Pre-2015 tickets embed the 64-bit SteamID at offset 12 (LE).
    if len(raw) >= 20:
        try:
            sid = struct.unpack_from('<Q', raw, 12)[0]
            if 0 < sid < (1 << 64):
                return sid
        except struct.error:
            pass
    return None


def steam64_from_profile_names(value):
    """`/steam/76561198000000001[,/steam/...]` -> 76561198000000001 (or None).

    2026-09-24 (item exchange): every cloud request carries `profileNames` -
    the Steam profile the cloud account is addressed by, e.g.
    `profileNames=%2fsteam%2f76561198000000001`. That steam64 is the ONLY stable
    identity in the request (the `auth` ticket blob is re-encrypted per request,
    see the 04:49:29 vs 04:49:58 captures), so it must drive the doc-store key:
    otherwise the call that WRITES a document and the call that READS it are
    mapped to different accounts.
    """
    for part in str(value or '').split(','):
        m = re.search(r'(\d{15,20})', part)
        if m:
            return int(m.group(1))
    return None


def account_from_request(params):
    """Derive a stable account identity from request parameters.

    Resolution order (2026-09-24, item-exchange fix):
      1. `profileNames` (`/steam/<steam64>`) - the cloud account the game
         addresses; stable for GET and POST and across requests,
      2. an explicit numeric id (`steamid`/`platformAccountName`/`username`/
         `accountName`),
      3. the auth ticket payload (`ticket`/`authToken`/`steamauth`/**`auth`**),
      4. the params-hash fallback (last resort only).

    Before this the game matched NONE of the old keys - it sends `profileNames`
    AND `auth=` - so every request fell through to (4) and hashed (ticket blob
    included) to a DIFFERENT accountID. The game then wrote GameToWeb under one
    account and read TomeGift under another (23 stray dirs in
    server/data/cloud), which is why item exchange always came back empty even
    though the request format itself was accepted.
    """
    steamid = steam64_from_profile_names(params.get('profileNames'))
    if steamid is None:
        for k in ('steamid', 'platformAccountName', 'username', 'accountName'):
            v = params.get(k)
            if v:
                try:
                    steamid = int(str(v))
                    break
                except ValueError:
                    continue
    ticket = (params.get('ticket') or params.get('authToken')
              or params.get('steamauth') or params.get('auth'))
    if steamid is None and ticket:
        steamid = parse_steam_ticket(ticket)
    if steamid is None:
        seed = json.dumps(params, sort_keys=True, default=str).encode()
        steamid = int.from_bytes(hashlib.md5(seed).digest()[:6], 'little') | (1 << 31)
    account_id = steamid & 0xFFFFFFFF
    return {
        'steamid': steamid,
        'accountID': account_id,
        'profileID': account_id,
        'accountName': str(params.get('platformAccountName') or
                           params.get('username') or
                           ('Player%d' % (account_id % 100000))),
    }


def get_session(sid):
    with _sessions_lock:
        return _sessions.get(sid)


def put_session(sid, sess):
    with _sessions_lock:
        _sessions[sid] = sess


# ---------------------------------------------------------------------------
# cloud document store  (the heart of player-to-player item exchange)
# ---------------------------------------------------------------------------

_cloud_lock = threading.RLock()


def _cloud_path(account_id, doc_key):
    safe_doc = re.sub(r'[^A-Za-z0-9_\-.]', '_', str(doc_key))[:120] or '_'
    d = os.path.join(CLOUD_DIR, str(account_id))
    return d, os.path.join(d, safe_doc + '.json')


def _load_doc(account_id, doc_key):
    _, path = _cloud_path(account_id, doc_key)
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (OSError, ValueError):
            pass
    return {'values': {}, 'friendPerms': 0, 'worldPerms': 0,
            'lastModifiedName': '', 'lastModifiedDate': 0,
            'updaterNames': {}, 'profileNames': {}}


def _save_doc(account_id, doc_key, doc):
    d, path = _cloud_path(account_id, doc_key)
    os.makedirs(d, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(doc, f, indent=1)
    os.replace(tmp, path)


def param_int(params, name, default=0):
    """First numeric value of a request parameter, or `default`.

    2026-09-24: `getNews` is the one endpoint the game calls with a QUERY
    STRING (`GET .../news/getNews?limit=32&version=1&platform=PC_STEAM&...`),
    and list-valued parameters reach the handlers as lists on some paths, so
    `int(params.get(name) or default)` can raise. This keeps the news handler
    from 500ing on a repeated/blank parameter.
    """
    v = params.get(name)
    if isinstance(v, list):
        v = v[0] if v else None
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return default


def requested_doc_keys(params):
    """The docKeys a cloud request addresses, in order (`TomeGift,GameToWeb`)."""
    v = params.get('docKeys')
    if v is None:
        v = params.get('docKey')
    if v is None:
        return []
    if isinstance(v, list):
        parts = [str(x) for x in v]
    else:
        parts = str(v).split(',')
    return [p.strip() for p in parts if p.strip()]


def param_slot(params, name, index):
    """Index-aligned slot of a comma-separated request parameter.

    2026-09-24: cloud parameters travel as parallel lists, one slot per docKey
    (`docKeys=TomeGift,GameToWeb&worldPerms=2,2&friendPerms=0,0`). A single-slot
    value applies to every docKey, and a missing parameter yields None.
    """
    v = params.get(name)
    if v is None:
        return None
    if isinstance(v, list):
        parts = [str(x) for x in v]
    else:
        parts = str(v).split(',')
    if not parts:
        return None
    return parts[index] if index < len(parts) else parts[-1]


def doc_content(doc, doc_key):
    """The stored document text for `doc_key`, or None when there is none.

    2026-09-24: the game stores a cloud document as the value of its docKey -
    `ops=MAP_SET&docKeys=GameToWeb&keys=GameToWeb&values=<Stats>...</Stats>`
    (captures 09-12 and 09-24, data/cloud/<acct>/GameToWeb.json) - so the text
    the client's doc reader (b32450) wants back is `values[docKey]`. A doc that
    only registered a permission (`keys=&values=`, i.e. `{"": ""}`) holds no
    text and must stay invisible: an empty `document` would be handed straight
    to the game's reader.
    """
    values = doc.get('values')
    if not isinstance(values, dict):
        return None
    cand = values.get(str(doc_key))
    if not (isinstance(cand, str) and cand.strip()):
        # single-value docs indexed under another name: take the only
        # non-empty keyed value, never the empty registration slot.
        cand = None
        for k, v in values.items():
            if str(k).strip() and isinstance(v, str) and v.strip():
                cand = v
    if cand is None or not str(cand).strip():
        if EMPTY_DOC_RECORD and str(doc_key) in values:
            # 2026-09-27: the docKey is MATERIALIZED (it was registered/written
            # with no content), so the document exists and is empty - the state
            # the real service answers with a record. See EMPTY_DOC_RECORD.
            return ''
        return None
    return cand


def doc_map_json(doc, doc_key, shape=None):
    """The gift MAP for the plain GET route in the DS2_GET_MAP shape, or None.

    2026-10-08 (DS2_GET_MAP): the receive path. Every entry of the returned
    document is walked at 0xb44f96 - key {ptr,size}, value char* at +0x10,
    stride 0x30 - and each VALUE is base64-decoded (0xb4394c, the decoder's
    only call site) before being fed to the deserializer that ends in the
    script event `onGiftReceived` and `GiftComponent.addGift`. That canonical
    entry is what a parsed map of `<giftId> -> base64 blob` produces:

        pairs (default): [["<giftId>","Qk9E...", ...], ...]
        obj:             {"<giftId>":"Qk9E...", ...}

    A bare single value (doc_content's fallback) produces no entries, the
    walk does not run, and the received gift never reaches the save - the
    inbox then shows the save's own SENT entry under the player's own name.

    Shape history: the obj shape was served in-game 2026-10-09 (three
    launches, envelope accepted, zero client errors) but produced NO save
    entry, so pairs - the default docshape alias the pre-split rotation
    served on login reads - is now the default. The plain-GET document text
    never reaches the hooked JSON parser (only MAP_GET inner documents do),
    so the client log cannot judge this route: the save forensics decides.

    The docKey sentinel ("TomeGift", always empty - sends use the gift id as
    the key) and empty values drop out; a single-value doc (GameToWeb, whose
    only key IS the docKey) has no non-docKey entry and returns None, so it
    keeps serving its raw text unchanged. Multi-gift documents come back with
    ALL their gifts in one document (the open question in docshape.txt about
    several stored gifts).
    """
    values = doc.get('values')
    if not isinstance(values, dict):
        return None
    items = [(str(k), v) for k, v in values.items()
             if str(k).strip() and str(k) != str(doc_key)
             and isinstance(v, str) and v.strip()]
    if not items:
        return None
    # `shape` lets either route choose its own map shape: the MAP_GET route
    # passes MAPGET_MAP (default obj), the plain-GET route passes GET_MAP.
    if shape is None:
        shape = GET_MAP
    if shape == 'obj':
        return json.dumps(dict(items), separators=(',', ':'))
    return json.dumps(items, separators=(',', ':'))


def apply_doc_perms(doc, params, index):
    """Persist the policy/permission slots the game sends beside a docKey.

    `policy` is the wire name of the document field `docPolicyBits` (the
    CallCloud reader asks for that exact name, 0xb32450), while
    `worldPerms`/`friendPerms` are the read/write bits other players see.
    Negative values mean "unchanged", so `worldPerms=-1&friendPerms=-1` never
    clobbers what a write stored - relevant because the exchange doc is
    registered `worldPerms=2` (world-readable) so gifts can be seen.

    2026-09-24: the caller only calls this for WRITING ops. A read still sends
    the slots (`policy=0&worldPerms=-1&friendPerms=-1`), and `policy=0` there is
    a placeholder, not "clear the policy" - applying it would wipe the
    registered `policy=1` on every read.
    """
    changed = False
    for param, field in (('policy', 'docPolicyBits'),
                         ('worldPerms', 'worldPerms'),
                         ('friendPerms', 'friendPerms')):
        raw = param_slot(params, param, index)
        if raw is None or str(raw).strip() == '':
            continue
        try:
            val = int(str(raw))
        except ValueError:
            continue
        if val < 0:
            continue
        if doc.get(field) != val:
            doc[field] = val
            changed = True
    return changed


def cloud_execute(op, params, actor):
    """Perform one cloud operation; returns the output dict."""
    doc_keys = requested_doc_keys(params) or ['default']
    # ops may be comma-separated (one per docKey), or a single op.
    if isinstance(op, str):
        ops = [o.strip().upper() for o in op.split(',')]
    else:
        ops = [str(op).upper()]
    keys = params.get('keys') or []
    values = params.get('values') or []
    if isinstance(keys, str):
        keys = [k for k in keys.split(',')]
    if isinstance(values, str):
        values = [v for v in values.split(',')]
    out_docs = {}
    with _cloud_lock:
        for di, dk in enumerate(doc_keys):
            opi = ops[di % len(ops)] if ops else 'GET'
            doc = _load_doc(actor['accountID'], dk)
            changed = False

            # 2026-09-24: policy/permission slots are index-aligned with
            # docKeys. A READ carries them as placeholders
            # (`policy=0&worldPerms=-1&friendPerms=-1` on the spawn's
            # `MAP_GET TomeGift`), so a read must never touch the store - the
            # registration's `policy=1,1&worldPerms=2,2` has to survive, or the
            # served `docPolicyBits` (which the client reads, 0xb32450) would be
            # cleared again on every request. On a WRITE, negative values mean
            # "unchanged" (see apply_doc_perms()).
            read_only = opi in ('MAP_GET', 'GET', '')
            if not read_only and apply_doc_perms(doc, params, di):
                changed = True
            if not read_only and EMPTY_DOC_RECORD and str(dk) not in doc['values']:
                # 2026-09-27: the client's own login registration
                # (`ops=SET,SET&keys=&values=`) creates the document on the real
                # service even though it stores no text; mirror that so the next
                # read answers with an (empty) record instead of `[0,[]]`.
                # See EMPTY_DOC_RECORD.
                doc['values'][str(dk)] = ''
                changed = True

            if read_only:
                pass  # read-only
            elif opi in ('MAP_SET', 'SET'):
                for k, v in zip(keys, values):
                    k = str(k)
                    if k == '' and not str(v):
                        # 2026-09-24: the TomeGift permission registration the
                        # game sends at login is `ops=SET,SET&keys=&values=`
                        # (all slots empty). Storing it produced junk
                        # `{"": ""}` documents that carried no content and only
                        # widened the store's key list.
                        continue
                    doc['values'][k] = v
                    doc['updaterNames'][k] = actor['accountName']
                    changed = True
                # 2026-09-24: accept the client's own name for the document
                # text (`document` - the field its reader b32450 asks for), so
                # gift XML can be stored here and then served straight back
                # instead of being dropped.
                text = param_slot(params, 'document', di)
                if text:
                    doc['values'][str(dk)] = text
                    doc['updaterNames'][str(dk)] = actor['accountName']
                    changed = True
            elif opi == 'MAP_INC':
                deltas = params.get('deltas') or values
                for k, dv in zip(keys, deltas):
                    k = str(k)
                    try:
                        cur = int(doc['values'].get(k, 0))
                        doc['values'][k] = cur + int(dv)
                    except (TypeError, ValueError):
                        doc['values'][k] = dv
                    doc['updaterNames'][k] = actor['accountName']
                    changed = True
            elif opi == 'MAP_REMOVE':
                # 2026-10-05: the game's collect names the placeholder `key`
                # (twice, once per docKey) instead of the gift ids it just read,
                # so the plain pop below matches nothing and the inbox never
                # empties. Under DS2_COLLECT, a remove whose keys are all the
                # placeholder takes the gifts the client was looking at; a remove
                # naming REAL ids still removes exactly those.
                real = [k for k in keys if str(k) in doc.get('values', {})]
                placeholder = [k for k in keys
                               if str(k) in COLLECT_PLACEHOLDER_KEYS]
                if COLLECT_REMOVES and placeholder and not real:
                    dropped = [k for k in doc.get('values', {})
                               if k not in ('TomeGift', '')]
                    for k in dropped:
                        doc['values'].pop(k, None)
                        doc.get('updaterNames', {}).pop(k, None)
                    changed = True
                    log('  collect: placeholder remove -> dropped %d gift(s) %s'
                        % (len(dropped), ', '.join(dropped) or '(none)'))
                else:
                    for k in keys:
                        doc['values'].pop(str(k), None)
                        changed = True
            elif opi == 'SET_PERM':
                if 'friendPerms' in params:
                    doc['friendPerms'] = params['friendPerms']
                if 'worldPerms' in params:
                    doc['worldPerms'] = params['worldPerms']
                changed = True
            else:
                raise ValueError('unknown op %r' % opi)

            if changed:
                # 2026-10-05 (attribution fix): `actor` is the account the
                # request ADDRESSES (profileNames), i.e. the RECIPIENT of a
                # gift - so stamping actor['accountName'] here made every gift
                # read back as self-sent. A gift blob carries its own sender in
                # `GifterName`, which is the authoritative source; fall back to
                # the old stamp only for non-gift documents (GameToWeb's XML).
                #
                # ONLY A WRITE may re-stamp. A REMOVE or a permission change is
                # not a modification BY the sender - it is the recipient clearing
                # their inbox - so it must leave `lastModifiedName` alone.
                # Re-stamping on a collect overwrote the sender with whatever the
                # first surviving blob said (observed 18:28:50: three MAP_REMOVEs
                # each re-stamped, so a deliberately set sender did not survive the
                # inbox being opened).
                if opi in ('MAP_REMOVE', 'SET_PERM'):
                    doc['lastModifiedDate'] = int(time.time())
                else:
                    sender = None
                    for _k, _v in (doc.get('values') or {}).items():
                        sender = gift_gifter_name(_v)
                        if sender:
                            break
                    doc['lastModifiedName'] = sender or actor['accountName']
                    doc['lastModifiedDate'] = int(time.time())
                    if sender and sender != actor['accountName']:
                        log('  gift attribution: recipient=%s sender=%r '
                            '(from blob)' % (actor['accountName'], sender))
                _save_doc(actor['accountID'], dk, doc)

            result = {
                'docKey': dk,
                'keys': keys,
                'values': [doc['values'].get(str(k)) for k in keys],
                'allValues': doc['values'],
                'docPolicyBits': 0,
                'friendPermissionBits': doc.get('friendPerms', 0),
                'worldPermissionBits': doc.get('worldPerms', 0),
                'lastModifiedProfileName': doc.get('lastModifiedName', ''),
                'lastModifiedDate': doc.get('lastModifiedDate', 0),
                'updaterNames': [doc.get('updaterNames', {}).get(str(k), '') for k in keys],
                'profileNames': [],
            }
            out_docs[dk] = result

    if len(out_docs) == 1:
        return next(iter(out_docs.values()))
    return {'docs': list(out_docs.values())}


def cloud_get_keys(params, actor):
    d = os.path.join(CLOUD_DIR, str(actor['accountID']))
    keys = []
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith('.json'):
                keys.append(fn[:-5])
    return {'docKeys': keys, 'keys': keys}


# ---------------------------------------------------------------------------
# tunnel message queue
# ---------------------------------------------------------------------------

def queue_message(account_id, message):
    with _msg_cond:
        _msg_queues.setdefault(account_id, []).append(message)
        _msg_cond.notify_all()


def wait_messages(account_id, timeout_s):
    deadline = time.time() + max(0.0, timeout_s)
    with _msg_cond:
        while True:
            q = _msg_queues.get(account_id)
            if q:
                msgs = q[:]
                _msg_queues[account_id] = []
                return msgs
            remaining = deadline - time.time()
            if remaining <= 0:
                return []
            _msg_cond.wait(remaining)


# ---------------------------------------------------------------------------
# HTTP handler
# ---------------------------------------------------------------------------

class _RawCapture:
    """Wraps a file-like object, logging every chunk of data read."""
    def __init__(self, orig, label):
        self._orig = orig
        self._label = label
    def read(self, *a, **kw):
        data = self._orig.read(*a, **kw)
        if data:
            log('raw[%s] recv %d bytes: %s' % (self._label, len(data), data.hex()))
        return data
    def readline(self, *a, **kw):
        data = self._orig.readline(*a, **kw)
        if data:
            log('raw[%s] line %d bytes: %s' % (self._label, len(data), data))
        return data
    def __getattr__(self, name):
        return getattr(self._orig, name)


class _ProbeReader:
    """Serve a pre-buffered chunk first, then the real stream. This lets
    us read-ahead from the TLS socket without losing the data for
    BaseHTTPRequestHandler."""
    def __init__(self, orig, buffered):
        self._orig = orig
        self._buf = buffered
        self._pos = 0
    def read(self, *a, **kw):
        if self._pos < len(self._buf):
            n = kw.get('n') or a[0] if (a and isinstance(a[0], int)) else 0
            if n and n > 0:
                if n >= len(self._buf) - self._pos:
                    chunk = self._buf[self._pos:]
                    self._pos = len(self._buf)
                    tail = self._orig.read(n - len(chunk))
                    return chunk + tail if tail else chunk
                else:
                    chunk = self._buf[self._pos:self._pos + n]
                    self._pos += n
                    return chunk
            else:
                chunk = self._buf[self._pos:]
                self._pos = len(self._buf)
                return chunk
        return self._orig.read(*a, **kw)
    def readline(self, *a, **kw):
        if self._pos < len(self._buf):
            nl = self._buf.find(b'\n', self._pos)
            if nl != -1:
                end = nl + 1
                chunk = self._buf[self._pos:end]
                self._pos = end
                return chunk
        if self._pos < len(self._buf):
            chunk = self._buf[self._pos:]
            self._pos = len(self._buf)
            return chunk
        return self._orig.readline(*a, **kw)
    def __getattr__(self, name):
        return getattr(self._orig, name)


def _http_request_complete(buf):
    """True once `buf` holds a COMPLETE HTTP request (2026-09-21).

    Used to stop the post-handshake probe the moment the client's request is
    fully buffered, so the reply goes out immediately instead of waiting for
    peer-close / CAPTURE_WAIT_S. Handles GET (no body) and POST with
    Content-Length; anything still incomplete keeps the caller waiting.
    """
    if not buf:
        return False
    end = buf.find(b'\r\n\r\n')
    if end == -1:
        return False
    head = buf[:end]
    clen = 0
    for line in head.split(b'\r\n')[1:]:
        if line.lower().startswith(b'content-length:'):
            try:
                clen = int(line.split(b':', 1)[1].strip())
            except ValueError:
                clen = 0
    return len(buf) >= end + 4 + clen


CAPTURE_WAIT_S = 45   # how long to wait for the game to speak post-handshake

# Greeting-rotation experiment (2026-08-28): the game connects, reads ONCE
# (~1ms timeout) with the server silent, closes, retries ~every 14s. So the
# SERVER speaks first on 443/8443. Each connection consumes the next candidate
# so one game session cycles the whole list. Baseline ('none') established in
# the 20:22 run: game closes TLS cleanly ~1s / plain ~1ms -> dropped from the
# rotation; every connection now carries a real candidate.
# Evidence for candidates: exe strings `[%[^]]]` + `_defaults` + `_shared`
# (INI-style section parser), `thqc-gds|https|/rpc|/gds/` descriptor, and a
# 60x u32 jump table (0..11) -> maybe a typed binary message protocol.
GREETINGS = [
    ('bracket_defaults', b'[_defaults]\n'),
    ('bracket_shared', b'[_shared]\n'),
    ('ini_both', b'[_defaults]\r\n[_shared]\r\n'),
    ('bracket_gds', b'[thqc-gds]\n'),
    ('bracket_ok', b'[OK]\n'),
    ('line_nl', b'\n'),
    ('thqc_desc', b'thqc-gds|https|/rpc|/gds/\n'),
    ('ver_u32_1', b'\x01\x00\x00\x00'),
    ('msg_u32_len4_ver1', b'\x04\x00\x00\x00\x01\x00\x00\x00'),
    ('gds_banner', b'GDS/1.0\n'),
    ('http_200', b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n'
                 b'Connection: close\r\n\r\n'),
]
_greet_i = [0]


def next_greeting():
    tag, payload = GREETINGS[_greet_i[0] % len(GREETINGS)]
    _greet_i[0] += 1
    return tag, payload


# TLS channel (443) gets its OWN rotation: the game attempts 443 once per
# launch, so each launch tests exactly one candidate. 2026-08-28 lesson: the
# game READ our '[thqc-gds]' greeting and still closed ~1s later -> a bare
# section header is an INCOMPLETE document. The parser (RE-decoded) consumes
# [section]/key=value until a '~' line TERMINATES the stream. So the top
# candidates are now COMPLETE documents ending with '~'. Two delivery styles:
# close_after=True (graceful EOF right after send - if the client's transport
# accumulates the buffer until EOF, the doc applies immediately) vs keep-open.
# Sentinel hosts t1..t10 = oracle: if the game applies ANY endpoint key it
# will getaddrinfo() that host (visible in the binkw32 proxy log).
# (Local-file oracle with s1..s10 sentinels is already deployed as
#  .\config\Darksiders2{485530A1-6E65-4316-AD74-52925309045A}.cfg.)

_DOC_LINES = [
    '[_defaults]',
    'PC.UsingSteam=true',
    'Language=en',
    'Game=en',
    'DeveloperMode=Debug',
    'gds=t7-ds2cfg.thqconnect.com',
    '',
    '[_shared]',
    'thqc-gds=t5-ds2cfg.thqconnect.com',
    'gds=t6-ds2cfg.thqconnect.com',
    'TelemetryEnabled=true',
    'Telemetry=t9-ds2cfg.thqconnect.com',
    'TelemetryTag=Release',
    '',
    '[thqc-gds]',
    'host=t1-ds2cfg.thqconnect.com',
    'endpoint=t2-ds2cfg.thqconnect.com',
    'server=t3-ds2cfg.thqconnect.com',
    'url=t4-ds2cfg.thqconnect.com',
    'scheme=https',
    'path=/rpc',
    'prefix=/gds/',
    '',
]
_DOC = ('\n'.join(_DOC_LINES) + '\n').encode()
_DOC_TILDE = _DOC + b'~\n'

# Real-host doc: identical grammar, but endpoints point at OUR server
# (gds.thqconnect.com -> hosts -> 127.0.0.1). If the game consumes THIS
# doc, its GDS libcurl client will dial 127.0.0.1:8443 with a TLS
# ClientHello and POST app/x-gds-rpc -- observable directly in the
# server log. Stronger oracle than the t1..t9 sentinels.
_DOC_REAL_LINES = [
    '[_defaults]',
    'PC.UsingSteam=true',
    'Language=en',
    'Game=en',
    'DeveloperMode=Debug',
    'gds=gds.thqconnect.com',
    '',
    '[_shared]',
    'thqc-gds=gds.thqconnect.com',
    'gds=gds.thqconnect.com',
    'TelemetryEnabled=true',
    'Telemetry=gds.thqconnect.com',
    'TelemetryTag=Release',
    '',
    '[thqc-gds]',
    'host=gds.thqconnect.com',
    'Port=8443',
    'port=8443',
    'scheme=https',
    'path=/rpc',
    'prefix=/gds/',
    '',
]
_DOC_REAL = ('\n'.join(_DOC_REAL_LINES) + '\n').encode()
_DOC_REAL_TILDE = _DOC_REAL + b'~\n'

TLS_GREETINGS = [
    ('doc_realhost_tilde_close', _DOC_REAL_TILDE, True),
    ('doc_realhost_tilde_keep', _DOC_REAL_TILDE, False),
    ('doc_full_tilde_close', _DOC_TILDE, True),
    ('doc_full_tilde_keep', _DOC_TILDE, False),
    ('doc_full_close', _DOC, True),
    ('doc_min_tilde', b'[_defaults]\nPC.UsingSteam=true\n~\n', True),
    ('bracket_gds', b'[thqc-gds]\n', False),
    ('thqc_desc', b'thqc-gds|https|/rpc|/gds/\n', False),
    ('http_200', b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n'
                 b'Connection: close\r\n\r\n', False),
    ('line_nl', b'\n', False),
    ('ver_u32_1', b'\x01\x00\x00\x00', False),
    ('msg_u32_len4_ver1', b'\x04\x00\x00\x00\x01\x00\x00\x00', False),
    ('gds_banner', b'GDS/1.0\n', False),
    ('bracket_ok', b'[OK]\n', False),
]
_greet_tls_i = [0]

# 2026-08-29: candidate 0 (doc_realhost_tilde_close) is PROVEN - the game
# consumed it (clean 443 close + GDS client woke up 3s later). Pin every
# 443 launch to it until 8443 traffic is decoded; set False to resume the
# one-candidate-per-launch rotation.
PIN_TLS_GREETING = True


def next_tls_greeting():
    if PIN_TLS_GREETING:
        return TLS_GREETINGS[0]
    tag, payload, close_after = TLS_GREETINGS[_greet_tls_i[0] % len(TLS_GREETINGS)]
    _greet_tls_i[0] += 1
    return tag, payload, close_after


# 8443 (login channel) server-first candidates. With the sync-connect proxy
# fix (2026-08-30) the game HOLDS each 8443 socket ~800ms waiting for the
# server to speak first, then closes + retries in pairs every 15-40s. The
# pre-fix rotation results are obsolete (sockets died 1ms in - greetings
# were never read). One candidate per 8443 connection; never close after
# sending (the game must be free to react). 443 stays pinned separately.
PLAIN_GREETINGS = [
    ('doc_realhost_tilde', _DOC_REAL_TILDE),
    ('doc_min_tilde', b'[_defaults]\nPC.UsingSteam=true\n~\n'),
    ('bracket_ok', b'[OK]\n'),
    ('ok_line', b'OK\n'),
    ('line_nl', b'\n'),
    ('thqc_desc', b'thqc-gds|https|/rpc|/gds/\n'),
    ('gds_banner', b'GDS/1.0\n'),
    ('ver_u32_1', b'\x01\x00\x00\x00'),
    ('msg_u32_len4_ver1', b'\x04\x00\x00\x00\x01\x00\x00\x00'),
    ('http_200', b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\n'
                 b'Connection: close\r\n\r\n'),
]
_greet_plain_i = [0]


def next_plain_greeting():
    tag, payload = PLAIN_GREETINGS[_greet_plain_i[0] % len(PLAIN_GREETINGS)]
    _greet_plain_i[0] += 1
    return tag, payload


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'THQConnect/1.0'

    def _probe_tls(self):
        """Wait (bounded) for the game to send data after the TLS handshake,
        logging every byte. Uses select() to wait - an SSLSocket that timed
        out once becomes unreadable ('cannot read from timed out object'),
        so we must never let read() itself hit the timeout.

        2026-09-21 EARLY-EXIT: the game is a real HTTP client now, so the
        moment the buffered bytes form a COMPLETE request we stop probing and
        hand the data to the handler. Previously the probe always waited for
        peer-close / CAPTURE_WAIT_S (=45 s, curl's own timeout) before any
        reply was produced - every response was late by that much, which is
        the 45 s stall seen in the 09-20 session. Old behaviour is kept for
        anything that is not a complete HTTP request (greeting experiments,
        443 doc reads).
        """
        buffered = b''
        try:
            deadline = time.time() + CAPTURE_WAIT_S
            sock = self.connection
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                r, _, _ = select.select([sock], [], [], min(2.0, remaining))
                if not r:
                    continue            # nothing yet, keep polling
                # readable now: a blocking read returns immediately with the
                # decrypted record (loopback: no fragmentation risk)
                sock.settimeout(None)
                try:
                    chunk = sock.read(65536)
                except (ssl.SSLError, OSError) as e:
                    log('tls probe read err: %r' % e)
                    break
                if not chunk:
                    log('tls probe: peer closed cleanly')
                    break
                buffered += chunk
                log('tls recv %d bytes: %s' % (len(chunk), chunk.hex()))
                if _http_request_complete(buffered):
                    log('tls probe: complete HTTP request buffered (%d bytes)'
                        ' -> serving now' % len(buffered))
                    break
                if len(buffered) > 65536:
                    break
            if not buffered:
                log('tls probe: no app data from peer within %.0fs' % CAPTURE_WAIT_S)
            else:
                log('tls probe: captured total %d bytes' % len(buffered))
        except Exception as e:
            log('tls probe err: %r' % e)
        return buffered

    def handle(self):
        """Log every connection + capture raw data passing through."""
        try:
            peer = self.client_address
            is_tls = isinstance(self.connection, ssl.SSLSocket)
            lport = int(getattr(self.server, 'server_address', (0, 0))[1])
            log('handler: connection from %s:%s -> :%s tls=%s'
                % (peer[0], peer[1], lport, is_tls))
            if is_tls:
                buffered = self._probe_tls()
                self.rfile = _ProbeReader(
                    _RawCapture(self.rfile, 'tls'), buffered)
            else:
                # plain 443/8443 = greeting-experiment channel: raw-capture
                # everything the game sends back (port 80 = real HTTP, skip)
                try:
                    if self.server.server_address[1] in (443, 8443):
                        self.rfile = _RawCapture(self.rfile, 'plain')
                except Exception:
                    pass
        except Exception:
            pass
        try:
            BaseHTTPRequestHandler.handle(self)
        except Exception as e:
            log('handler: exception %r' % e)

    # ---- plumbing ---------------------------------------------------------

    def log_message(self, fmt, *args):  # silence default stderr logging
        pass

    def send_error(self, code, message=None, explain=None):
        """Log malformed requests that never reach _dispatch."""
        log('!! malformed request from %s:%s -> %s %s (%s)' % (
            self.client_address[0], self.client_address[1],
            code, message, explain))
        try:
            BaseHTTPRequestHandler.send_error(self, code, message, explain)
        except Exception:
            pass

    def _read_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0:
            return b''
        return self.rfile.read(min(length, 8 * 1024 * 1024))

    @staticmethod
    def _parse_params(path, query, body):
        params = {}
        for k, v in parse_qs(query).items():
            params[k] = v[0]
        if body:
            text = body.decode('utf-8', 'replace').strip()
            if text.startswith('{'):
                try:
                    obj = json.loads(text)
                    if isinstance(obj, dict):
                        params.update(obj)
                except ValueError:
                    pass
            else:
                for k, v in parse_qs(text).items():
                    params[k] = v[0]
        return params

    def _actor(self, params):
        sid = params.get('sessionID') or ''
        sess = get_session(sid)
        if sess:
            return sess['account']
        return account_from_request(params)

    def _respond(self, code, payload, content_type='application/json'):
        if isinstance(payload, (dict, list)):
            body = json.dumps(payload).encode('utf-8')
        elif isinstance(payload, str):
            body = payload.encode('utf-8')
        else:
            body = payload
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
        return body

    @staticmethod
    def _as_list(v, keep=False):
        if v is None:
            return []
        if isinstance(v, list):
            return v
        if keep and not v:
            return []
        return [v] if keep else []

    @staticmethod
    def _users_objs(u, actor=None):
        # game schema (0xafa559 -> 0xaf8f37): users = [ {name:[..],accountID:<n>,profileID:<n>} ]
        pid = None
        aid = None
        if isinstance(actor, dict):
            pid = actor.get('profileID')
            aid = actor.get('accountID')
        arr = []
        if isinstance(u, list):
            for x in u:
                if isinstance(x, dict):
                    d = dict(x)
                    nm = d.get('name')
                    if isinstance(nm, list):
                        pass
                    elif nm:
                        d['name'] = Handler._as_list(nm, keep=True)
                    else:
                        src = (d.get('username') or d.get('accountName')
                               or d.get('profileName') or '')
                        d['name'] = Handler._as_list(src, keep=True)
                    d.setdefault('accountID', aid or 0)
                    d.setdefault('profileID', pid or 0)
                    arr.append(d)
                else:
                    nm = '' if x is None else str(x)
                    arr.append({'name': Handler._as_list(nm, keep=True),
                                'accountID': aid or 0, 'profileID': pid or 0})
        elif u:
            nm = str(u)
            arr.append({'name': Handler._as_list(nm, keep=True),
                        'accountID': aid or 0, 'profileID': pid or 0})
        return arr

    @staticmethod
    def _ok(output=None, **extra):
        # 2026-09-15 DECODED (definitive): the shared async parser at
        # 0xafa77d runs LIST-first token readers over a bare {} object:
        #   status/age -> numeric token (int)
        #   url/email/username/name -> JSON ARRAY of strings
        #   users -> JSON ARRAY of {name:[..], accountID:<n>, profileID:<n>}
        # Top-level must NOT be wrapped in [] (0xaf7e16 consuming '[' leaves
        # the field readers at '{' and they misread the members).
        out = dict(output or {})
        out.update(extra)
        if 'status' in out and isinstance(out['status'], str):
            s_map = {'ok': 0, 'signedin': 0, 'online': 0, 'success': 0,
                     'failure': 1, 'connected': 2, 'disconnected': 3}
            out['status'] = s_map.get(str(out['status']).lower(), 0)
        uname = out.get('username')
        if not uname:
            pi = out.get('profileInfo', {})
            uname = pi.get('accountName') if isinstance(pi, dict) else None
        uname = uname or ''
        pid = None
        aid = None
        pi = out.get('profileInfo') if isinstance(out.get('profileInfo'), dict) else {}
        pid = pid or pi.get('profileID')
        aid = aid or pi.get('accountID')
        actor = {'profileID': pid, 'accountID': aid}
        env = {
            'status': int(out.get('status', 0)),
            'url': Handler._as_list(out.get('url'), keep=True),
            'email': Handler._as_list(out.get('email'), keep=True),
            'username': Handler._as_list(uname, keep=True),
            'age': int(out.get('age') or 0),
            'users': Handler._users_objs(out.get('users') or [uname], actor),
        }
        out = dict(out)
        out.update(env)
        env['resultCode'] = out.get('resultCode', 0)
        env['result'] = out.get('result', 0)
        for k, v in out.items():
            if k not in env:
                env[k] = v
        env['output'] = out
        return env

    def _dispatch(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
        body = self._read_body()
        params = self._parse_params(path, parsed.query, body)

        peer = '%s:%s' % (self.client_address[0], self.client_address[1])
        body_text = body.decode('utf-8', 'replace')
        log('%s %s %s from %s%s' % (
            method, path,
            ('body=' + body_text[:2000]) if body_text else '',
            peer,
            ' keys=' + ','.join(list(params.keys())[:12]) if params else ''))
        if TRACE_CLOUD:
            # Full length, untruncated - see TRACE_CLOUD. The gift payloads are
            # ~1-3 KB of base64 each, so the normal 2000-char cut drops exactly
            # the part that identifies which gift a request refers to.
            trace('==> %s %s' % (method, self.path))
            for name in sorted(params):
                value = params[name]
                if isinstance(value, list):
                    for i, item in enumerate(value):
                        trace('    %s[%d] = %s' % (name, i, item))
                else:
                    trace('    %s = %s' % (name, value))

        resp_body = b''
        resp_code = 200
        try:
            handler = getattr(self, 'h_' + re.sub(r'[^a-z0-9]', '_', path.lower().strip('/')), None)
            if handler:
                resp_code, resp_body = handler(method, params)
            else:
                log('  -> unhandled path, returning empty positional envelope')
                resp_code, resp_body = 200, self._pos_arr(
                    self._pos_base(self._actor(params), status=0))
        except Exception as exc:  # never kill the connection on bugs
            log('  !! handler error: %r' % exc)
            resp_code, resp_body = 200, self._pos_arr(
                self._pos_base(self._actor(params), status=1))

        log('  <- %d %s' % (resp_code, resp_body[:400].decode('utf-8', 'replace')))
        if TRACE_CLOUD:
            trace('    <- %d (%d bytes)' % (resp_code, len(resp_body)))
            trace('    %s' % resp_body.decode('utf-8', 'replace'))
        self.send_response(resp_code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(resp_body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Pragma', 'no-cache')
        self.end_headers()
        try:
            self.wfile.write(resp_body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _dump(obj):
        # 2026-09-15: the game's shared async parser (0xafa77d) is a
        # LIST-first tokenizer: url/email/username are JSON ARRAYS,
        # users is an array of objects {name:[..], accountID:<n>, profileID:<n>},
        # status/age read numeric tokens. Top-level is a bare OBJECT.
        return json.dumps(obj, separators=(',', ':')).encode('utf-8')

    @staticmethod
    def _pos_arr(payload, tail=()):
        """PROVEN response grammar (2026-09-16, fmt#0 accepted by the game).

        The shared async checker (0xafa77d) walks the response as a POSITIONAL
        sequence inside an outer array, commas REQUIRED:

            [ status, [url...], [email...], [username...], age,
              [ [[name...],accountID,profileID], ... ], *tail ]

        Evidence: `[0,[],[],["X"],0,[[["X"]acct,prof]]]` produced
        `CheckSSOStatusAsync - response good` with NO follow-up
        "was not formatted correctly", while every keyed form died at byte 3.
        Keyed JSON is NOT parsed at all - the tokenizer never reads key names.
        """
        users = []
        for u in payload.get('users') or []:
            if isinstance(u, dict):
                nm = u.get('name')
                aid = u.get('accountID') or 0
                pid = u.get('profileID') or 0
            else:
                nm, aid, pid = u, 0, 0
            nm = nm if isinstance(nm, list) else ['' if nm is None else str(nm)]
            users.append([[str(x) for x in nm], int(aid or 0), int(pid or 0)])
        arr = [
            int(payload.get('status') or 0),
            [str(x) for x in (payload.get('url') or [])],
            [str(x) for x in (payload.get('email') or [])],
            [str(x) for x in (payload.get('username') or [])],
            int(payload.get('age') or 0),
            users,
        ]
        arr.extend(tail)
        return json.dumps(arr, separators=(',', ':')).encode('utf-8')

    @staticmethod
    def _pos_raw(items):
        """Exact positional array body (no keyed wrapper) for sweeps."""
        return json.dumps(items, separators=(',', ':')).encode('utf-8')

    @staticmethod
    def _oracle(name, params=None):
        """2026-09-19 live oracle: serve server/oracle/<name>.json verbatim.

        Returns the raw file bytes (read on EVERY request, so edits / the
        variant cycle take effect on the game's very next retry), or None to
        fall through to the normal handler.  Two modes:

          * <name>.json           -> that exact body on every request
          * <name>.0.json ... .N  -> variant sweep: served in a cycle, one
                                     per request, so a SINGLE game launch
                                     tests every candidate shape.

        Inside the body the tokens {NAME} (requesting platform account name)
        and {SID} (sessionID) are substituted, so candidate shapes stay
        realistic.  Each served body is logged with a sequence number, the
        variant index/count and its byte length, so the game's ARG/tokenizer
        trace in ds2_tls.log can be aligned to the exact body that produced it.
        """
        single = os.path.join(ORACLE_DIR, name + '.json')
        if os.path.isfile(single):
            return Handler._oracle_serve(name, single, None, None, params)
        variants = []
        i = 0
        while True:
            vp = os.path.join(ORACLE_DIR, '%s.%d.json' % (name, i))
            if not os.path.isfile(vp):
                break
            variants.append(vp)
            i += 1
        if not variants:
            return None
        with _SWEEP_LOCK:
            k = _ORACLE_SEQ[0]        # 0-based index of THIS sweep request
        # _oracle_serve() owns the increment, so the cycle is strictly
        # 0,1,2,...,N-1,0,... : the FIRST request always tests variant 0.
        return Handler._oracle_serve(name, variants[k % len(variants)],
                                     k % len(variants), len(variants), params)

    @staticmethod
    def _oracle_serve(name, path, vidx, vcount, params):
        try:
            with open(path, 'rb') as fh:
                raw = fh.read()
        except OSError as exc:
            log('  ORACLE %s: read failed: %s' % (name, exc))
            return None
        raw = raw.strip()
        if not raw:
            return None
        if params:
            uname = (params.get('platformAccountName') or '').encode('utf-8')
            sid = (params.get('sessionID') or '').encode('utf-8')
            raw = raw.replace(b'{NAME}', uname).replace(b'{SID}', sid)
        with _SWEEP_LOCK:
            _ORACLE_SEQ[0] += 1
            seq = _ORACLE_SEQ[0]
        shown = raw.decode('utf-8', 'replace')[:400]
        if vidx is None:
            log('  ORACLE %s #%d bytes=%d body=%s' % (name, seq, len(raw), shown))
        else:
            log('  ORACLE %s v%d/%d #%d bytes=%d body=%s'
                % (name, vidx, vcount, seq, len(raw), shown))
        return raw

    def _pos_users(self, actor, extra_users=None):
        """users triples: [ [[name...],accountID,profileID], ... ]"""
        uname = actor['accountName']
        users = extra_users if extra_users is not None else [{
            'name': [uname],
            'accountID': actor.get('accountID') or 0,
            'profileID': actor.get('profileID') or 0,
        }]
        out = []
        for u in users:
            if isinstance(u, dict):
                nm = u.get('name')
                aid = u.get('accountID') or 0
                pid = u.get('profileID') or 0
            else:
                nm, aid, pid = u, 0, 0
            nm = nm if isinstance(nm, list) else ['' if nm is None else str(nm)]
            out.append([[str(x) for x in nm], int(aid or 0), int(pid or 0)])
        return out

    def _pos_base(self, actor, status=0, extra_users=None):
        """Common 6-positional payload for a logged-in account."""
        uname = actor['accountName']
        users = extra_users if extra_users is not None else [{
            'name': [uname],
            'accountID': actor.get('accountID') or 0,
            'profileID': actor.get('profileID') or 0,
        }]
        return {
            'status': status,
            'url': [],
            'email': [],
            'username': [uname],
            'age': 0,
            'users': users,
        }

    def do_GET(self):
        self._dispatch('GET')

    def do_POST(self):
        self._dispatch('POST')

    def do_PUT(self):
        self._dispatch('PUT')

    def do_DELETE(self):
        self._dispatch('DELETE')

    # ---- endpoint handlers (h_<sanitized path>) ---------------------------

    def h_version(self, method, params):
        return 200, self._dump(self._ok({
            'version': '1.0.0-local',
            'service': 'thqc-darksiders2-pc',
            'status': 'online',
            'time': int(time.time()),
        }))

    def h_thqconnect_login_initsession(self, method, params):
        sid = new_session_id()
        actor = account_from_request(params)
        put_session(sid, {'account': actor, 'created': time.time(), 'authed': False})
        log('  initSession -> sessionID=%s account=%s' % (sid, actor['accountName']))
        raw = self._oracle('initSession')
        if raw is not None:
            return 200, raw
        return 200, self._pos_arr(self._pos_base(actor, status=0), [sid])

    def h_thqconnect_login_platformlogin(self, method, params):
        sid = params.get('sessionID') or new_session_id()
        actor = account_from_request(params)
        sess = get_session(sid) or {'created': time.time()}
        sess['account'] = actor
        sess['authed'] = True
        put_session(sid, sess)
        cfg = self.server.config
        profile_name = cfg.get('default_profile_name', 'Death')
        log('  platformLogin -> account=%s steamid=%s' % (actor['accountName'], actor['steamid']))
        output = {
            'sessionID': sid,
            'auth': hashlib.sha256(('auth:' + sid).encode()).hexdigest()[:40],
            'profileInfo': {
                'accountName': actor['accountName'],
                'playerIndex': 0,
                'profileID': actor['profileID'],
                'accountID': actor['accountID'],
            },
            'localProfileInfo': {
                'accountName': actor['accountName'],
                'profileID': actor['profileID'],
                'accountID': actor['accountID'],
                'profileName': profile_name,
            },
            'avatar': '',
            'clanName': '',
            'username': actor['accountName'],
            'age': 0,
            'users': [actor['accountName']],
            'globalSettings': {},
            'gameSettings': {},
            'tunnels': [{
                'tunnelName': 'local',
                'serverFQDN': 'localhost',
                'certificate': '',
            }],
            'jobID': 0,
        }
        # 2026-09-24 CORRECTED GRAMMAR (live ARG trail, 09-18 + 09-23 runs).
        #
        # The 09-21 "shared checker" lock was WRONG: ssoStatus and
        # platformLogin are bound by DIFFERENT structs, and the live expect()
        # return addresses prove it.  The platformLogin body is walked by the
        # CONFIG-DOCUMENT binder, whose fields land on these wire slots:
        #
        #   slot  field             binder          live expect() ret
        #   +1    result            generic value   af7f47 (scalar 0 accepted)
        #   +3    tunnels           P1 0xafa214     afa25b
        #   +6    certificates      P2 0xafa32b     afa372
        #   +9    gameSettings      P3 0xafa442     afa489
        #   +19   globalSettings    P3 0xafa442     afa489 (then afa49e on '{')
        #
        # Proof: find_calls(0xafa442) = the binder invoked by the config-doc
        # walker directly after the "certificates" reader (0xafa32b), with the
        # member names "gameSettings" ([esi+0x30]) and "globalSettings"
        # ([esi+0x44]); the same walker reads "result" then "tunnels" first.
        # P1/P2/P3 are 0x117-byte clones: each accepts '[' or '{' at its slot
        # and treats an EMPTY list as success (jae -> epilogue 0xafa546).
        #
        # That is exactly why the old 6-field body died at byte 25: the 09-23
        # body parked the scalar `age = 0` in the globalSettings slot, which
        # demands '['/'{'.  The 09-18 p0 candidate parked the users array
        # there, cleared the +19 gate, then died at +2b/+36 because a list
        # ELEMENT must be string-ish (element binder 0xaf8cc4 reads a generic
        # value, then the members "key"/"value" through 0xaf8288 = a string or
        # a list of strings - a bare number is rejected).
        #
        # 2026-09-24 VERIFIED IN-GAME (run 04:41): this shape completes the whole
        # walk in ONE pass - "response good: 15 bytes", all five slots, the final
        # check ']' OK, DeleteJob[7], and no "was not formatted correctly" - the
        # first time in the project's history.
        # The sweep is therefore retired: server/oracle/platformLogin.json serves
        # this body every time (single-file oracle path, no variant cycling) and
        # the eight candidates sit parked in server/oracle/parked/. The fallback
        # below duplicates the proven shape on purpose: every document field
        # empty, i.e. each binder's empty-list fast path.
        raw = self._oracle("platformLogin")
        if raw is not None:
            return 200, raw
        return 200, self._pos_raw([
            0,      # result
            [],     # tunnels
            [],     # certificates
            [],     # gameSettings
            [],     # globalSettings
        ])

    def h_thqconnect_login_readsession(self, method, params):
        sid = params.get('sessionID') or ''
        sess = get_session(sid)
        valid = bool(sess and sess.get('authed'))
        actor = sess['account'] if sess else account_from_request(params)
        log('  readSession sid=%s valid=%s' % (sid, valid))
        raw = self._oracle('readSession')
        if raw is not None:
            return 200, raw
        return 200, self._pos_arr(
            self._pos_base(actor, status=0),
            [sid, 1 if valid else 0,
             [actor['accountName'], int(actor.get('accountID') or 0),
              int(actor.get('profileID') or 0)]])

    def h_thqconnect_login_ssostatus(self, method, params):
        actor = self._actor(params)
        # 2026-09-16 LOCKED: fmt#0 was ACCEPTED by the game's shared async
        # checker -> response good, then AsyncJobDriver::DeleteJob with NO
        # "was not formatted correctly". The grammar is now fixed (see
        # _pos_arr). Do not change this shape without a new sweep.
        log('  ssoStatus -> positional 6-field (PROVEN fmt#0) actor=%s'
            % actor['accountName'])
        raw = self._oracle('ssoStatus')
        if raw is not None:
            return 200, raw
        return 200, self._pos_arr(self._pos_base(actor, status=0))

    def h_thqconnect_news_getnews(self, method, params):
        """NEWS response: `[ result, [ ...items... ] ]` - POSITIONAL.

        2026-09-24 DECODED IN-GAME (this replaces the "named object" reading,
        which the first real launch FALSIFIED). That 24-byte body produced both
        verdicts for the same bytes -

            GetNewsAsync - response good: 24 bytes, [{"result":0,"news":[]}]
            GetNewsAsync - response was not formatted correctly: <same body>

        - and the hook trace shows the checker walking the RAW response text:

            ARG expect(S) want=0x5b '[' got=0x5b '[' @+0/18 ret=af7e47
            ARG expect(S) want=0x5b '[' got=0x7b '{' @+1/18 ret=af7f47  <- dies

        `af7e47` is the '[' probe inside the container opener `0xaf7e16` (takes
        '[' or '{', one flag per branch); `af7f47` is the '[' probe inside the
        SCALAR scanner `0xaf7f06`, reached through

            0xaf8243 -> 0xaf80e2 -> 0xaf803e -> 0xaf7f06
            (the "result" read: dword, range -2^31..2^31-1)

        `0xaf803e` uses ONLY its parser/out arguments - the pushed name literal is
        dead metadata; no key is ever compared on this wire (verified for every
        reader below). So the slot after '[' must hold the SCALAR result, and
        `[{...}]` can never pass in any field order.

        The checker itself (`0xb32012`, single caller 0xb20c71):

            call 0xaf7e16(reader, ctx, desc, flags)  ; open '[' OR '{'
            if (flags) { 0xaf8243("result", out+0)   ; dword, required
                         0xb31f0a("news",   out+4)   ; list,  required }
            call 0xaf7eb2(...)                       ; close + separator

        hence `[ result, news ]`; `0xb31f0a` accepts an empty `[]` for the list.

        The news ITEM element reader `0xb31c90` is still positional/undecoded, so
        items are served empty even if config carries news - an empty list passes
        the gate and cannot mis-format a launch.
        """
        cfg = self.server.config
        news_items = cfg.get('news', [])
        actor = self._actor(params)
        limit = param_int(params, 'limit', 20)
        raw = self._oracle('getNews')
        if raw is not None:
            return 200, raw
        if news_items:
            log('  news: config holds %d items, but the news ITEM reader '
                '(0xb31c90) is positional/undecoded - serving []'
                % len(news_items))
        body = [0, []]
        raw = self._pos_raw(body)
        log('  news getNews limit=%d actor=%s(%s) -> %d bytes %s'
            % (limit, actor['accountName'], actor['accountID'], len(raw),
               raw.decode('utf-8')))
        return 200, raw

    def h_thqconnect_cloud_execute(self, method, params):
        op = str(params.get('ops') or params.get('op') or '').upper()
        actor = self._actor(params)
        if TRACE_CLOUD:
            # The receive-path question this answers: after reading the index,
            # does the client ever ask for a gift BY ID (keys=<giftId>), or does
            # it only ever send the placeholder `key`? Every gift id in the
            # addressed account is listed, so a miss is unambiguous.
            keys_raw = params.get('keys')
            doc = _load_doc(actor['accountID'], 'TomeGift')
            ids = [k for k in (doc.get('values') or {}) if k not in ('TomeGift', '')]
            trace('  cloud op=%r keys=%r docKeys=%r profile=%r'
                  % (op, keys_raw, params.get('docKeys'),
                     params.get('profileNames')))
            trace('    stored gift ids here: %s' % (', '.join(ids) or '(none)'))
            if ids:
                sent = [keys_raw] if isinstance(keys_raw, str) else list(keys_raw or [])
                named = [k for k in sent if k in ids]
                if named:
                    trace('    ** client NAMED a stored gift id: %s' % named)
                elif sent:
                    trace('    client sent keys matching NO stored gift id'
                          ' (placeholder?) %s' % sent)
        if op:
            log('  cloud op=%s actor=%s(%s)' % (op, actor['accountName'], actor['accountID']))
            cloud_execute(op, params, actor)
        raw = self._oracle('cloud_execute')
        if raw is not None:
            return 200, raw
        if not op:
            return 200, self._cloud_body(actor, 'noop')
        # 2026-09-24: answer the READ with the documents the store holds for the
        # addressed docKeys (this is what makes item exchange show content).
        # 2026-10-06: the ops ride along - `_cloud_body` needs them to split the
        # ROUTES: a plain GET slot gets the RAW stored text (document route),
        # only other ops get the rendered index map. See the route rule in the
        # `_cloud_body` docstring.
        return 200, self._cloud_body(actor, 'execute',
                                      requested_doc_keys(params), op)

    def h_thqconnect_cloud_getkeys(self, method, params):
        actor = self._actor(params)
        cloud_get_keys(params, actor)
        # Unchanged: the key LIST remains `[0,[]]` (proven accepted). `_cloud_body`
        # with no docKeys returns that same empty shape.
        return 200, self._cloud_body(actor, 'getkeys')

    def _cloud_body(self, actor, tag, doc_keys=None, ops=None):
        """CallCloud response: `[ resultCode, [ <document>, ... ] ]`.

        2026-09-24 DECODED IN-GAME (item exchange; CORRECTS the "fields are read
        BY NAME" reading, which the first real launch FALSIFIED). The checker is
        `0xb3280a` - found as the only caller of the doc-list reader `0xb325d2`
        (call site 0xb3288a) - and it walks, in order:

            0xaf8243("resultCode", out+4)   ; dword, required
            0xb325d2("data",       out+8)   ; list,  required

        inside one container opened by `0xaf7e16`. `0xb325d2` drives one element
        per document through `0xb32450`, and `0xb32450` reads its five fields IN
        ORDER (`0xaf803e` below uses only its parser/out arguments, so the pushed
        names are diagnostics and no key is ever matched):

            [0] status                  -> 0xb3240c/0xb320cb: byte via 0xaf8223,
                                           enum -2..13 (out of range logs
                                           "Please upgrade client, enum changed")
            [1] document                -> 0xaf8288: string, 0x800-byte buffer,
                                           escape-aware (cmp al,0x5c at af83c6,
                                           \\uXXXX at af83df), so serving JSON
                                           escapes (\\n / \\t / \\") is right
            [2] docPolicyBits           -> 0xaf8243: dword
            [3] lastModifiedTime        -> 0xaf803e: scalar scanned by 0xaf7f06
                                           then _atoi64'd (64-bit out)
            [4] lastModifiedProfileName -> 0xaf8645 -> 0xaf8288: string

        THE BUG this replaces: elements were served as JSON OBJECTS
        (`{"status":0,"document":...}`). The trace of that 1598-byte body ends:

            ARG expect(S) want=0x5b '[' got=0x5b '[' @+0/63e  ; top-level open
            ARG expect(S) want=0x5b '[' got=0x30 '0' @+1/63e  ; resultCode 0
            ARG expect(S) want=0x5b '[' got=0x5b '[' @+3/63e  ; data list open
            ARG expect(S) want=0x5b '[' got=0x7b '{' @+4/63e  ; element: no '['
            ARG expect(S) want=0x7b '{' got=0x7b '{' @+4/63e  ; ... '}' branch
            ARG expect(S) want=0x5b '[' got=0x22 '"' @+5/63e  ; <- dies here
            CallCloudAsync - response was not formatted correctly

        i.e. the field-1 reader (`0xaf7f06`) hits the KEY quote of `"status"`,
        treats it as a quoted NUMBER, and then finds `s` where it needs digits. A
        keyed element can never parse - the record must be a bare positional list.

        Historical evidence (kept): `[0,[]]` is format-ACCEPTED - 22:13:38 and
        22:45:58 "response good: 6 bytes" followed by a clean DeleteJob with no
        "was not formatted correctly", and again for the 2026-09-24 04:49:58
        `MAP_GET TomeGift` spawn. The earlier non-empty attempts
        (`[0,[["X"]]]`, `[0,[[["X"],aid,pid]]]`) died for the same reason as the
        keyed element above: the wrapped user record put either a string or a
        list in slot 0, where `0xb32450` reads the scalar `status`. `[0,[]]` is
        still the answer whenever the store holds no document text, and is
        produced by the same code path that serves real documents.

        That empty answer IS the item-exchange failure: the spawn's `MAP_GET
        TomeGift` was accepted but delivered zero documents, so the exchange UI
        had nothing to list even though the game had written
        `data/cloud/<acct>/GameToWeb.json` and had registered TomeGift. Each
        stored document is now handed back in the reader's own element shape,
        with the text the game wrote under its docKey (e.g. `<Stats>...</Stats>`)
        in slot 1.

        2026-10-06 ROUTE SPLIT (`ops` argument): the document slot means
        different things per op, and the client's own history decides which
        shape each route may carry. All 54 `Unable to process string` verdicts
        in `ds2_tls.log` sit on a `MAP_GET` - a raw stored value (the gift's
        `Qk9E...` blob) as its document text is rejected, because that route
        runs the CloudInternal map parser (0xb08b07: `'[' or '{'`), so
        `MAP_GET` keeps the rendered index map under DOC_MAP. The plain `GET`
        route is the document route: on 09-28 it carried the raw blob/XML
        (`ops=GET,GET` -> `[0,[[0,"Qk9E...",...],[0,"<Stats>...",...]]]`) and
        the client logged `response good` with NO inner rejection - and zero
        rejections in the whole log sit on a plain GET. So a `GET` slot now
        serves `doc_content()` RAW even when DOC_MAP is on, which is what lets
        the gift blob (with its `GifterName`) reach the client's base64
        dispatcher at all; before this split DOC_MAP intercepted every op and
        the client only ever saw the id->int index. `ops` is index-aligned with
        `doc_keys` (`GET,GET`), exactly like `cloud_execute()` parses it; a
        missing `ops` (the offline previews in tools/gift_capture.py) keeps the
        pre-split behaviour - DOC_MAP renders, otherwise raw.
        """
        docs = []
        if isinstance(ops, str):
            op_list = [o.strip().upper() for o in ops.split(',')]
        elif ops:
            op_list = [str(o).upper() for o in ops]
        else:
            op_list = []
        spec = None
        used_slot = {}
        for di, dk in enumerate(doc_keys or []):
            doc = _load_doc(actor['accountID'], dk)
            opi = op_list[di % len(op_list)] if op_list else ''
            # 2026-10-09 DS2_MAPGET_MAP - MUST be evaluated BEFORE the DOC_MAP
            # branch below. With DOC_MAP=1 every non-GET op (MAP_GET included)
            # enters the index render, so a MAPGET_MAP hook living in the else
            # can never fire - which is why round_1017's MAP_GET still served the
            # id->int index even with DS2_MAPGET_MAP=1 (gloves never ingested).
            # MAP_GET is the ONLY route whose document reaches the base64 decoder
            # (CloudInternal parser 0xb08b07 -> entry walk 0xb44f96), so when a
            # doc HAS gift-id keys its BLOB MAP is served here, ahead of the
            # index, so the walk decodes the blob -> ingests -> the row renders.
            # obj is the canonical key->value the walk eats.
            text = None
            served_mapget = False
            if MAPGET_MAP and opi == 'MAP_GET':
                mapped = doc_map_json(doc, dk, MAPGET_MAP)
                if mapped is not None:
                    text = mapped
                    spec = 'mapget:' + MAPGET_MAP
                    served_mapget = True
            if not served_mapget and DOC_MAP and opi != 'GET':
                # 2026-09-28: serve the document's MAP as JSON text - the
                # CloudInternal parser (0xb08b07) needs '[' or '{', drops empty
                # values (its value reader rejects `""`), and its element
                # interpreter reads named members from each entry. The spec comes
                # from server/docshape.txt round-robin, one per request, so a
                # batch of candidates can be A/B-ed by reopening the exchange -
                # see DOC_SHAPE_FILE / doc_shape(). The rotation advances only
                # when a shape is actually rendered (a plain GET serves raw
                # text and must not consume a docshape line).
                if spec is None:
                    spec = doc_shape()
                text = render_doc(doc.get('values') or {}, spec)
            elif not served_mapget:
                # Plain GET (or DOC_MAP off): the RAW stored document text -
                # the route that carries the gift blob to the client.
                # 2026-10-08 DS2_GET_MAP: a map doc (gift-id keys) goes out as
                # the full JSON object `{"<id>":"Qk9E..."}` instead of the bare
                # single value - that is the shape the entry walk at 0xb44f96
                # base64-decodes (the receive path, see GET_MAP). Only the GET
                # op is affected: the offline previews (ops missing) and a
                # DOC_MAP-off run keep the pre-split behaviour.
                text = doc_content(doc, dk)
                if GET_MAP and opi == 'GET':
                    mapped = doc_map_json(doc, dk)
                    if mapped is not None:
                        text = mapped
                        spec = 'getmap:' + GET_MAP
            if text is None:
                continue
            # POSITIONAL record - see the docstring: `0xb32450` reads these five
            # slots in this order and never looks at a key.  Slots 0/2/3/4 are
            # live-editable (server/docslot.txt, one line per request) so a single
            # game launch can sweep them - see DOC_SLOT_FILE / doc_slot().
            rec = [
                int(doc.get('docStatus') or 0),        # 0 status (byte enum -2..13)
                text,                                   # 1 document (string)
                int(doc.get('docPolicyBits') or 0),     # 2 docPolicyBits (dword)
                int(doc.get('lastModifiedDate') or 0),  # 3 lastModifiedTime (int64)
                doc.get('lastModifiedName') or '',      # 4 lastModifiedProfileName
            ]
            override = doc_slot()
            if override:
                for idx, value in override.items():
                    if 0 <= idx < len(rec):
                        rec[idx] = value
            used_slot = override
            docs.append(rec)
        body = [0, docs]
        raw = self._pos_raw(body)
        log('  cloud %s CLbody=%d bytes docs=%d shape=%s slots=%s %s'
            % (tag, len(raw), len(docs), spec or '-', used_slot,
               json.dumps([0, [d[1][:60] for d in docs]],
                          separators=(',', ':'))[:160]))
        return raw

    def h_thqconnect_url_geturl(self, method, params):
        url = 'http://localhost:%d/web/index.html' % self.server.config.get('http_port', 80)
        base = self._pos_base(self._actor(params), status=0)
        base['url'] = [url]
        return 200, self._pos_arr(base)

    def h_thqconnect_tunnel_tunnel(self, method, params):
        actor = self._actor(params)
        timeout = float(self.server.config.get('tunnel_poll_timeout_s', 25))
        wait_messages(actor['accountID'], timeout)
        return 200, self._pos_arr(self._pos_base(actor, status=0))

    def h_gds_rpc(self, method, params):
        log('  GDS rpc params=%s' % json.dumps(params, default=str)[:1000])
        return 200, self._dump({'resultCode': 0, 'result': 0, 'output': {}})

    # admin API -------------------------------------------------------------

    def h_admin_status(self, method, params):
        with _sessions_lock:
            n_sess = len(_sessions)
        return 200, self._dump({
            'sessions': n_sess,
            'queuedMessages': {k: len(v) for k, v in _msg_queues.items()},
            'cloudDocs': sum(
                len([f for f in os.listdir(os.path.join(CLOUD_DIR, a)) if f.endswith('.json')])
                for a in (os.listdir(CLOUD_DIR) if os.path.isdir(CLOUD_DIR) else [])
            ),
        })

    def h_admin_send(self, method, params):
        target = int(params.get('accountID') or 0)
        msg = params.get('message') or {}
        if isinstance(msg, str):
            try:
                msg = json.loads(msg)
            except ValueError:
                pass
        queue_message(target, msg)
        return 200, self._dump({'queued': True})

    def h_admin_cloud_get(self, method, params):
        actor = {'accountID': int(params.get('accountID') or 0)}
        doc = _load_doc(actor['accountID'], params.get('docKey') or 'default')
        return 200, self._dump(doc)

    def h_web_index_html(self, method, params):
        page = ('<html><head><title>Darksiders 2 Local Server</title></head>'
                '<body><h1>Darksiders 2 local THQ Connect server</h1>'
                '<p>The servers are alive again. Death rides.</p></body></html>')
        return 200, page.encode()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

class Server(ThreadingHTTPServer):
    """HTTP(S) server with per-connection protocol auto-detection.

    Peeks the first byte of each connection: 0x16 = TLS ClientHello ->
    wrap with TLS; anything else is served as plain HTTP (and logged),
    so we can see exactly what the client sends either way.
    """
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, config, tls_ctx=None):
        ThreadingHTTPServer.__init__(self, addr, handler)
        self.config = config
        self.tls_ctx = tls_ctx
        log('server %s:%s up (greet_8443=%s, greet_tls=%s, empty_doc=%s, doc_map=%s, '
            'get_map=%s, mapget_map=%s)'
            % (addr[0], addr[1], bool(config.get('greet_8443')),
               bool(config.get('greet_tls')), EMPTY_DOC_RECORD, DOC_MAP,
               GET_MAP or 'off', MAPGET_MAP or 'off'))

    def get_request(self):
        while True:
            conn, addr = self.socket.accept()
            try:
                # TLS/plain detection with a bounded wait. 443 needs LONG:
                # the game connects (WSAEWOULDBLOCK on its non-blocking
                # socket) and only sends its ClientHello ~1.1s later
                # (measured: connect 22:46:10.041 -> ClientHello
                # 22:46:11.164). A 250ms peek misclassified it as plain
                # HTTP -> 400 -> client TLS died on 'HTTP/1.' garbage.
                # 8443 needs SHORT: with the sync-connect proxy fix the
                # game holds each 8443 socket only ~800ms waiting for
                # server-first data before closing (measured 2026-08-30).
                # A 5s peek would still be waiting when the peer closes
                # and the greeting would land on a dead socket.
                peek = b''
                peek_eof = False
                conn.setblocking(False)
                budget = 45 if self.server_address[1] == 8443 else 500
                for _ in range(budget):
                    try:
                        peek = conn.recv(2, socket.MSG_PEEK)
                        if not peek:
                            peek_eof = True   # peer closed while we waited
                        break
                    except BlockingIOError:
                        time.sleep(0.01)
                    except OSError:
                        peek = b''
                        break
                conn.setblocking(True)
                conn.settimeout(30)

                # 8443 server-first greeting: after the TLS peek (so a late
                # ClientHello is caught first); only for connections that
                # arrived with no TLS bytes and are still open. One
                # candidate per connection (own rotation); never close
                # after sending - the game must be free to react.
                if (self.server_address[1] == 8443
                        and self.config.get('greet_8443')
                        and peek[:1] != b'\x16'
                        and not peek_eof
                        and self.tls_ctx is not None):
                    try:
                        tag, payload = next_plain_greeting()
                        if payload:
                            conn.sendall(payload)
                            log('<< plain-greeting[%s] %d bytes sent (8443 '
                                '%s:%s)' % (tag, len(payload),
                                            addr[0], addr[1]))
                    except OSError as e:
                        log('8443 greeting send failed from %s:%s: %r'
                            % (addr[0], addr[1], e))
                        try:
                            conn.close()
                        except OSError:
                            pass
                        continue
            except OSError as e:
                log('connection probe failed from %s:%s: %r' % (addr[0], addr[1], e))
                try:
                    conn.close()
                except OSError:
                    pass
                continue

            if peek[:1] == b'\x16' and self.tls_ctx is not None:
                try:
                    try:
                        lport = conn.getsockname()[1]   # local port: 443 vs 8443
                    except OSError:
                        lport = 0
                    conn = self.tls_ctx.wrap_socket(conn, server_side=True)
                    cipher = conn.cipher()
                    cert = conn.getpeercert()
                    subj = ''
                    if cert:
                        subj = ''.join('/%s=%s' % (kv[0], kv[1])
                                       for rdn in cert.get('subject', ())
                                       for kv in rdn)
                    log('>> TLS handshake OK %s:%s -> local:%s cipher=%s peer_cert=%s'
                        % (addr[0], addr[1], lport, cipher, subj or 'none'))
                    tag, payload, close_after = (None, None, False)
                    # 2026-09-11: 8443 is libcurl HTTPS (client-first). The game
                    # sends its GET /thqconnect/... request immediately after the
                    # TLS handshake; a server greeting would corrupt the HTTP
                    # request/response. Only 443 uses the greeting (config-doc).
                    if self.config.get('greet_tls') and lport != 8443:
                        tag, payload, close_after = next_tls_greeting()
                    if payload:
                        try:
                            conn.sendall(payload)
                            log('<< greeting[%s] %d bytes sent (tls %s:%s -> local:%s)'
                                % (tag, len(payload), addr[0], addr[1], lport))
                            if close_after:
                                # EOF-style delivery test: the REAL THQ server
                                # sends the config doc and closes PROMPTLY; the
                                # client (~108ms measured) never reads our doc
                                # when the server holds the connection open 5s,
                                # then aborts with close_notify. So: send, give
                                # a short beat for any fast reply, CLOSE — the
                                # client sees EOF and (hypothesis) finally reads
                                # its buffered doc. 2026-09-06 experiment.
                                conn.settimeout(0.15)
                                try:
                                    reply = conn.read(65536)
                                    if reply:
                                        log('tls recv %d bytes (reply to '
                                            'greeting[%s] fast-close): %s'
                                            % (len(reply), tag, reply.hex()))
                                    else:
                                        log('greeting[%s]: eof right after '
                                            'doc (peer closed)' % tag)
                                except socket.timeout:
                                    log('greeting[%s]: no fast reply in 150ms; '
                                        'closing immediately (EOF delivery)'
                                        % tag)
                                except (ssl.SSLError, OSError) as e:
                                    log('greeting[%s] read err: %r' % (tag, e))
                                try:
                                    conn.unwrap()   # graceful close_notify
                                except OSError:
                                    pass
                                conn.close()
                                continue    # accept the next connection
                        except OSError as e:
                            log('greeting[%s] send failed: %r' % (tag, e))
                    return conn, addr
                except Exception as e:
                    log('!! TLS handshake FAILED %s:%s -> local:%s peek=%s: %r'
                        % (addr[0], addr[1], lport, peek[:10].hex(), e))
                    try:
                        conn.close()
                    except OSError:
                        pass
                    continue
            else:
                if peek:
                    try:
                        lport = conn.getsockname()[1]
                    except OSError:
                        lport = 0
                    log('.. plain-text/unknown proto %s:%s -> local:%s, first bytes: %s'
                        % (addr[0], addr[1], lport, peek.hex()))
                return conn, addr

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        log('!! connection error from %s:%s: %r' %
            (client_address[0], client_address[1], exc))


def make_tls_context(config, port=None):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    # The game ships OpenSSL 1.0.0 (TLS 1.0 era) - allow legacy negotiation.
    try:
        ctx.minimum_version = ssl.TLSVersion.TLSv1
    except (AttributeError, ValueError):
        pass
    try:
        ctx.set_ciphers('DEFAULT@SECLEVEL=0')
    except ssl.SSLError:
        log('warning: could not set SECLEVEL=0 cipher string; old TLS may fail')
    # Present ONLY the leaf certificate, without the CA that signed it.
    # The game may pin either the embedded Root CA (chain -> root works)
    # or one of the embedded leaf certs as an exact-match trust anchor
    # (an extra CA cert above the leaf would break that match).
    # use_client_ca_cert=True -> present the certificate signed by the
    # ORIGINAL embedded "THQ Connect Client CA" key (recovered from the exe),
    # for game builds that trust that CA for server certificates.
    if config.get('use_client_ca_cert'):
        chain = os.path.join(CERT_DIR, 'server-clientca.crt')
        key = os.path.join(CERT_DIR, 'server.key')
        log('using Client-CA-signed server certificate (server-clientca.crt)')
    else:
        clone_n = int(config.get('serve_leaf_clone') or 0)
        ckey = os.path.join(CERT_DIR, 'leaf%d_clone.key' % clone_n)
        slot_chain = os.path.join(CERT_DIR, 'leaf%d_slot.pem' % clone_n)
        ca_chain = os.path.join(CERT_DIR, 'leaf%d_ca.pem' % clone_n)
        ss_chain = os.path.join(CERT_DIR, 'leaf%d_clone.pem' % clone_n)
        if clone_n and os.path.exists(slot_chain) and os.path.exists(ckey):
            chain, key = slot_chain, ckey
            log('serving leaf%d_slot: byte-EXACT copy of the exe leaf slot '
                '(original identity, Root-CA-signed) -> chain-verify AND '
                'byte-exact pin both satisfied' % clone_n)
        elif clone_n and os.path.exists(ca_chain) and os.path.exists(ckey):
            chain, key = ca_chain, ckey
            log('serving leaf%d_ca: ORIGINAL identity (full DN *.thqconnect.com, '
                'same serial) signed by our Root CA -> chain-verify AND leaf-slot '
                'identity/pin both satisfied' % clone_n)
        elif clone_n and os.path.exists(ss_chain) and os.path.exists(ckey):
            chain, key = ss_chain, ckey
            log('serving SELF-SIGNED leaf%d clone (full-DN identity, same serial)'
                % clone_n)
        else:
            chain = os.path.join(CERT_DIR, 'server.crt')
            key = os.path.join(CERT_DIR, 'server.key')
    ctx.load_cert_chain(chain, key)
    # 2026-09-08: 8443 is libcurl (the GAME's 'Running https://…8443/…' +
    # 'PEM_read_bio_X509 failed'), which presents NO client certificate. Only
    # the TLS-on-443 handshake presents the game's CN=DS2 client cert. So do
    # NOT request a client cert on 8443 (a CertificateRequest makes libcurl
    # fail PEM immediately -> instant close). Default = verify off per-port
    # via config 'verify_client_cert_ports' (e.g. ['443']).
    verify_ports = [int(p) for p in
                    (config.get('verify_client_cert_ports') or [])]
    do_verify = bool(config.get('verify_client_cert')) \
        and (port is None or port in verify_ports)
    if do_verify:
        cca = os.path.join(CERT_DIR, 'client_local_ca.pem')
        if os.path.exists(cca):
            ctx.load_verify_locations(cca)
            ctx.verify_mode = ssl.CERT_OPTIONAL
            log('mutual-TLS on: CertificateRequest sent, anchor=%s '
                '(game presents its CN=DS2 client cert signed by our local '
                'Client CA)' % cca)
    return ctx


def check_port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(('0.0.0.0', port))
            return True
        except OSError:
            return False


def main():
    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(CLOUD_DIR, exist_ok=True)

    config = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config.update(json.load(f))
        except (OSError, ValueError) as e:
            log('config load failed (%s), using defaults' % e)
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)

    log('=' * 60)
    log('Darksiders 2 local server starting')
    log('  data dir: %s' % DATA_DIR)

    servers = []
    tls_ctx = make_tls_context(config) if config.get('enable_tls', True) else None
    ports = list(config.get('https_ports') or [config.get('https_port', 443)])
    for port in ports:
        port = int(port)
        if not check_port_free(port):
            log('note: port %d busy, skipping (change https_ports in '
                'server/config.json)' % port)
            continue
        # One TLS context per port: the game's CN=DS2 mTLS client cert is only
        # presented on 443; 8443 (libcurl) must NOT get a CertificateRequest.
        pctx = make_tls_context(config, port=port) if tls_ctx else None
        srv = Server(('0.0.0.0', port), Handler, config, tls_ctx=pctx)
        servers.append(srv)
        if pctx is not None:
            log('  listening on 0.0.0.0:%d (TLS auto-detect, client-cert-%s)' %
                (port, 'requested' if pctx.verify_mode != ssl.CERT_NONE
                 else 'none'))

    port = int(config.get('http_port', 80))
    if not check_port_free(port):
        log('note: port %d busy, plain HTTP disabled' % port)
    else:
        servers.append(Server(('0.0.0.0', port), Handler, config))
        log('  HTTP  listening on 0.0.0.0:%d' % port)

    if not servers:
        log('no listeners started - exiting')
        sys.exit(1)

    threads = []
    for srv in servers:
        t = threading.Thread(target=srv.serve_forever, kwargs={'poll_interval': 0.5})
        t.daemon = True
        t.start()
        threads.append(t)

    log('ready. Waiting for the game...')
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        log('shutting down')
        for srv in servers:
            srv.shutdown()


if __name__ == '__main__':
    main()