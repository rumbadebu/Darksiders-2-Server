# Darksiders 2 Private Server — STATE (single source of truth)

> **AGENT: READ THIS FILE FIRST after ANY context loss (compaction, model switch, restart).**
> It is the authoritative current state. Do NOT re-derive state from logs.
> After EVERY game test/experiment: (1) update "CURRENT STATE" below, (2) append a dated entry to `JOURNAL.md`.
> Log rules: NEVER `Get-Content` ds2_tls.log whole (447 MB) — use `python tools/reframe.py <log> --tail N --level gift`.
> One command per tool call (parallel calls race the terminal); keep every output tiny.
> Archives (deep reference, read only when needed): `GIFT_PROTOCOL_FALLBACK.md` (protocol/RE reference),
> `CONTINUE_HERE.md` (session history through 2026-10-11), `JOURNAL.md` (append-only log from 2026-10-11).

## CURRENT STATE (updated 2026-10-14)

**Goal: ACHIEVED.** Recipient inbox renders received gifts; sender name is correct
(from blob `GifterName`, per-gift — the old "gift from yourself" bug is gone). Full
cycle send -> login -> inbox render -> collect verified in BOTH directions (playerA
<-> playerB). Mail clears on collect (MAP_REMOVE with the real gift id).

### Works
- Login/mTLS; all THQ endpoints (platformLogin, ssoStatus, getNews, cloud/execute)
- SEND: `MAP_SET` writes base64 BOD blob into recipient's TomeGift doc; sender from blob `GifterName`
- RECEIVE (SOLVED 2026-10-14): armed docshape `[%E%] => {"%K%":"%V%","data":1}` on the
  MAP_GET route. On inbox open the walk (0xb44f96) fires per entry, base64-decodes the
  blob -> BOD, stores it under the REAL gift id (`WALKAEF key='<giftId>'`), fires the
  VCALL notification, and the row renders. Collect sends the real gift id via MAP_REMOVE.
- Blob format: b64 -> `BOD`+17B header (inflated len u32 @+16) -> zlib stream @+20 -> payload;
  string framing `[8B hash][u16 len][chars]`; giftId = 19-digit decimal. BOD value-type
  tags: 07 root obj, 09 array, 04 obj, 0f string, 02 num/bool, 0d terminator; armor item
  tag=04, weapon item tag=03 (+weapon-only fields). No fresh blob is string-where-object.
- Proxy: `binkw32.dll` (Bink forwarder + TLS log + probe loader; **after ANY rebuild re-run
  decorate_eat.py or use tools/tls_proxy/build_rust.bat** — bare cargo output is NOT
  deployable) + `ds2_probe.dll` (instrumentation, loaded via GetProcAddress).
  DEPLOY TRAP: the game-dir ds2_probe.dll was reverted to a STALE no-VEH build (183296 B)
  at least twice; the correct VEH build is 196608 B. ALWAYS verify by marker string
  (`VEH registered`/`thunk_fn`/`code_bytes`) + LastWriteTime, never by hash/exit code.
- No server restart needed for doc/store edits: `_load_doc` re-reads the file per request.

### Open — nothing blocking
- None. Both mailboxes empty & healthy after collect.
- Optional: root-cause the OLD pre-fix axe blob (15141810279299964802, sent 2026-10-05)
  that alone still triggers the 0x496d5c type-confusion render crash. It is stale data
  that no longer occurs; fresh weapons (hammer, fresh axe) render fine and refuted both
  the "count" and the "00 vtag" theories. Low priority.
- The unjournaled 10-14 17:44 run (empty TomeGift) showed a DIFFERENT bug: `lock xadd`
  (InterlockedDecrement) refcount UAF on inbox UI teardown — not the receive path.

### Latest evidence (2026-10-11, probe — server/logs/ds2_probe.log)
- Gift walk FIRES: `DECODE #0 ret=b44fd4 ... size=20 cap=31 head=15717548710165239937 <- GIFT WALK`
  => walk @0xb44f96 base64-decodes the document KEY string (the giftId, 20 chars -> 15 bytes), once per entry.
- **Exp B VERDICT (19:43, first STABLE run) — WALK MECHANICS OBSERVED:** with the index
  `[{"key":"<giftId>","data":1}]` the walk fires once: decodes the giftId -> 15 bytes,
  assigns it into the gift object (`WALK48 0x48c56b`), and builds the store pair
  (`WALKAEF 0xaef713 key='key' val=<binary> flag=1`). **Conclusion: the walk decodes each
  document member's VALUE and stores {member-name: decoded-value}.** The gift payload must
  be the VALUE: serving `[{"<giftId>":"<blob>"}]` makes the walk decode the blob to the
  BOD binary (the real gift) and store it under the NUMERIC gift id — which also explains
  the old 0x96d5c comparator AV (pair key "key" is non-numeric).
- Probe log lives in `server/logs/` (DS2_TLS_LOG_DIR), NOT the game dir. Rotate before each test.
- Probe timestamps show "2026-11-00" (broken date format — cosmetic).

### Armed config (live)
- server **round_1024** (pid 46688): env DS2_EMPTY_DOC=1 DS2_DOC_MAP=1 DS2_TRACE=1
  DS2_GET_MAP=obj DS2_MAPGET_MAP=off; docshape/docslot re-read per request
- `server/docshape.txt`: `[%E%] => {"%K%":"%V%","data":1}` ← **armed: {giftId: blob, data:1}**
  (the 19:58 `{giftId: blob}` was parser-rejected; the two-member schema is required)
- `server/docslot.txt`: `0=0 4="playerB"`
- Game dir: binkw32.dll (proxy E8A1F1EFF80C, EAT-decorated, probe-loader present) +
  **ds2_probe.dll DF5757C42943 — DECODE @0xb4394c, WALK48 @0xb44fe1, WALKAEF @0xb44ffc,
  STRBAD @0x496d4d (now also logs the walk's post-loop virtual call ret=0xb45049)** +
  binkw32_real.dll. Hash parity verified. Kill switch:
  DS2_PROBE_OFF=1 (load, no patch); rollback: delete ds2_probe.dll.

### Logging & rotation (2026-10-11)
- All writers rotate on size; env `DS2_LOG_MAX_MB` overrides (defaults: proxy ds2_tls.log
  4 MB, probe ds2_probe.log 1 MB, server requests.log/trace.log 4 MB). Rotated files are
  `<stem>_<n>.log`, each rotation writes a banner line — traceable. round_*_stdout.txt
  rotates per server launch.
- ds2_tls.log + ds2_probe.log land in `server/logs/` (DS2_TLS_LOG_DIR points there).
- Test: `python tools/test_log_rotation.py` (6 checks). Bounded reading:
  `python tools/reframe.py <log> --tail N --level gift` — never read ds2_tls.log whole.

### Store (gifts per mailbox)
- Both mailboxes currently EMPTY (all test gifts collected successfully 2026-10-14).
- Backup of the last populated state: `server/data/cloud_backup_20261014_214017`.
- Historical: playerA 100000001 had gloves/hammer from playerB; playerB
  100000002 had boots/axe+hammer from playerA — all rendered + collected cleanly.
  The stale pre-fix axe `15141810279299964802` was the lone crasher (see focus).

### >>> CURRENT FOCUS (2026-10-14) — RECEIVE SOLVED; nothing blocking

The receive path works end to end and is verified in both directions. The exact
mechanism, from the probe:
`DECODE #0 (blob) -> WALK48 (BOD) -> WALKAEF key='<giftId>' -> VCALL #0 -> row renders`.
Sender name comes from the blob's `GifterName` (per-gift, so the "gift from yourself"
bug is gone). Collect sends the real gift id via `MAP_REMOVE` and the mailbox clears.

The 2026-10-13 crash (0x496d5c, "string where object expected") was the OLD pre-fix
axe blob (15141810279299964802, sent 2026-10-05) ONLY. Isolation proved it: dropping
that one gift left the fresh boots to render with no crash; then playerA received a
fresh hammer+gloves pair AND a fresh axe+hammer envelope — all rendered fine with
correct attribution. That refuted BOTH "the count (2 gifts)" and "the 00 vtag"
theories (fresh weapons carry the 00 vtag too and render). So the type-confusion was
stale data, not a live defect. Ingest was never the problem; the empty playerA
mailbox on 10-13/10-14 was just a gift blob that had been emptied during testing
(restored with `_reseed_gift.py`).

### Next steps (optional, non-blocking)
1. **Root-cause the old axe blob** (what its pre-fix format got wrong) — low priority;
   it no longer occurs. Its BOD value-type-tag sequence is in JOURNAL 2026-10-14.
2. **The 10-14 17:44 empty-inbox crash** (`lock xadd` / InterlockedDecrement refcount
   UAF on inbox UI teardown) is a separate latent bug — worth a look only if empty
   mailboxes are hit often.
3. Before ANY probe rebuild: re-deploy the 196608 B VEH build and verify markers
   (`VEH registered`/`thunk_fn`/`code_bytes`) + LastWriteTime — the game-dir DLL was
   reverted to a stale no-VEH build twice.

### Archived: the old fix ladder (SOLVED — kept for reference)
- **A (locked in):** `[%E%] => {"%K%":"%V%","data":1}` on MAP_GET — giftId-named member
  + blob value. Probe: `DECODE head=Qk9E…` -> `WALKAEF key='<giftId>'`. This IS the fix.
- B/C were never needed. Kill switch DS2_PROBE_OFF=1; rollback: delete ds2_probe.dll.
