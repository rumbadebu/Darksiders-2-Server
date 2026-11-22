# ANALYSIS TIMELINE (fast-deliberation log)

Purpose: one short, dated, evidence-first entry per investigation round. The raw
`ds2_tls.log` is 300+ MB per run and is **deleted on every deploy** (the build
script does `del ds2_tls.log`); anything worth keeping from a run MUST be
summarized here before the next launch. `CONTINUE_HERE.md` stays the long-form
journal; this file is the index you read first.

Workflow per round:
1. game closed -> extract (game verdicts, PEEK trail, server log tags)
2. append a dated entry below (evidence + conclusion + next step)
3. apply the server/DLL change
4. deploy (auto-clears ds2_tls.log) + restart server with fresh stdout
5. only then propose a launch

---

## 2026-09-16 - SHARED ASYNC FORMAT CHECK: both shapes fail at byte 3

### Standing facts (unchanged from earlier rounds)
- 443 mTLS + libcurl HTTPS on 8443 fully working; the game sends real requests:
  `cloud/execute`, `login/platformLogin`, `login/ssoStatus`, `news/getNews`.
- Steam fake-ticket accepted (`OnAuthToken - succeeded`); no more crashes.
- DLL hooks live: `[GAME]` console sink, GST hook, 163 synth, PEEK dumper on
  the response tokenizer (`0xaf7d65`), closesocket caller logging.

### Evidence from this round (09-16 05:43 session)
The game prints TWO lines per request; the first is TRANSPORT, the second is the
FORMAT VERDICT. `response good` does NOT mean the format check passed:
```
[GAME] AccountPlatformLoginAsync - response good: 1274 bytes, {"status":0,...
[GAME] AccountPlatformLoginAsync - response was not formatted correctly: {"status":0,...
[GAME] CheckSSOStatusAsync     - response good: 46 bytes, [0[][]["Player2112"]0[[["Player2112"]123123]]]
[GAME] CheckSSOStatusAsync     - response was not formatted correctly: [0[][][...
```
=> KEYED object (full schema, correct types) FAILS.
=> POSITIONAL commaless `[0[][]["X"]0[[["X"]123123]]]` FAILS TOO.

PEEK trail (tokenizer's own read positions, ret-tagged build):
```
KEYED      {"status":0,...   -> peek @+0 '{' , @+1 '"' , @+2 's'  -> rejected
POSITIONAL [0[][][]...       -> peek @+0 '[' , @+1 '0' , @+2 '['  -> rejected
```
At +2 the same three value-dispatch probes fire (`0xaf7f90` / `0xaf7f9c` /
`0xaf7fa6`) and all decline.

### Conclusions
1. The checker is positional (no key names compared) BUT the commaless positional
   form is also rejected at its 3rd byte -> the **top-level container and/or the
   separator grammar is still unknown**. Two facts pin it down:
   - after `{` `"` it wants a NON-name token (a digit) -> not a normal JSON object,
   - after `[` `0` it rejects `[` -> either separators are REQUIRED (`,`) or the
     url field is not a list.
2. Key names exist in the exe (`url`@0xed7db0, `users`@0xed8048,
   `username`@0xed8054, `status`@0xd884ac) but are schema metadata, not compared
   against the response text (keyed text fails at byte 3).

### Next step (implemented this round)
FORMAT SWEEP: serve a different candidate shape per ssoStatus retry, tagged in
the server log as `ssoStatus fmt#<n> <name>`, and read the `[GAME]` verdict for
each. Candidates chosen to isolate container vs separator vs field-type:
  fmt#0 outer-array + commas   `[0,[],[],["X"],0,[[["X"],ID,ID]]]`
  fmt#1 bare sequence          `0[][]["X"]0[[["X"]ID ID]]`
  fmt#2 numeric-keyed object   `{"0":0,"1":[],...,"5":[...]}`
  fmt#3 unquoted numeric keys  `{0:0,1:[],...,5:[...]}`
  fmt#4 field-wrapped arrays   `[[0],[[],[]],[["X"]],[0],[[[[..]]]]]`
  fmt#5 control positional     `[0[][]["X"]0[[["X"]IDID]]]`  (known fail)
One launch = up to 6 verdicts. The first candidate with NO
"was not formatted correctly" names the grammar.

### Known-good server behaviours (do not regress)
- `_dump()` wraps every payload as a JSON ARRAY `[{...}]` (line ~747, the LIVE
  one - there is a dead duplicate `_dump` at ~703 that is never called).
- 8443 never greets (client-first HTTPS); 443 keeps mTLS + the 323B doc.

---

## 2026-09-16 07:1x - BREAKTHROUGH: RESPONSE GRAMMAR PROVEN (fmt#0 ACCEPTED)

### Evidence (one launch, 6-way sweep)
`fmt#0` = `[0,[],[],["<name>"],0,[[["<name>"],acctID,profID]]]` produced, in the
game console (`>> PARSE`/`[GAME]` sink lines):

    CheckSSOStatusAsync - response good: 69 bytes, [0,[],[],["Player92656"],0,[[["Player92656"],3286492656,3286492656]]]
    AsyncJobDriver::DeleteJob[6] CheckSSOStatusAsync      <-- NO "was not formatted correctly"

All other candidates (bare sequence, numeric-keyed object, unquoted keys,
field-wrapped arrays, commaless control) retried with
`response was not formatted correctly`.

### PEEK trail = exact grammar walk
    @+0  [            outer ARRAY opens
    @+1  0            field0 `status`  : NUMBER
    @+2  ,            comma REQUIRED (af7e86 = expect-comma)
    @+3  []           field1 `url`     : array of strings
    @+5  ,[]
    @+7  ,["<name>"]  field3 `username`: array of strings
    @+17 ,0           field4 `age`     : NUMBER
    @+19 ,[[["<name>"],acctID,profID]]   field5 `users`: array of
                      [ [name...], accountID, profileID ]

### Conclusion
The shared async checker (0xafa77d) tokenizes a **positional sequence inside an
outer array, commas mandatory**. It never reads JSON key names - a keyed body
dies at byte 3 (the opening key quote is handed to the NUMBER reader).
Server now emits this grammar from `_pos_base()`/`_pos_arr()`.

### Next step (implemented this round, awaiting launch)
1. `ssoStatus` LOCKED to the proven 6-field positional form (no more sweep).
2. `platformLogin` FORMAT-TAIL SWEEP - the 6 fields are proven but platformLogin
   carries login extras (sessionID / auth / profileInfo) whose POSITION is
   unknown. One tail per retry, tagged in the server log:
     fmt#0 t0-no-tail
     fmt#1 t1-sid+auth+profile-triple     [sid, auth, [name,acctID,profID]]
     fmt#2 t2-flat-scalars                [sid, auth, name, acctID, profID]
     fmt#3 t3-sid+auth+triple+localprofile
   Winner = the first whose `[GAME]` verdict has `response good` with NO
   "was not formatted correctly".
3. `readSession`/`getNews`/`tunnel`/`cloud` handlers all emit positional now.
4. DLL: `>> PARSE #n START len= ret= text=` groups each response's full text
   with its PEEK walk (peek cap raised to 900) so one response = one block.

### Pre-launch state (verified)
- repo == Steam `binkw32.dll` sha `9E7F6B9A...`, markers `PARSE #` + `PEEK ret=`
  present in the deployed binary.
- `ds2_tls.log` CLEARED before launch.
- server UP (07:35:16) emitting `[0,[],[],["Player5675"],0,[[["Player5675"],2883405675,2883405675]]]`
  for ssoStatus (verified in server log at 07:35:39).

---

## 2026-09-16 15:5x - ROUND: ARG hook (expect/check argument logging)

### Why this round
fmt#0 positional grammar is ACCEPTED by ssoStatus (proven). Two endpoints still
reject the same bytes, each dying at a different place in the walk:
- **platformLogin** stops at **+25** - inside an `expect()` (hard requirement).
- **CallCloud** stops at **+6** - inside a `check()` (probing test).

So each endpoint has a **bespoke parser** (platformLogin readers
`afa293`/`afa3aa`/`afa4c1`, CallCloud `b3264c`) and the missing information is
*which character each parser demands at the failing offset*.

### Tokenizer primitives (identified this round)
| addr | primitive | behavior |
|------|-----------|----------|
| `af7d65` | `peek()` | returns current char (no consume) |
| `af7d86` | `skip_ws()` | peek -> `af7c12`(isspace) -> advance |
| `af7db8` | `expect(c)` | peek, `cmp c,al`, consume on match |
| `af7def` | `check(c)` | non-consuming test |
| `af7e6b` | separator | accepts `,` **or** `:` |
| `af7e16` | begin | expects `[` then optional `{` |

### New instrument
`expect()` (0xaf7db8) and `check()` (0xaf7def) share the proven 6-byte prologue
(`55 8B EC FF 75 08`), so both are patched with the established technique and log:

```
>> ARG expect(af7db8) want=0x5b '[' got=0x2c ','' @+19/46 ret=afa2xx
```

- `want=` names the character the parser requires at that point.
- `got=` is the character actually present (reconstructed from the tokenizer
  state: `engine=*(s+4)`, `data=*(engine)`, `len=*(engine+8)`, `pos=*(s+8)`).
- `ret=` names the **caller**, i.e. which endpoint's reader is asking.

**Caller filter (essential):** expect/check are the tokenizer's hottest
primitives - an unfiltered cap is exhausted by unrelated parsing before the
login response is ever tokenized. Only callers in the response-parser ranges are
logged: `0xaf7c00-0xafc000` (tokenizer + generic readers),
`0xb32000-0xb32fff` (CallCloud parser), `0xb02800-0xb02cff` (the format-check
call site at `b02ac8`). Cap raised to **3000** entries.

### Also still armed
`platformLogin` 4-candidate tail sweep, one per retry, tagged in the server log
`platformLogin fmt#n <name>`:
`t0-no-tail` / `t1-sid+auth+profile-triple` / `t2-flat-scalars` /
`t3-sid+auth+triple+localprofile`. Winner = the tag whose `[GAME]` verdict has
`response good` with **no** `was not formatted correctly`.

`ssoStatus` stays LOCKED to the proven 6-field positional form (`_pos_arr`),
so it cannot regress while the other endpoints are being decoded.
`_dump` is back to a bare keyed object (correct for the GDS JSON-RPC path).

### Pre-launch state (verified)
- game **closed**; `ds2_tls.log` **CLEARED** (absent).
- repo == Steam `binkw32.dll` sha `028B1EFA...` (275968 B, 15:51:19); markers
  `>> ARG ` + `ARG patch` + `PARSE #` + `PEEK ret=` all present **in the
  deployed binary** (content check).
- server UP (15:52:19), all three ports; ssoStatus emitting the proven
  positional form.

### Expected outcome
One launch (~25-30 s of retries) yields, per endpoint, an ARG trail ending at
the exact failing offset with the exact character demanded - e.g. if
platformLogin's tail must be `[sid, auth, [name,acct,prof]]` the ARG line will
show `want=0x5b '['` at the position our tail put a scalar. That closes the
guesswork: the server shape becomes a direct transcription of the ARG trail.

---

## 2026-09-18 14:49 - ROUND: platformLogin field-4 + CallCloud 2-field sweeps

### Evidence read (previous session's ARG trails, offsets are HEX)
- `ssoStatus` ACCEPTED: full walk ends at `@+0x43` (67 B) = our body length.
  Field map = `[status(num), url(list), email(list), username(list), age(num), users(triples)]`.
- `platformLogin` REJECTED at **`@+19`** (hex 25): the parser demands `['` **or**
  `{` there, but our body has the scalar `0` (the `age` slot). So platformLogin's
  field 4 is **NOT `age`** - it is an array/object (i.e. the users array, like sso).
  Its bespoke readers `afa25b`/`afa372`/`afa489` run for url/email/username.
- `CallCloud` (`cloud/execute`) REJECTED at **`@+6`** with `check(']')`: its reader
  consumes field1 then immediately expects the list to CLOSE. So CallCloud is a
  **2-field** shape `[status, <one list>]`, not the 6-field prologue.

### This round's change (server only - no DLL change needed)
- `platformLogin` = **field-4 type sweep**, one candidate per retry, tagged
  `platformLogin fmt#n <name>`:
  | # | name | tail |
  |---|---|---|
  | 0 | p0-users4-age5 | `[..., users-triples, 0]` |
  | 1 | p1-users4-5field | `[..., users-triples]` (5 fields) |
  | 2 | p2-emptylist4-age5 | `[..., [], 0]` |
  | 3 | p3-emptyobj4-age5 | `[..., {}, 0]` |
- `cloud/execute` = **2-field shape sweep**, tagged `cloud fmt#n <name>`:
  | # | name | body |
  |---|---|------|
  | 0 | c0-status+emptylist | `[0,[]]` |
  | 1 | c1-status+namelist | `[0,[["X"]]]` |
  | 2 | c2-status+usertriples | `[0,[[["X"],a,p]]]` |
- `ssoStatus` stays **LOCKED** to the proven `_pos_arr(_pos_base(...))` form so it
  cannot regress while the other two are decoded.

### Verified cycling (probe, 14:48:06-14:48:18, server log)
- `platformLogin fmt#0..3` all four bodies observed in sequence.
- `cloud fmt#0/1/2` all three observed.
- `ssoStatus` -> `[0,[],[],["Player94770"],0,[[["Player94770"],2683694770,2683694770]]]`.

### Pre-launch state (verified)
- game **closed**; `ds2_tls.log` **CLEARED** (absent).
- repo == Steam `binkw32.dll` sha `028B1EFA...` (275968 B) - unchanged this round
  (ARG + PARSE + PEEK instrumentation already deployed).
- server UP (14:49:04) with sweep counters **reset to 0**, so the game's first
  `platformLogin` gets `fmt#0` and the first `cloud` gets `fmt#0`.

### Expected outcome
One launch (~30 s) yields up to 4 platformLogin verdicts + 3 cloud verdicts.
Winner = the tag whose `[GAME]` line shows `response good: N bytes` with **no**
following `was not formatted correctly`. Correlate by the `fmt#n` tag + the
same-second `>> PARSE #n` block, then read that block's ARG trail to confirm the
parse ran to EOF (`@+bodylen`).

---

## 2026-09-18 14:58 - CORRELATOR TOOL + ROUND RE-ARMED (no launch yet)

Round-2 of the field-4/cloud sweep (the 09-17 arming, never launched: the
09-17 16:27 attempt produced no `ds2_tls.log`, i.e. the DLL was never reached).

### New: `tools/correlate_fmt.py` (fast-deliberation tool)
Pairs the server's sweep tags with the game's verdicts automatically, so a
launch is read in one command instead of manual grep-joining:
- S1: parses `server/logs/server_new_stdout.txt` for `fmt#n` tags + request lines,
- S2: parses `ds2_tls.log` for the four verdict strings,
- S3: pairs tag -> verdicts within a 45 s window,
- S4: summary per `(endpoint, fmt#)`, flags **WINNER** (accepted),
- S5: ARG walk of the last N parse groups (want/got/@+pos + body head).
Fixed two defects during bring-up: a truncated `main()` (missing) and a
`TypeError: 'NoneType' object is not subscriptable` when a section had no data
(guarded early-return at the "no ARG lines" branch). Verified exit 0 with an
empty log (all sections degrade gracefully).

### New RE find: FOUR verdict strings, not three
Searched `Darksiders2.exe.orig` for the async-complete messages; besides
`response good`, `was not formatted correctly` and the job-name line there is a
fourth: `%s - (used %u/%u) extra data ignored: %s`. That is a **partial
consumption** report - a response can parse successfully yet leave a tail
unread. It must be treated as a distinct outcome (may still advance the flow),
not as a failure. The correlator already reports it separately.

### Re-armed state (verified)
- game **closed**; `ds2_tls.log` **CLEARED** (absent).
- repo == Steam `binkw32.dll` sha `028B1EFA2FE00197` - unchanged this round
  (ARG + PARSE + PEEK instrumentation already deployed and confirmed in-binary).
- server **restarted 14:58:05** -> sweep counters reset, so the game's first
  `platformLogin` gets `fmt#0` and the first `cloud` gets `fmt#0`.

### Armed candidates (server side)
`platformLogin` (4) - field 4 is a LIST/OBJECT per the ARG trail
(`@+19 want='[' got=','` / `want='{' got='0'` where sso has scalar `age`):
`p0 [0,[],[],[name],users,0]` / `p1 [0,[],[],[name],users]` /
`p2 [0,[],[],[name],[],0]` / `p3 [0,[],[],[name],{},0]`.
`cloud` (3) - its own reader consumes ONE list then `check(']')` at +6:
`c0 [0,[]]` / `c1 [0,[[name]]]` / `c2 [0,users]`.
`ssoStatus` **LOCKED** to the proven fmt#0 6-field positional form (do not sweep).

### Next action
Launch the game, leave ~30 s, then:
`python tools/correlate_fmt.py` (add `--groups 3` for more ARG context).

---


---

## 2026-09-24 04:41 - VERIFIED IN-GAME: platformLogin passes the walk, login handshake completes

### The run (session 04:40:44 -> 04:42:12; server log `round_0924b_stdout.txt`)
Server served the first sweep candidate:
`ORACLE platformLogin v0/8 #1 bytes=15 body=[0,[],[],[],[]]`.
Client side, ONE pass of `PARSE #3`, no mismatch, no retry:

```
04:41:00.207 [GAME] AccountPlatformLoginAsync - response good: 15 bytes, [0,[],[],[],[]]
  expect '[' got '[' @+0  ret=af7e47   outer list
  expect '[' got '0' @+1  ret=af7f47   result (generic value: scalar accepted)
  expect '[' got '[' @+3  ret=afa25b   tunnels
  expect '[' got '[' @+6  ret=afa372   certificates
  expect '[' got '[' @+9  ret=afa489   gameSettings
  expect '[' got '[' @+c  ret=afa489   globalSettings   <- byte-25 gate PASSED
  check  ']' got ']'      ret=af7edf   close OK
  expect ':' got EOF      ret=af7eb0   (non-fatal trailing probe)
04:41:00.209 [GAME] AsyncJobDriver::DeleteJob[7] AccountPlatformLoginAsync
```

**No `AccountPlatformLoginAsync - response was not formatted correctly`** - the line that
ended every run from 09-18 through 09-23. The pl gate is closed: empty lists hit the
element binder's success fast path for every document field, exactly as the disasm predicted.

### ssoStatus re-confirmed in the same pass
`PARSE #4` walked all 45 payload bytes of the LOCKED fmt#0 body
(`CheckSSOStatusAsync - response good: 69 bytes, [0,[],[],["Player56048"],0,[[["Player56048"],2189056048,2189056048]]]`)
and `DeleteJob[8]` followed - the pl fix did not disturb the already-proven sso grammar.

### The Steam ticket path was healthy
`OnAuthToken callback: result=1 ticketHandle=28` + `SteamAuthManager::OnAuthToken - succeeded`
at 04:41:00.033 -> pl was issued. Then `GST(this=84801b0, buf=102ed9ac, max=2048) -> 1d` /
`request auth ticket with ticketHandle[29]` -> `OnAuthToken - succeeded` again at
04:41:00.952 -> ssoStatus was issued. Both tickets accepted, so neither request hit the
"auth token should not be empty" gate.

The `cb163 SYNTH: mgr+0x828 m_hAuthTicket=0 vs synth 1d [MISMATCH] / NOT setting state` at
04:41:02.043 is the +1500 ms late-synth tail firing after the manager had already consumed
and cleared the ticket. The 09-23 session shows the identical line
(`01:22:19.493 ... m_hAuthTicket=0 vs synth 18 [MISMATCH]`) while its login was fine -
COSMETIC, not a gate.

### Post-login request pattern is unchanged and normal
Complete session request list: cloud execute GET (04:40:47) -> cloud execute SET (04:40:48)
-> platformLogin (04:41:00) -> ssoStatus (04:41:01) -> nothing. Identical in shape to 09-23,
which then ran 9.5 minutes untouched, so the post-sso silence is the client's resting state
(the login UI is done; nothing else is requested from the title screen), not a regression.

The app was closed cleanly at 04:42:12: `AccountInternal::Disconnect` /
`~WorldwideLoginService - uninitializing` / 37-byte close_notify (alert 112) on the 8443
socket / `THQServerManager::Destroy` / `SteamAPI_Shutdown`.

### Frozen: the sweep is retired, v0 is the answer
`server/oracle/platformLogin.json` = `[0,[],[],[],[]]` (15 bytes) - the single-file oracle
path, so no variant cycling and every request gets the proven body (live-verified:
`ORACLE platformLogin #2 bytes=15 body=[0,[],[],[],[]]`, no `vN/8`). The eight candidates
moved to `server/oracle/parked/platformLogin.<0..7>.json` for future mining if a later stage
ever wants non-empty `tunnels` / `certificates` / settings (`v1`..`v7`; element shape is one
generic value + `key`/`value` members via 0xaf8288). The handler fallback in
`server/ds2_server.py` already serves the same v0 shape.

### Next action
Re-launch the game and take the login through to gameplay: the 4-request pattern above should
repeat with `ORACLE platformLogin #N` (no variant index), this time with no
"was not formatted correctly" anywhere. Then exercise the online flow that actually matters -
the cloud document ops (`TomeGift` / `GameToWeb` = item exchange) and the UI's profile name.

## 2026-09-24 - platformLogin grammar DECODED: it is the CONFIG-DOCUMENT binder (+ oracle repaired)

### The 09-18 sweep verdict had never been read - now it is
`PARSE #2` (15:50:25, body = candidate `p0`) cleared the byte-25 gate exactly as
predicted and died LATER, moving the question forward by 4 fields:

```
+19 expect '[' got '[' ret=afa489     <- gate PASSED: a LIST here is legal
+1a af7e47 '[' / +1b af82e2 '[' / +1c af830e '"' / +29 check ']'   <- element[0] OK
+2b expect '[' got '3' ret=af82e2     <- wants a LIST ...
+2b expect '"' got '3' ret=af830e     <- ... or a STRING
+36 check ']'  got '3' ret=af7edf     <- element must CLOSE here -> "not formatted correctly"
```
A bare NUMBER inside that element is what kills it; a list/string is fine.

### What the platformLogin body really is: the 443 config document
`find_calls.py 0xafa442` -> the binder is invoked by the config-doc walker straight
after the certificates reader, with member names `gameSettings` ([esi+0x30]) and
`globalSettings` ([esi+0x44]); the same walker reads `result` then `tunnels` first.
The live `expect()` return addresses map 1:1 onto that struct:

| slot | member | binder | live ret | our old body |
|---|---|---|---|---|
| +1 | `result` | generic value | af7f47 | scalar `0` ACCEPTED |
| +3 | `tunnels` | P1 0xafa214 | afa25b | `[]` ACCEPTED |
| +6 | `certificates` | P2 0xafa32b | afa372 | `[]` ACCEPTED |
| +9 | `gameSettings` | P3 0xafa442 | afa489 | `["N"]` ACCEPTED |
| +19 | `globalSettings` | P3 0xafa442 | afa489 / afa49e | `0` -> **HARD FAIL (byte 25)** |

P1/P2/P3 are 0x117-byte clones: gate `[` or `{`, element binder 0xaf8cc4 = one generic
value + members `key`/`value` read via 0xaf8288 (string OR list-of-strings), and an
EMPTY list is an explicit success fast-path (`jae 0xafa546`).

- The 09-21 lock ("no pl-specific grammar; 0xafa77d shared with ssoStatus") is DISPROVEN.
- The old 6-field body parked scalar `age` in the `globalSettings` slot = the byte-25 abort.
- Request side (GET `.../platformLogin?version=1&region=0&country=..&appID=50650&platform=PC_STEAM&title=Darksiders2&language=English&auth=<hex blob>`,
  no sessionID key; account/steamid decoded from the `auth` blob) does NOT carry any field
  list - the response grammar is chosen statically per endpoint, not by the request.

### Oracle was silently DEAD (explains the "no ORACLE lines" mystery)
`_SWEEP_LOCK` was referenced in `_oracle()` / `_oracle_serve()` but never defined ->
`NameError` on every oracle request -> swallowed by `_dispatch` -> fallback body served
and no log line. Fixed with `_SWEEP_LOCK = threading.Lock()` beside `_ORACLE_SEQ`, and the
variant cycle is now strictly 0,1,2,...,N-1 (only `_oracle_serve` bumps the counter), so the
FIRST request of a run always tests variant 0.

### Armed for the next launch (server restarted; 80/443/8443 up)
`server/oracle/platformLogin.<n>.json`, v0 first, one variant per request:
`v0 [0,[],[],[],[]]` (all document fields empty = every binder's fast path) /
`v1 [0,[],[],[],["N"]]` / `v2 [0,[],[],[],[["N"]]]` / `v3 [0,[],[],[],[["N"],"N"]]` /
`v4 [0,[],[],[],[["N"],"N","N"]]` / `v5 [0,[],[],[],{}]` / `v6 [0,[],[],[],[{}]]` /
`v7 [0,[],[],["N"],[]]`.
Handler fallback = v0's shape. `ssoStatus` stays LOCKED to the proven fmt#0 body.

### Next action
Launch the game, then read the verdict + walk with `python tools/pl_trace.py`.
v0 is expected to complete the whole walk; whichever variant the run tested is named in
`ORACLE platformLogin vN/8 ...` in `server/logs/round_0924b_stdout.txt`.


## 2026-09-27 20:2x - GIFT: exchange read VERIFIED client-side; capture tooling now sees both sides

### Verified in-game this round (session 20:11-20:21, game exited cleanly)
Client log (`ds2_tls.log`, one pass now that the game is closed) for the session:
- `20:11:07` GET `cloud/execute?ops=GET,GET&docKeys=TomeGift,GameToWeb` -> `CallCloudAsync
  - response good: 1517 bytes, [0,[[0,"<Stats> ..."]]]`
- `20:11:08` GET `...&ops=SET,SET&docKeys=TomeGift,GameToWeb&policy=1,1&worldPerms=2,2`
  -> `response good` (the exchange registration; server keeps it, reads never clobber it)
- `20:11:49` platformLogin -> `AccountPlatformLoginAsync - response good: [0,[],[],[],[]]`
- `20:11:50` ssoStatus -> `response good: [0,[],[],["Player42002"],0,[[["Player42002"],100000002,100000002]]]`
- `20:12:10` POST MAP_SET GameToWeb (2791 chars) -> `response good`
- `20:12:11` getNews limit=32 -> `response good: 6 bytes, [0,[]]`
- `20:13:34` **the exchange access**: GET `...ops=MAP_GET&docKeys=TomeGift`
  -> `CallCloudAsync - response good: 6 bytes, [0,[]]`  (today's TomeGift state)

So the receive path is now honest AND accepted: the client parses our `[0,[]]` for the
gift document without a single "not formatted correctly".

### Client-side verdict tally over the whole (loader) log: 148 entries, 68 sent / 80 recv
`38 CallCloud good, 12 AccountPlatformLogin good, 12 CheckSSOStatus good, 6 GetNews good`;
the 12 failures are ALL historical, newest `2026-09-24 05:48`, and every one is either the
old JSON-object shape (`[{"status":..,"document":..}]`, `{"result":0,"news":[]}`,
`[{"key":..,"value":..}]`) or a pre-fix positional sweep. Nothing after 09-24 failed.
That is independent, client-side confirmation of the positional-body discovery.

### No gift was sent, and no friends were queried
TomeGift requests in the whole client log: the login GET, the SET registration, and the
20:13:34 MAP_GET. No `CallExtension*`, no `requestExtensions`, no `getFriends`, no
Steam friend API line anywhere near the exchange access (only the wire parameter
`friendPerms=` matches "Friend"). Opening the exchange produced exactly ONE request, so
the gift list is fed by the TomeGift document alone - nothing else.

### Tooling (this round's deliverable)
`tools/gift_capture.py` now reads BOTH sides of the wire:
- `--any` lists every request the server logged, any endpoint, + per-endpoint tally;
- `--find <token>` prints the newest matching entry, decoded (searches encoded+decoded);
- `--game-log [PATH]` reads the client's own log instead (default = the same `ds2_tls.log`
  path `tools/correlate_fmt.py` uses), showing `SENT <url>` lines and `RECV <Job>Async -
  response ...` verdicts, so a send that dies before the network is still visible;
- `--doc/--seed/--text/--clear` stay server-log only (refused with `--game-log`).
`tools/test_gift_capture.py` -> 49 checks pass (was 31 at the start of the day).

### Next action (needs the game)
1. Launch; open the exchange; SEND a tome (any recipient). If the recipient picker is
   empty, note it - the friend list is NOT coming from our server (no friend request was
   ever made), so it is the Steam emulator's list and may need populating.

## 2026-09-27 20:5x - the exchange's "connection error": what the logs DO and DO NOT show

Reported: accessing the exchange now fails with a connection error. Session
20:41-20:43 (game quit cleanly at 20:43:17) answers half of it from the logs:

- the cloud read was fine - `20:43:09 GET ...ops=MAP_GET&docKeys=TomeGift` ->
  server `CLbody=6 bytes docs=0 [0,[]]` -> client
  `CallCloudAsync - response good: 6 bytes, [0,[]]` (parse trace walks `[0,[]]`
  to the end, `ARG check want=0x5d ']' got=0x5d ']' @+5/6`);
- that session's full request list is the same seven as the previous one (cloud
  GET,GET / SET,SET -> platformLogin -> ssoStatus -> getNews -> MAP_SET stats ->
  MAP_GET TomeGift), every one `response good`. So NOTHING failed on the wire;
- `cb163 SYNTH: ... m_hAuthTicket=0 vs synth 55 [MISMATCH]` + `NOT setting
  state` at 20:43:10.5 is NOISE, not a failure: the synthesizer fires 1500 ms
  after `GST` even when the real callback already arrived, and it does so for
  handles 82/84/85 in every session, each preceded by `OnAuthToken -
  succeeded`;
- the one genuine oddity: `20:41:12.505 connect -> 127.0.0.1:443` (the GDS
  service, `ds2.gds.thqconnect.com:443` `/gds/rpc`) which our 443 greeting hack
  greets and then CLOSES (`greeting[doc_realhost_tilde_close] ... EOF
  delivery`), the client's socket closing at 20:41:13.999 with a stack that
  ends in script/UI code (`db2b5f`). The script API pool right beside the gift
  callbacks (`0xd7e648 onRetrieveGiftFailure`, `0xd7e660 onRetrieveGiftSuccess`,
  `0xd7e678 onSentGiftFailure`, `0xd7e68c onSentGiftSuccess`, `0xd7ea70
  retrieveGifts`, `0xda4730 onGiftSendAttempt`, `0xda4784
  onDisallowedGiftFailure`) also holds `0xd7e500 getNetworkLinkStatus`,
  `0xd7e518 getNetworkConnectionStatus`, `0xd7e590 isProfileSignedIn`,
  `0xd7e5a4 isProfileOnline`, `0xd7e5e8 getStringForCommunicationStatus` - i.e.
  the UI has an explicit offline/link-status gate, and GDS is down all session.

### Most likely server-side cause, now testable: no document record at all
Our honest answer to a registered-but-never-written docKey is `[0,[]]`
(`docs=0`). On the real service the doc EXISTS from the client's own login
registration (`ops=SET,SET&keys=&values=`), so a read answers with a record
whose document is empty. `doc_content()` treats a blank value as "no document"
(that is what keeps the `{"": ""}` registration junk invisible), so this state
was previously unrepresentable - proved with the new preview:

    python tmp_seed_gift.py empty-str   -> [0,[]]                    (no record!)
    python tmp_seed_gift.py xml-empty   -> [0,[[0,"<TomeGift></TomeGift>",1,t,"seed"]]]
    $env:DS2_EMPTY_DOC='1'  (then)  python tmp_seed_gift.py empty-str
                                        -> [0,[[0,"",1,t,"seed"]]]  walk OK

`server/ds2_server.py` therefore has an OPT-IN switch, `EMPTY_DOC_RECORD`
(`DS2_EMPTY_DOC=1`), which materializes the docKey on any write (the
registration included) and lets `doc_content` return `''` for it, so the read
answers with an empty-document record. Default is unchanged; 5 new checks in
`tools/test_cloud_exchange.py` (37 total) pin both answers, and the empty record
passes `tools/sim_reader.py`.

### Next action (in order)
1. restart the server WITH the switch, then open the exchange:
   `$env:DS2_EMPTY_DOC='1'; python server\ds2_server.py`
   - error gone + empty list -> the UI needs the record; then the grammar from a
     real send is the only thing left;
   - error unchanged -> it is the link-status/GDS gate (see
     `getNetworkLinkStatus` above), not the document;
2. `python tmp_seed_gift.py xml-empty` (no restart) is the fallback candidate;
3. `python tmp_seed_gift.py clear` restores the honest empty store;
4. report the exact on-screen text and WHEN it appears (screen open / pressing a
   button) - that single datum separates the two branches above.

## 2026-09-28 21:47 - VICTORY: the exchange opens and the first gift is captured (grammar found)

What unblocked it: `DS2_EMPTY_DOC=1` (`ds2_server.EMPTY_DOC_RECORD`) - a docKey the
client itself registered exists even with no text, so `MAP_GET TomeGift` answers a
RECORD `[0,[[0,"",policy,time,name]]]` instead of `[0,[]]`. Before it the menu hung
~4 s on "Connection timeout. The Darksiders 2 servers are not available..." while the
client log showed *no* further network or Steam activity at all (ds2_tls.log: nothing
for four seconds after `CallCloudAsync - response good: 6 bytes, [0,[]]`), i.e. the
script was waiting for a document that never came. Checked live before the run (curl
on 8443 and on 80) and in the startup banner (`empty_doc=True`).

THE GRAMMAR, captured verbatim at 21:47:47 (the first gift the game itself ever wrote):

    GET /thqconnect/cloud/execute?profileNames=/steam/76561198000000002
        &ops=MAP_SET&docKeys=TomeGift&keys=5394434090946791192
        &values=Qk9E/QQBAQAeAAAAJgAAAJ4DAAB42nVSW0hUURQ9Z5xR...

i.e. `base64( "BOD" + 16-byte header + zlib(object graph) )`; the header is
`040101001e000000260000009e030000` (version, 30, 38, and the INFLATED size 926).
`BOD` (0xf8ee09) is the THQServer frame magic, the same object graph the base64
decoder 0xb4394c feeds back on read, and the MAP key is the gift NUMBER - `TomeGift`
is a map giftId -> gift, one entry per gift. Inside: `Gift` -> `Items` ->
`weapon_hammer_a01` (+ `Modifiers`/`cp1_`, Attributes, `OverrideRarity`),
`GifterName=playerB`, `Location=$Environment.Z1_MakersVillage.TriStone`, `Number`,
`IsFromPlayer`, `Version`, `UniqueId`.

The gift landed in the RECIPIENT's mailbox - `server/data/cloud/100000001/TomeGift.json`
holds `{"TomeGift":"", "5394434090946791192":"Qk9E..."}` - i.e. the client wrote into
another profile's document, which is exactly what `worldPerms=2` is for. Two new
checks in `tools/test_gift_capture.py` (52 total) keep the capture decodable
(`base64 -> BOD -> zlib -> Gift/Items/GifterName`), and the whole road is written up in
Russian in `VICTORY_RU.md`, with `tools/gift_doc_decode.py` to peel any capture.


2. Read both sides AT ONCE, the game log first (it catches a pre-network failure):
   `python tools/gift_capture.py --game-log --find TomeGift`
   `python tools/gift_capture.py --any`   (then `--find TomeGift` if it did hit the wire)
3. Whatever the send carries (`values=` or `requestExtensions=`) IS the grammar.

