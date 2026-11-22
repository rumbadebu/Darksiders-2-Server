# Darksiders 2 Private Server — JOURNAL (append-only)

Current state lives in **`STATE.md`** (read that FIRST after any context loss).
Append a dated entry at the END of this file after every round. Legacy archives:
`GIFT_PROTOCOL_FALLBACK.md` (protocol/RE reference, sections 1-12), `CONTINUE_HERE.md` (session history to 2026-10-11).

---

## 2026-10-11 — Files restructured; probe breakthrough

**Change:** `PROGRESS.md` replaced by `STATE.md` (current truth, read-first) + this `JOURNAL.md` (append-only).
Legacy files untouched. Motivation: Cline auto-compact loses context; small readable state + append-only log
ends the "re-establish truths every round" loop.

**Probe evidence** (`server/logs/ds2_probe.log`, session 12:39-12:40):
```
ds2_probe_init called
DECODE 0xb4394c patched (resume b43953) stub=656dc000 o=39
DECODE #0 ret=b44fd4 out=d28e3fc in=d28e418 size=20 cap=31 head=15717548710165239937 <- GIFT WALK
```
- The gift walk (0xb44f96 -> 0xb44fd4) DOES fire on MAP_GET and base64-decodes the document KEY string
  (the giftId, 20 chars -> 15 bytes). Fired once — one gift, one entry.
- The login GET,GET blob was never decoded at 0xb4394c (no other DECODE lines in the session).

**Interpretation:** the receive path activates and the walk consumes the key. Open: where the 15 decoded
bytes go (binary ID? lookup key?), and where the blob content is supposed to enter the client.

**Tried & failed — do NOT repeat:** blob in `data` (MAP_GET rejects; trace @+26 wants '[' rejects '{');
blob in `"key"` (AV @0x96d5c giftId comparator); `status=2` (breaks ingest); data=1 vs data=0 (flag not the
render gate); slot4 name alone (no row); 8-way docshape sweep; login pairs/obj/off GET_MAP; `IsFromPlayer`
flip; docslot status/timestamp sweeps.

**Next:** Exp A two-entry document (giftId entry + blob entry, verify with `_rendertest.py` first);
Exp B trace the 15 decoded bytes and find the second decode site.

**Note:** probe log timestamps show month "2026-11-00" (cosmetic date-format bug in probe).

---

## 2026-10-11 15:3x — log rotation implemented, deployed, verified

- **What:** size-based rotation on every log writer — proxy `ds2_tls.log` (new log_write()
  behind tlog!, 4 MB default), probe `ds2_probe.log` (rename at limit, 1 MB), server
  `requests.log` + `trace.log` (`_rotate_if_big`, 4 MB). Env `DS2_LOG_MAX_MB` overrides.
  Rotated files are `<stem>_<n>.log`; each rotation writes a banner line (traceable).
  round_*_stdout.txt rotates per server launch.
- **Why:** ds2_tls.log had reached 447 MB; bare reads of it blew the model context twice.
- **Verified:** `tools/test_log_rotation.py` 6/6; proxy + probe compile clean; deployed
  with hash parity (game == root: proxy E8A1F1EFF80C, probe 87546ADAA864); EAT decoration
  re-confirmed on the deployed proxy (78/78 names; `_BinkOpen@8` present — the earlier
  "decorated=7" reading was shell-output truncation).
- **Caught:** build_rust.bat's deploy step did NOT propagate the new proxy to the game
  dir (hash mismatch) — deployed manually. Rule now in STATE.md: after every proxy build
  verify game-dir hash == root hash.
- **Server:** restarted as round_1023 (pid 23056) with DS2_EMPTY_DOC=1 DS2_DOC_MAP=1
  DS2_TRACE=1 DS2_GET_MAP=1; ports 80/443/8443 all listening.
- **Next test (unchanged):** launch recipient (playerA), open exchange once; read
  `server/logs/ds2_probe.log` (auto-rotating now) for DECODE lines via reframe.py.

---

## 2026-10-11 15:41 — Exp A armed: GET_MAP=obj (store-populator hypothesis)

- Server restarted as **round_1024** (pid 46688) with `DS2_GET_MAP=obj` — the login route
  now serves `{"<giftId>":"<blob>"}` instead of pairs; everything else identical
  (docshape `{"key":"%K%","data":1}`, docslot `0=0 4="playerB"`, trace on). Banner
  verified: `get_map=obj, mapget_map=off`; ports 80/443/8443 listening; probe log cleared
  for clean evidence.
- Hypothesis (from the 12:40 probe session): the walk (0xb44f96 -> 0xb44fd4) is the store
  populator — it fired on the MAP_GET index and decoded the giftId, but never fired on the
  login pairs read. A real map on the login route should make it fire there.
- Oracle: a `DECODE ret=b44fd4` line during the LOGIN read (before the mailbox is opened)
  = store populated; the next MAP_GET index lookup should then render the row.
- Revert if needed: `DS2_GET_MAP='1'` (pairs) + restart.

---

## 2026-10-11 15:44 — Exp A VERDICT: login route never feeds the gift walk (FALSIFIED)

- Config: round_1024, `DS2_GET_MAP=obj` — the login read served
  `{"15717548710165239937":"Qk9E...","<Stats>..."}` (server log 15:43:14, `shape=getmap:obj`,
  CLbody=2455, docs=2). MAP_GET served the index as usual (15:44:14).
- Probe: `DECODE #0 ret=b44fd4 head=15717548710165239937 <- GIFT WALK` at **15:44:14.845** —
  that is the MAP_GET second, 60 s AFTER the login read (15:43:14). One decode, identical to
  the pairs run. The inbox stayed empty.
- **Verdict:** the login GET,GET document never goes through the gift walk in ANY shape
  (pairs AND obj both falsified). The gift blob is never decoded from server-delivered
  content on the login route; the walk fires only on MAP_GET, always decoding the giftId.
- Consequence: the login route cannot be the store source. The open unknown is now exactly
  one thing: what the walk does with the 15 decoded bytes after the MAP_GET decode
  (insert vs lookup, and the key used).
- Next (Exp B): one more probe hook at the walk's continuation 0xb44fd4 — log the next
  call/compare after the decode returns. Probe-only change; server login shape irrelevant.

---

## 2026-10-11 16:0x — Exp B hooks built, deployed, armed

- Two call-emulating hooks in `ds2_probe.dll` (hash **D8BE53943D03**, deployed to the game
  dir, parity verified):
  - **WALK48** @0xb44fe1 (`call 0x48c56b`): logs the gift object + the DECODED string
    (size/cap/head) the walk assigns into it.
  - **WALKAEF** @0xb44ffc (`call 0xaef713`): logs the key/value/flag the walk pushes into
    the internal store pair.
- Each replaces the 5-byte `call rel32` with a jmp to a stub that logs (VirtualQuery-
  guarded) then emulates the call exactly (push original return address, jmp target) —
  no game-state change. Caught + fixed a self-inflicted stack bug mid-build (the 3-arg
  shape pushed `arg` twice).
- Walk disassembly on record: decode @0xb44fcf -> 0x48c56b (assign decoded into gift
  object, this=[edi+8]) -> 0xaef713 (pair construct from entry key/value) -> loop
  (stride 0x30, head 0xb44fa5).
- Next: launch recipient (playerA), open the exchange once, read the probe log for
  WALK48/WALKAEF lines.

---

## 2026-10-11 16:2x — delayed freeze post-mortem: shared stub buffer; FIXED

- Symptom: inbox access froze, then crashed — delayed, not instant.
- Root cause: `static mut WSTUB` inside `patch_walk_call` is shared across invocations.
  The second patch (WALKAEF @0xb44ffc) overwrote the first hook's stub, so the WALK48
  site (0xb44fe1) executed WALKAEF's code: called `0xaef713` with WALK48's stack and the
  WRONG return address (0xb45001) → shifted stack → delayed corruption → freeze.
- Diagnostic signature: a **delayed** crash = wrong-target call with plausible args
  (it runs for a moment first); an **instant** crash would mean a bad deref inside the
  logger. The freeze also proves both patch sites are hit — the mechanism works.
- Fix: per-hook module-level stub buffers (`WSTUB48`/`WSTUBAEF`) passed by pointer.
- Deployed: probe **3D11CC8F72EA** (parity verified), probe log cleared.
- Next: launch recipient, open the exchange, read the WALK48/WALKAEF lines.

---

## 2026-10-11 19:3x — REAL root cause of the delayed crash: stub jumped to the call SITE (infinite recursion); FIXED

- The 19:34 session proved the "shared stub buffer" theory **wrong** — with per-hook buffers
  the freeze repeated identically.
- Log evidence: WALK48 fired **200+ times in 46 ms**; from #1 on `decoded=` showed
  `b44fe6` = the hook's **own pushed return address** (stack shifted each iteration);
  WALKAEF **never fired**.
- Root cause: the call-emulating stub's final `jmp target` jumped to the **call site**
  (0xb44fe1) instead of the **callee**. Since 0xb44fe1 now holds `jmp stub`, the stub
  re-entered itself: infinite recursion, +4 stack bytes per iteration → stack overflow →
  delayed crash. The logger's 200-cap hid the loop's true length.
- Fix: read the call's `rel32` (`call rel32` at target → callee = target+5+rel32) and jmp
  to the **callee**. Deployed: probe **0A88C05C541A** (parity verified), log cleared.
- Lesson (also written into the code): in a call-emulating hook the final jmp MUST target
  the callee, never the patched instruction. Fingerprint of this bug: one hook looping
  with its own return address as the "argument" while the paired hook never fires.
- Next: launch recipient, open the exchange, read the WALK48/WALKAEF lines.

---

## 2026-10-11 19:43 — Exp B stable run: walk mechanics OBSERVED (the receive path)

- First stable session (the jmp-to-callee fix held): inbox opens, no crash, hooks fire once.
- Probe (one-shot): `DECODE` giftId -> 15 bytes; `WALK48 0x48c56b` assigns the decoded
  string into the gift object; `WALKAEF 0xaef713 key='key' val=<binary> flag=1` builds the
  store pair.
- **Conclusion: the walk decodes each document member's VALUE and stores
  {member-name: decoded-value}.** With the index shape the store gets
  `{"key": <decoded giftId>}` — meaningless -> empty inbox. The gift payload IS the value:
  serving `[{"<giftId>":"<blob>"}]` makes the walk decode the blob to the BOD binary and
  store it under the numeric gift id (also explains the 0x96d5c AV: the pair key "key" is
  non-numeric).
- Armed: docshape MAP_GET = `[%E%] => {"%K%":"%V%"}` (live, no restart needed).
- Cosmetic probe fix pending deploy (`this=` read offset [esp+0x18] -> [esp+0x1c]; pushfd
  sits at [esp+0]): build **524C9BF50E35**, deploy when the game closes. Deployed probe is
  still 0A88C05C541A (works fine; only the logged `this` value is wrong).
- Next: launch recipient, open the exchange; check the probe log for `decoded=<blob>`.

---

## 2026-10-11 20:13 — Fix A VERDICT: the shape is RIGHT; crash localized to one consumer call

- **`[{"giftId":"blob","data":1}]` PARSES and the walk works end-to-end** (probe):
  `DECODE size=828 head=Qk9E…` (blob) -> `WALK48 size=621 head=BOD…` (BOD assigned into
  the gift object) -> `WALKAEF key='15717548710165239937'` — **the store gets
  {realGiftId: BOD} exactly as designed.** Member names are POSITIONAL (any first name
  works as long as `data` is present — the earlier single-member failure was the missing
  `data`).
- **Crash after the walk:** WER `Fault offset 0x00096d5c` = **RVA** -> VA `0x496d5c` =
  `cmp dword [edi],eax` inside a string helper — `this` (esi) valid, **the ARG pointer
  (edi=[ebp+8]) is garbage**. Same RVA as the old blob-in-key crash.
- **Stored blob verified intact:** 828 b64 -> 621 raw, `BOD` magic, zlib @20 -> **724-byte
  payload** containing `Gift/Items/armor_gloves_wanderer_d01`. The data is fine; the
  consumer misreads something around it.
- **New STRBAD hook @0x496d4d (function entry, 6-byte prologue):** logs only calls whose
  arg is unreadable, with the ORIGINAL return address = THE CALLER. The log fires before
  the crash, so one launch names the exact consumer to fix. Probe **9FCF9B2F8265**.
- Next: launch recipient, open the exchange, read `STRBAD #… ret=0x…` from the probe log.

---

## 2026-10-13 13:11 — crash confirmed at the string helper; STRBAD widened to catch it

- Fix A re-run confirmed STABLE walk output: `WALK48 this=2ff65838` (a REAL pointer — the
  pushad-offset fix worked), `WALKAEF key='15717548710165239937'`, then the crash at
  13:11:12 — WER `Fault offset 0x96d5c` (RVA) = VA `0x496d5c`, same instruction, same
  millisecond as the walk finishing.
- **STRBAD did not fire** — the crash call passed a *readable* argument, so the filter
  skipped it. Hypothesis: the walk's post-loop `call [eax+0x50]` (ret=0xb45049) IS this
  string helper — the call passes `this` (ecx) but no second argument, so `[ebp+8]` reads
  stack garbage that happens to be readable.
- STRBAD now ALSO logs ret=0xb45049 calls (probe **DF5757C42943**). The next launch will
  show either `ret=b45049 (walk-vcall)` (the virtual call is the culprit) or
  `ret=<other> <- BAD PTR` (another consumer passes garbage).

---

## 2026-10-13 13:46 — STRBAD CAUGHT THE CALLER: ret=0x4b2c46; vtable hook armed

- STRBAD fired (after fixing my own cap bug — the counter incremented on all ~14,880
  calls and exhausted before the crash call): `STRBAD #0 this=19ee64 arg=61ba5f20
  ret=4b2c46 <- BAD PTR`.
- The caller: function entry **0x4b2ad3** (a thiscall method: `mov ebx,[ebp+8]` — it
  REQUIRES a stack argument; internally calls 0x4b2646 lookup, then at 0x4b2c41
  `mov ecx,ebx; push eax; call 0x496d4d`). The crash passed garbage this AND a
  non-stack arg — so the method was entered via an INDIRECT call without its argument.
- **No static vtable holds 0x4b2ad3** (dword scan: 0 hits) → the gift object's vtable is
  runtime-constructed (script binding).
- **New VCALL hook @0xb45042** (the walk's post-loop `mov eax,[ecx]; call [eax+0x50]`,
  5 bytes): logs (object, vtable, method) before the call executes. Probe
  **6333CE9AFF00**. The VCALL line will show exactly where the virtual call goes and
  whether the vtable is garbage or a real script-object vtable.

## 2026-10-13 15:45 - VCALL resolves to a member-pointer thunk (obs_method named)

VERDICT: the gift notification consumer is one more hop away; next hook names it.

- Full crash chain captured in ONE clean probe run (13:11 session):
  - DECODE #0 ret=b44fd4 size=828 head=Qk9E/...          <- GIFT WALK (blob decoded)
  - WALK48 #0 this=2ff65838 decoded=d05e3fc size=621 head=BOD...
  - WALKAEF #0 key='15717548710165239937' val='(empty)' flag=1
  - VCALL #0 obj=102dc478 vtable=12e85c0 method=b430bf
              obs=2ffa8050 obs_vtable=12e8740 obs_method=498250 ret=b45047
  - STRBAD #0 this=19ee64 arg=61c78a38 ret=4b2c46 <- BAD PTR
  - WER: Darksiders2.exe fault offset 0x00096d5c (= VA 0x496d5c, string helper)
- Disassembly of obs_method 0x498250 proves it is a MEMBER-POINTER THUNK:
      0x498250  mov eax, ecx        ; this = observer
      0x498252  mov ecx, [eax+8]    ; -> real target OBJECT (new this)
      0x498255  test ecx,ecx / je ret
      0x498259  mov eax, [eax+0xc]  ; -> real target METHOD (runtime pointer)
      0x498260  jmp eax             ; tail-call, no args
  So the crash is NOT in the thunk; it is in obs->field_c, a runtime-bound
  listener never previously identified.
- IMPORTANT re-frame: the 188 ms gap VCALL(44.806) -> STRBAD(44.994) is consistent
  with the notification REACHING THE UI and the UI then failing to RENDER the gift,
  not failing during ingest. Ingest is fully proven working.
- FIX (deployed): VCALL hook now also reads [obs+8] and [obs+0xc], logging
  thunk_obj=/thunk_fn=. thunk_fn is the real listener -> disassemble it and the
  gift fields it reads are the final docshape fix.
- Probe 883C9E771D23 deployed; markers thunk_obj/thunk_fn VERIFIED in the game-dir
  binary; probe log cleared.
- DEPLOY TRAP (2nd occurrence): Copy-Item reported success while the game file kept
  the OLD timestamp (14:54 vs build 15:38) and lacked the new markers. ALWAYS verify
  by marker string + LastWriteTime - never by hash or exit code alone.



## 2026-10-13 15:55 - STOPPED the thunk chase; VEH installed (catches the AV at any depth)

STRATEGY CHANGE - and why.  The notification consumer turned out to be a chain of
thunks, each costing a game launch + crash:
    b430bf (walk vtable+0x50) -> obs vtable[1]
    0x498250 -> jmp [obs+0xc]            (member-pointer thunk)
    0x4d00de -> jmp 0x5d4498             (call 40591a / 4df2db / 4cfb93 then tail-jmp)
    0x5d4498 -> call 0x5d3757            (7-byte thunk, push 0)
This is the game's script event dispatch.  Following it by hand is unbounded.

KEY EVIDENCE that redirected the effort: the fault offset MOVED between runs -
0x96d5c (string helper, STRBAD logged) then 0x1035 (a LOW address inside the image,
i.e. an indirect call through a bad pointer, STRBAD never logged).  Different crash
sites on the same input = the code is walking different paths, i.e. our gift IS
being processed into UI code.  Combined with the 188 ms VCALL->AV gap this supports:
INGEST WORKS (decode -> BOD -> store under real gift id, all proven), the crash is
in RENDERING a gift whose fields the consumer expects.

FIX: vectored exception handler in the probe.  One run now yields the whole answer
regardless of chain depth:
    *** AV #0 addr=0x... (RVA 0x...) bad_addr=0x... eax=.. ecx=.. edx=.. ebx=.. esi=.. edi=.. ebp=.. esp=..
    *** AV #0 code_bytes=<16 bytes at the faulting instruction> code_ok=<bool>
    *** AV #0  <reg>=<addr> -> <16 bytes there>     (for each readable pointer reg)
- first=1 (first in the VEH chain), AV-only, capped at 4, returns
  EXCEPTION_CONTINUE_SEARCH so the game still crashes normally and WER still records.
- CONTEXT x86 offsets used: Edi 0x9c, Esi 0xa0, Ebx 0xa4, Edx 0xa8, Ecx 0xac,
  Eax 0xb0, Ebp 0xb4, Eip 0xb8, Esp 0xc4 (FloatSave = 0x70 bytes).
  EXCEPTION_RECORD: ExceptionAddress 0x0c, ExceptionInformation[] 0x14 -> [1] = bad VA.

BUGS HIT WHILE BUILDING (both caught by the marker check, which is why the deploy
trap rule exists):
1. `AtomicUsize` not in scope - the probe imports AtomicU32 only; use that.
2. My insert displaced `#[no_mangle]` from `ds2_probe_init` (attribute ended up 70
   lines above the fn, separated by real items) -> `LNK2001 unresolved external
   symbol ds2_probe_init`.  Rust does NOT allow an attribute separated from its item
   by other items, only by comments.  When inserting a block ABOVE an exported fn,
   always move the attribute with it.

DEPLOYED: probe 196608 B, 15:55:19, markers code_bytes + "VEH registered" VERIFIED in
the game-dir binary; probe log cleared.



## 2026-10-13 16:20 - VEH PAID OFF: crash named exactly; hash theory REFUTED

The vectored handler worked on its first real crash and did in one run what four
rounds of thunk-chasing could not.

=== THE AV (16:04:11, crash on EXITING the inbox) ===
    *** AV #0 addr=0x65b92d (RVA 0x25b92d) bad_addr=0xdad7af8b
        eax=dad7af4b ecx=2f330d40 edx=1d231617 ebx=0 esi=12b70fc0
        edi=12ba20e0 ebp=19ee04 esp=19eddc
    *** AV #0 code_bytes=ff6040558bec8b4930d945088b0151d9  code_ok=true
    *** AV #0  ecx=2f330d40 -> 4b af d7 da 33 9a 1f a5 08 00 55 6e 69 71 75 65
    *** AV #0  esi=12b70fc0 -> 68 bc 20 01 04 00 00 00 a0 7a 58 17 00 09 3a 13
    *** AV #0  edi=12ba20e0 -> c0 b2 1d 01 02 00 00 00 48 81 ed 12 00 09 3a 13

READING IT:
- code_bytes starts `ff 60 40` = `jmp dword ptr [eax+0x40]`  <- the faulting insn.
- bad_addr 0xdad7af8b = eax + 0x40, confirming it is that jump.
- eax = 0xdad7af4b, and ecx -> `4b af d7 da | 33 9a 1f a5 | 08 00 | "Unique"`.
  Read little-endian that is the 8-byte hash 0xa51f9a33dad7af4b followed by
  u16 length 8 and the text "UniqueId".  So eax is the LOW HALF OF THE STRING HASH,
  i.e. the code did `mov eax,[ecx]` then `jmp [eax+0x40]`: it dereferenced raw BOD
  string bytes where it expected an object pointer.  Classic type confusion -
  a BOD STRING record handed to a consumer expecting an OBJECT.

=== HASH THEORY TESTED AND REFUTED (tools/_hashcmp.py) ===
Hypothesis was: the 8-byte prefix is a per-process-seeded hash, so a blob written by
the sender could never be resolved by the recipient.
RESULT: the hashes are IDENTICAL in every blob, across both accounts and all backups:
    Gift 73b849c1206a83b3   Items 8b5b7457fe236c3d   GifterName 647e850169e20c8a
    Location 7213b0ede1bd509d   UniqueId a51f9a33dad7af4b   (7 blobs checked)
=> DETERMINISTIC and portable. No per-process seed. That theory is dead.
(tools/_hashtest.py: none of fnv1a64 / fnv1_64 / djb2 / sdbm / crc64xz reproduces it,
 so it is an engine-specific hash - not needed though, since it is stable.)

=== WHAT ELSE THIS SESSION ESTABLISHED ===
- Ingest still fully working and now STABLE: on inbox OPEN the walk fires, decodes the
  blob (828 B), builds the BOD (621 B), stores it, notifies - and does NOT crash.
- The crash is DOWNSTREAM (on inbox exit) and intermittent by path.
- User observation (important): there was NO notification this run. Notification is
  stored IN THE SAVE and appears when the save is restored - it is save-driven, not
  server-driven. Consistent with the earlier finding that the inbox renders from the
  save, not from the live cloud store.
- The gift was REMOVED from server/data/cloud/100000001 during the session (only the
  `TomeGift` sentinel remains), so the collect/placeholder-remove path now deletes.
  Restore from cloud_backup_20261007 if it is needed again.
- `WALKAEF val='(empty)'` is a HOOK ARTIFACT, not evidence: peek_cstr() is applied to
  what is really a std::string object, so it reads size/capacity bytes. Do not read it
  as "the stored value was empty".

=== NEXT ===
The remaining question is WHICH BOD field is a string where the consumer wants an
object. Dump the BOD with its TYPE TAGS (the byte before each [8-byte hash][u16
len][text] - e.g. `0f 01` seen earlier) and compare the tag sequence against the
field names, looking for the slot where the parser hands out a string as an object.
Tooling already exists: tools/gift_doc_decode.py (--strings) + tools/_hashcmp.py.


## 2026-10-14 — RECEIVE SOLVED: gifts render with correct attribution (both directions)

**RESULT: the receive path works.** Sender name is correct (from blob `GifterName`,
per-gift not per-login — the old "gift from yourself" bug is gone). Full cycle
send -> login -> inbox render -> collect verified in BOTH directions.

### BOD type-tag dump (the 2026-10-13 NEXT) — done offline with _bodtags.py
Decoded every gift and tagged each field's value type. Armor gifts (gloves, boots)
and weapon gifts (axe, hammer) both carry the same envelope; the only cross-item
difference is the item-name field's own tag (armor=04, weapon=03) plus weapon-only
fields (Type/Damage/DamageRange). NO field is a string-where-object-is-expected in
any FRESH blob — the old type-confusion theory did NOT reproduce on current data.

### The 10-13 crash was the OLD axe blob specifically, refuted as "count" and as "00 vtag"
- unjournaled 10-14 17:44 run (empty TomeGift) was a DIFFERENT bug: `lock xadd`
  (InterlockedDecrement) on a freed ptr in UI teardown — a refcount UAF, NOT the
  receive blocker. The game-dir ds2_probe.dll had also been reverted to a STALE
  no-VEH build (183296 B); re-deployed the 196608 B VEH build (markers verified).
- Isolation (backup cloud_backup_20261014_214017): dropped the OLD axe
  (15141810279299964802, sent 2026-10-05, pre-fix) leaving only the fresh boots ->
  boots rendered, NO crash. So it was the SPECIFIC old blob.
- Then playerA received hammer+gloves (2 gifts, one a weapon), and separately a
  fresh axe+hammer envelope -> ALL rendered fine, correct attribution, NO crash.
  => COUNT is not the trigger, the `00` vtag is not the trigger (fresh weapons have
  it too and render). The culprit was the stale pre-fix axe blob only.

### What made it work (the actual fix, already in place)
- Re-seeded the recipient gift into server/data/cloud/100000001 (blob had been
  emptied during 10-13 testing; _reseed_gift.py restores from the preifp snapshot).
- Armed docshape `[%E%] => {"%K%":"%V%","data":1}` + docslot `0=0 4="playerB"`
  (sender). Probe: walk fires -> base64-decodes blob -> BOD -> stores under the REAL
  gift id -> VCALL notification -> row renders. collect sends the real gift id via
  MAP_REMOVE and the mailbox clears.
- No server restart needed for any of it: _load_doc re-reads the file per request.

### New tools (root)
- _bodtags.py / _vtag.py: BOD frame -> inflate -> per-field value-type-tag dump.
- _reseed_gift.py: restore the recipient's gift blob from a snapshot.
- _drop_gift.py (+ --restore): remove one gift for count-isolation.
- _count_research.py: what render_doc serves for 1 vs N gifts + field diff.

### NEXT (optional)
- Root-cause the OLD axe blob's defect for completeness (what its stale format got
  wrong) — low priority, it is pre-fix data that no longer occurs.
- Rebuild probe/log rotation untouched; both mailboxes now empty & healthy.


