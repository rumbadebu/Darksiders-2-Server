# Darksiders 2 — Tome Gift (mail) protocol: FALLBACK REFERENCE

Standalone capture for resuming work with no live server/context. Everything
below is copied from **real, verified wire sessions** (`round_1016`,
2026-10-09 18:50–19:17): a clean-slate send of one gift ("gloves") from
`playerB` to `playerA, plus the receiver-side open (no gift shown) and a
reverse send of "axe" from `playerA` to `playerB`, plus the server-side
decode of the blobs. Treat this as the ground truth for the send/receive path.
Sections 1–8 cover the send + blob format; **section 9 covers the receiver-side
test (gift delivered but not displayed)** — the current focus.

If the live investigation stalls, this document is enough to rebuild the gift
flow from scratch.

---

## 1. Accounts / steamids / docs

| display name | account id (last 32 bits) | steamid64             | folder (`My Games\Darksiders2\`) |
|--------------|---------------------------|-----------------------|----------------------------------|
| playerA    | **100000001**             | 76561198000000002     | `100000001/dssave0.dsav`         |
| playerB  | **100000002**              | 76561198000000001     | `100000002/dssave0.dsav`          |

`account = steamid64 & 0xFFFFFFFF`. The server derives `PlayerNNNNN` from it:
`Player12554` = 100000001 = playerA; `Player42002` = 100000002 = playerB.

**Cloud doc store**: `server/data/cloud/<account>/TomeGift.json` (and
`GameToWeb.json`). Each `TomeGift.json` is a map:

```json
{
  "lastModifiedName": "<name>",
  "values": {
    "TomeGift": "",                     // docKey sentinel (kept even when empty)
    "<giftId>": "Qk9E...",              // base64 BOD blob, one per received gift
    ...
  }
}
```

**Mailbox semantics (confirmed):** a player's `TomeGift.json` holds the gifts
**they RECEIVED** (the sender writes into the recipient's doc). Sending is a
`MAP_SET` addressed by `profileNames=<recipient steamid>` with `keys=<giftId>`,
`values=<base64 blob>`.

After the 18:58 gloves send the store was:

```
doc 100000001 (playerA)   lastModifiedName='playerB'  gifts=['15717548710165239937']   <-- RECEIVED
doc 100000002 (playerB)  lastModifiedName='playerA'    gifts=[]                          <-- empty
```

So the send worked: the gloves gift landed in the recipient's doc with
`GifterName='playerB'`.

---

## 2. Cloud request/response envelope

Endpoint: `GET /thqconnect/cloud/execute?<params>` (also POST for body ops).

Common query params: `profileNames`, `ops`, `docKeys`, `keys`, `values`,
`policy`, `worldPerms`, `friendPerms`, `version=1`, `platform=PC_STEAM`,
`title=Darksiders2`, `language=English`, `auth=<hex blob>`.

**Response envelope** (array form the game parses):

```
[ <status array> ]
[ 0, [ [ <status>, <document>, <data?>, <lastModifiedTime>, <lastModifiedName> ], ... ] ]
```

Concrete read response (empty inbox doc, `MAP_GET`):

```
[0,[[0,"",0,1791559563,"playerA"]]]
        ^  ^ ^ ^         ^
        |  | | |         +-- lastModifiedName (the string the inbox may show)
        |  | | +------------ lastModifiedTime (unix)
        |  | +-------------- data field (0 here)
        |  +---------------- document text ("" = empty inbox)
        +------------------- per-doc status (0 = OK)
```

Concrete write response (the gloves `MAP_SET`):

```
[0,[[0,"[{\"key\":\"15717548710165239937\",\"data\":0}]",0,1791561501,"playerB"]]]
```

Note the **document echoed back is the index shape** `[{"key":"<giftId>","data":0}]`
— that's `render_doc` with the `[%E%] => {"key":"%K%","data":0}` template. The
game acknowledges the key; the *received-gift body* it actually parses comes
from the plain GET read (§5), not from this write echo.

---

## 3. The SEND — full captured wire trace (gloves)

**Request** (`MAP_SET`, 18:58:21) — sent by the currently-logged-in sender
(playerB, `profileNames` = recipient playerA):

```
GET /thqconnect/cloud/execute?
    profileNames=%2fsteam%2f76561198000000002     <-- /steam/<playerA> = RECIPIENT
    &ops=MAP_SET
    &docKeys=TomeGift
    &keys=15717548710165239937                      <-- the giftId (18-digit)
    &values=Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3Nycp...<-- base64 BOD blob (see §4)
    &policy=0&worldPerms=-1&friendPerms=-1
    &version=1&platform=PC_STEAM&title=Darksiders2&language=English
    &auth=14000000286b926e18581d41f24ca5040100100... HTTP/1.1
Host: thqc-darksiders2-pc.thqconnect.com:8443
Accept: */*
Pragma: no-cache
Cache-Control: no-store
```

Server-side: `cloud op=MAP_SET actor=Player12554(100000001)` then
`gift attribution: recipient=Player12554 sender='playerB' (from blob)` —
i.e. the server already parses the sender name out of the decoded blob.

**Response**: `[0,[[0,"[{\"key\":\"15717548710165239937\",\"data\":0}]",0,1791561501,"playerB"]]]`

Preceding login chatter (same session, for context):
- `GET,GET` (TomeGift+GameToWeb) → 2 docs, first `""` (inbox), second `<Stats>…` XML.
- `MAP_SET GameToWeb` (`keys=GameToWeb`, `values=<Stats>…`) → player stats save.
- `MAP_GET TomeGift` (`keys=&values=`, `data` 0 or 1) → the inbox index read.
- `<- 200 [0,[],[],[],[]]` / `[0,[],[],["Player42002"],0,[[["Player42002"],100000002,100000002]]]` = presence/roster calls.

---

## 4. The gift blob — full format (REVERSED & VERIFIED)

`values` = **base64 of a BOD container**. Decode -> inflate -> gift object.

### 4a. BOD container (base64-decoded bytes)

```
offset  bytes                 meaning
0..2    42 4f 44              ASCII "BOD" (magic)
3       fd                    container tag
4       04                    version
5..8    01 01 00 1a           u32 = 0x1a000101 - flags (constant)
9..12   26 00 00 00           u32 = 0x26 = 38 - header/compressed-block size hint
13..16  00 00 00 00           padding / reserved (0)
16..19  d4 02 00 00           u32 = 724 = INFLATED payload length  <-- the length field
20..    78 da ...             zlib stream (gloves used 78 da = best compression)
```

Concrete header (gloves blob, verified byte-for-byte):

```
@ 0  42 4f 44 fd     "BOD" + 0xfd
@ 4  04 01 01 00     version 4, flags
@ 8  1a 00 00 00     26
@12  26 00 00 00     38
@16  d4 02 00 00     724  <-- INFLATED length (zlib output)
@20  78 da 63 64     zlib stream begins (78 da)
```

To inflate: find the zlib magic (`78 9c` / `78 da` / `78 01`) scanning from
offset 3, then `zlib.decompress(rest)`. Result length == the u32 at offset 16
(724 here). When rewriting the blob, keep that u32 at **offset 16** in sync with
the new inflated length.

### 4b. Inflated gift payload - printable strings (gloves, in order)

```
Gift
Items
  armor_gloves_wanderer_d01          <-- the attachment item id
StatsTableName
  base/mod_armor_gloves              <-- item stats table
Modifiers
  ItemModifier / ModifierLabel / $Item.Attributes.Defense
  AttributeModifiers / AttributeModifier / Attribute / Defense / AttributeID
  ValueTable / ValueLookup / Visited / cp1_ / OverrideRarity
Number
IsFromPlayer
  GifterName
    playerB                      <-- THE SENDER NAME (one per mail, not per item)
  Location
    $Environment.Z1_MakersVillage.TriStone
Version
UniqueId
```

**Serialization of strings** (why name rewriting works): each value string is
stored as `[8-byte hash][u16 LE length][utf8 bytes]`. `GifterName` is a *value*
string (the literal `GifterName` is its key). The 8-byte hash is the engine's
`SStringHash` intern key - **NOT** a standard fnv/crc/djb2 (all tested, none
match). To rewrite the name safely: swap the bytes, fix the u16 length, and set
a **fresh random 8-byte hash** (reusing the old hash returns the cached old
string from the interning table). Then bump the BOD length u32 (section 4a).

`tmp_setgifter.py --name <NAME>` does exactly this with in-memory round-trip
verification; `tmp_verifyrewrite.py` diffs payloads; `tmp_blobdump.py` shows
strings; `tmp_wirecheck.py` runs it through `_load_doc + doc_map_json`.



### 4c. Complete stored gloves blob (verbatim fixture)

The exact `values` string stored in
`server/data/cloud/100000001/TomeGift.json` under key `15717548710165239937`
(828 base64 chars -> 621 BOD bytes -> 724 inflated). Reproduce a known-good
gift offline:

```
Qk9E/QQBAQAaAAAAJgAAANQCAAB4<...truncated: full gift blob removed...>
```

Decode helper (used everywhere in the tools):

```python
import base64, zlib
def bod_inflate(b64):
    r = base64.b64decode(b64 + "=" * (-len(b64) % 4))
    st = min([x for m in (b"x\x9c", b"x\xda", b"x\x01")
              if (x := r.find(m, 3)) != -1] or [-1])
    return zlib.decompress(r[st:])          # len must equal u32 @ offset 16
```

---

## 5. The RECEIVE / ingest path (exe RE)

The client ingests a received gift during the **login `GET,GET` read** (not on
demand). The exe chain, all addresses in `Darksiders2.exe`:

| step | address | role |
|------|---------|------|
| JSON doc parse (dispatch) | `0xb44e65` | switch on document kind; case 4 = map entries |
| entry walk | `0xb44f96` | iterates parsed entries, stride `0x30` = `{key {ptr,size}, value char* @+0x10}` |
| base64 decoder | `0xb4394c` | decodes each entry VALUE (its ONLY call site is `0xb44fcf`) |
| gift event | `onGiftReceived` (xref `0x722ee3`) | -> script `GiftComponent.addGift` |

**The walk eats ONLY a parsed map of `<giftId> -> base64 blob`.** A bare value
(`Qk9E...` alone) yields no entries, so the walk never runs and the gift is
silently accepted but never ingested (`response good` proves only that the
*envelope* parsed, not that ingestion happened).

Script chain (`media/scripts.obsp`): UI `onGiftRetrieveAttempt` ->
`Game.retrieveGifts` (exe `0xd7ea70` -> thunk `0x4e66e0`) ->
`onRetrieveGiftSuccess`; inbox name renders from `gift.GifterName` via
`base/additemtotome` (`GifterName -> TomeGifterName`).



## 6. Server levers (env flags, `server/ds2_server.py`)

| env var | default | effect |
|---------|---------|--------|
| `DS2_EMPTY_DOC` | off | materialize the docKey sentinel so an empty inbox answers with a record |
| `DS2_DOC_MAP` | off | render `MAP_GET` (index) docs through the `doc_shape()` template |
| `DS2_GET_MAP` | off | **the receive fix**: plain `GET` serves `doc_map_json()` instead of the bare value |
| `DS2_COLLECT` | off | debug tool: fully clear a mail (game normally keeps mail, clears only attachments) |
| `DS2_TRACE` | off | write `server/logs/trace.log` |

`DS2_GET_MAP` shape selection:
- `1` or `pairs` (default when on) -> `[["<giftId>","Qk9E...",...],...]`
- `obj` -> `{"<giftId>":"Qk9E...",...}` (served in-game 10-09, envelope OK, NO save entry -> obj is a known-dead candidate)
- unset -> off (bare value, pre-split behaviour)

Key functions: `doc_content()` (bare value), `doc_map_json()` (the map, see §5),
`render_doc(values, spec)` (index template; the `[%E%] => {"key":"%K%","data":0}`
alias), `requested_doc_keys()`, `param_slot()`.

---

## 7. Verified facts & OPEN problem (why this doc exists)

**Confirmed working (round_1016, clean slate):**
- Server store cleared (`tmp_clear_gifts.py`), then ONE gift (gloves) sent
  `playerB -> playerA` at 18:58:21.
- Send landed correctly: `doc 100000001` (playerA) now holds
  `15717548710165239937`, `lastModifiedName='playerB'`, blob
  `GifterName='playerB'`, item `armor_gloves_wanderer_d01`.
- Server parses sender from blob: `gift attribution: recipient=Player12554
  sender='playerB' (from blob)`.
- Serves the gift on the receive read in the `pairs` shape.

**OPEN - attribution / which gifts the inbox shows:**
- A clean restore + launch (sending nothing) still showed the player's OWN sent
  gifts (axe+hammer) under their own name. Those live in the SENDER's outbox
  doc, not the recipient's inbox. Hypothesis: the inbox renders the save's local
  SENT list merged with the receive doc, or the `GET,GET` read touches the wrong
  doc. **NOT yet root-caused.**
- Rewriting the blob `GifterName` to a sentinel (`SERVERMAIL`) did NOT change
  what the inbox displayed -> the displayed name is likely a SAVE-side field,
  not the blob's `GifterName`.

**Design (confirmed with user):** mail is per-sender (one `GifterName` per mail
row); items in a mail have NO attribution (just an attachment bag). Collecting
clears attachments but keeps the row. `DS2_COLLECT` full-clear is a debug tool.

---

## 8. Tool inventory (repo root / tools/)

| file | purpose |
|------|---------|
| `tmp_setgifter.py` | rewrite `GifterName` in stored blobs (fresh hash + len + BOD len fixup), `--dry`/`--name` |
| `tmp_verifyrewrite.py` | diff payload before/after a rewrite (only the name should change) |
| `tmp_blobdump.py` | decode + list printable strings per gift |
| `tmp_wirecheck.py` | run a rewrite through `_load_doc + doc_map_json` (what the wire serves) |
| `tmp_giftaudit.py` | compare save gift records vs. server mailbox docs |
| `tmp_clear_gifts.py` | `backup` / `clear` / `show` the gift store |
| `tmp_savecmp.py`, `tmp_recvsave.py`, `tmp_names.py`, `tmp_gifter.py` | save-side forensics |
| `tools/disasm.py`, `tools/find_calls.py`, `tools/find_xrefs.py` | exe RE helpers |
| `tools/test_cloud_exchange.py`, `test_gift_capture.py`, `test_login_flow.py` | offline suites |
| `tmp_route_verify.py` | live route/shape check against a running server |

**Server runbook:** launch with `DS2_EMPTY_DOC=1 DS2_DOC_MAP=1 DS2_TRACE=1
DS2_GET_MAP=1`, then watch `server/logs/round_<N>_stdout.txt` and `trace.log`.
Account = `steamid64 & 0xFFFFFFFF`. Restore a clean save before each test;
the store can be reset with `tmp_clear_gifts.py backup` then `clear`.


---

## 9. Session 2 (2026-10-09 ~19:13) — RECEIVER sees NO gift, sender clear

Second hands-on test, same clean store, `round_1016`, same env levers.

**Timeline (chronological, from `round_1016_stdout.txt`):**

| time | actor | op | note |
|------|-------|----|------|
| 18:58:21 | playerB | `MAP_SET` | sends **gloves** `15717548710165239937` -> playerA; `gift attribution: recipient=Player12554 sender='playerB'` |
| 19:13:20 | **playerA** | `GET,GET` | **login read** served `shape=getmap:pairs`, gift id + gloves blob present in body |
| 19:13:51 | playerA | `MAP_SET` | writes own `GameToWeb` stats |
| 19:14:10 | playerA | `MAP_GET` | inbox index read -> `[{"key":"15717548710165239937","data":0}]` (gloves visible in index) |
| 19:14:11 | playerA | `MAP_REMOVE` | `keys=key` (placeholder), `values=` — **client clears the placeholder key** |
| 19:15:29 | playerA | `GET,GET` | login read again, still `getmap:pairs`, gloves served |
| 19:17:14 | playerA | `MAP_GET` | index -> `{"key":"15717548710165239937","data":1}` (data flag flipped 0->1) |
| 19:17:15 | playerA | `MAP_REMOVE` | `keys=key` again |
| 19:17:36 | playerA | `MAP_SET` | sends **axe** `15141810279299964802` -> playerB; `gift attribution: recipient=Player42002 sender='playerA'` |

**RESULT: playerA opened the inbox and saw NO gifts**, even though the server
delivered the gloves gift on the login read (both `GET,GET` and the `MAP_GET`
index show it). This is the receiver-side failure in isolation.

**Store after the session (verified):**

```
doc 100000001 (playerA)   lastModifiedName='playerB'  gifts=['15717548710165239937']  gloves  <- RECEIVED, not shown
doc 100000002 (playerB)  lastModifiedName='playerA'    gifts=['15141810279299964802']  axe     <- just sent
```

**KEY NEW FINDING — the `MAP_REMOVE keys=key` loop:**
The receiver's client, on every inbox open, issues `MAP_REMOVE keys=key values=`
(the literal placeholder `key`, from the `[%E%] => {"key":"%K%","data":0}` index
shape). The index read returns `data` going `0 -> 1` between reads. This looks
like the client is treating the served index entry as a **placeholder to be
cleared**, not a gift to ingest — i.e. the server's index `{"key":"...","data":N}`
is being consumed as "delete this key" rather than "here is a gift".

**Implication:** the receive document is reaching the client correctly (gloves
blob is in the `GET,GET` body in pairs shape, id is in the `MAP_GET` index), but
the client does not turn it into a visible inbox row. The gap is now
**display/ingest on the client side**, not delivery. Two concrete things to try
next:

1. The `MAP_GET` index entry shape — `{"key":"<id>","data":0}` with `data`
   flipping to 1 may need a different `data` value or a different key than the
   literal `key`; the `MAP_REMOVE keys=key` may be the client trying to remove a
   row the server keeps re-serving.
2. Compare against `docslot.txt` (status/timestamp slots) — the served record's
   `lastModifiedName`/status may gate whether the client renders the row.



---

## 11. 2026-10-09 (late) - `IsFromPlayer` byte flag flip (round_1021)

### What was ruled out first (so we don't retry)
- **MAP_GET cannot carry the blob.** Every blob-in-MAP_GET variant errors the
  client (`0xb08b07`): obj `{"<id>":"Qk9E..."}` -> `Error: Unable to process
  string`; pairs `[["<id>","Qk9E..."]]` -> inbox access error. The frozen
  `docshape.txt` baseline (103 good / 15 bad) already proved the MAP_GET `data`
  slot is read as **int64 only**. So `MAPGET_MAP` was **reverted off** — the
  inbox now opens cleanly again on the int64 index (`mapget_map=off`, confirmed
  in the running banner `round_1021`).
- The receive blob *does* reach the client on the plain `GET,GET` route (pairs
  shape, `get_map=pairs`), but the inbox still shows **no row** (open, empty).
  The item also did not appear in the save this session (the 7KB save growth was
  the game's own DLC/preorder `Tome_*_Giver` bonus mails, not our gift).

### The finding: `IsFromPlayer` is TRUE on received mail
The gift blob is a BOD container (base64 -> `BOD`+header+zlib -> payload). In
the payload each field is `[key][value TLV]`. The `IsFromPlayer` value TLV is:

```
04 01 01   = type 0x04 (bool), len 1, value 0x01 (TRUE)
```

Both stored received gifts carried `IsFromPlayer = TRUE` (0x01). Hypothesis:
this flag means "sent by the local player"; when true the client files the gift
under **Sent** (the original "gifts from myself" symptom) and never renders it
as an incoming row (the now-empty-inbox symptom). This is the ONE field that
differs and matches BOTH symptoms, so it is the highest-value single byte to
test.

### The change applied (server-side blob rewrite, reversible)
`tmp_setifp.py --to false` flipped the value byte `01 -> 00` (`04 01 00` = bool
FALSE) on both received gifts, in place, using the same proven BOD-rewrite
technique as the `SERVERMAIL` name rewrite (fresh-intern not needed for a bool;
length unchanged so the BOD payload-length field is untouched). Round-trip
verified in memory before writing; confirmed on disk:

```
doc 100000001 gift 15717548710165239937  IsFromPlayer.tlv = 040100  (false)
doc 100000002 gift 15141810279299964802  IsFromPlayer.tlv = 040100  (false)
```

Server reads docs fresh from disk per request (no cache), so this is **live**
without a restart. Backup of the pre-flip store is at
`server/data/cloud_backup_20261009_precleartest/` and an additional backup was
taken before the flip.

### Restore / revert
- Revert this flip: `python tmp_setifp.py --to true` (flips back to `04 01 01`).
- Restore the whole store: copy
  `server/data/cloud_backup_20261009_precleartest/` back over `server/data/cloud/`.

### The decisive test (round_1021, playerA holds the gloves gift)
Launch as **playerA**, open the exchange:
- **A row now appears** (gloves, per-sender mail) -> `IsFromPlayer` was the
  gate; we then wire the real flag into the normal send/receive path (send writes
  it false; the receiver's own outbox keeps it true).
- **Still empty (no row)** -> `IsFromPlayer` is not the (only) gate. Next
  untouched lever: the `MAP_REMOVE keys=key` + index `data:0->1` behaviour
  (section 9), i.e. the client treating the served index as a clear-placeholder.

Server banner (live proof of the reverted regression):
`... get_map=pairs, mapget_map=off`.



---

## 10. MAP_GET is the REAL receive route - serve the blob map there (DS2_MAPGET_MAP)

2026-10-09 ~19:40 (round_1017). This overturns the section-9 "slot" theory.

**Why slots are NOT the blocker.** In the section-9 receive session the `MAP_GET`
read at 19:14:10 already served slot 4 `lastModifiedProfileName` = the correct
sender (`playerB`), yet the inbox showed nothing. The name/slots are fine -
the row simply never renders.

**The real lever (RE evidence).** The gift blob's base64 decoder (`0xb4394c`) has
exactly ONE call site: the entry walk at `0xb44f96` (stride 0x30 = {key {ptr,size},
value char* @+0x10}). That walk is fed by the CloudInternal map parser `0xb08b07`,
and all 54 `Unable to process string` verdicts prove that parser runs on the
**MAP_GET** route, not the plain `GET`. A raw blob on `GET` is rejected because
`0xb08b07` needs `[` or `{`; the plain-GET document never reaches that parser.

But `MAP_GET` was serving the **id->int index** `[{"key":"<id>","data":0}]`
(value = integer 0). It parses, but the walk decodes the integer 0 - nothing -
so `onGiftReceived -> GiftComponent.addGift` never fires and no row renders.
That is the whole bug:

* plain `GET,GET` -> serves the gift **blob** (has the data) but that route's
  document never reaches the decoder - no ingest.
* `MAP_GET` -> reaches the decoder (would ingest) but serves `0`, not the blob -
  no data.

**The fix (implemented, `server/ds2_server.py`).** New env `DS2_MAPGET_MAP`:

    1 | obj  (default) -> {"<giftId>":"Qk9E...", ...}   <- canonical key->value the walk eats
    pairs            -> [["<giftId>","Qk9E..."], ...]

When ON, a doc that HAS gift-id keys serves its gift **map** on the `MAP_GET`
route (in the serve block, `opi == 'MAP_GET'`), instead of the integer index, so
the walk base64-decodes the blob -> ingests -> the inbox renders the row.
Single-value docs (GameToWeb) have no gift-id key -> `doc_map_json` returns None
-> the normal index/docshape render is kept (the blob map never clobbers them).
`doc_map_json(doc, dk, shape)` gained a `shape` param so each route picks its own
(plain GET uses `GET_MAP`, MAP_GET uses `MAPGET_MAP`).

**Oracle.** This is NOT judgeable from the client log (`response good` is
meaningless here). The oracle is the SAVE: after relaunching the recipient, the
recipient's save must gain a gift record with `GifterName` = the sender, and the
inbox must show the row with the sender name.

**Started as round_1017** (PID 37980, port owner 37980) with
`DS2_EMPTY_DOC=1 DS2_DOC_MAP=1 DS2_TRACE=1 DS2_GET_MAP=1 DS2_MAPGET_MAP=1`.
Suites: test_cloud_exchange 57/57, test_gift_capture 92/92, test_login_flow pass.

Store at test time (symmetric):
    doc 100000001 (playerA inbox)   gift 15717548710165239937  (gloves, from playerB)
    doc 100000002  (playerB inbox) gift 15141810279299964802  (axe, from playerA)

Next: relaunch EITHER account, open the exchange, and read the save gift record.

---

## 12. 2026-10-09 (round_1021) - clean-slate symmetric send; MAP_GET blob-map REJECTED (decisive)

Full client+server capture of the round_1021 session. The store was cleared
first (`tmp_clear_gifts.py`) so both mailboxes start empty. The user then:

1. Sent "gloves" as `playerB` -> `playerA`.
2. Switched to `playerA`, opened the inbox: **NO gift shown**.
3. Sent "axe" as `playerA` -> `playerB`.

### Store state after the two sends (from disk, verified)

```
acct 100000001 (playerA inbox)   gifts=['15717548710165239937']  lastMod='playerB'  (gloves)
acct 100000002  (playerB inbox) gifts=['15141810279299964802']  lastMod='playerA'    (axe)
```

So BOTH sends landed correctly in the RECIPIENT's doc. The send path is correct
end to end. The failure is purely on the DISPLAY / ingest side.

### Server cloud ops (round_1021_stdout.txt, playerA = Player12554 = 100000001)

```
21:15:02  GET,GET     Player12554  shape=getmap:pairs  [["15717548710165239937","Qk9E...","<Stats>..."]]
21:15:33  MAP_SET     Player12554  GameToWeb  [{"key":"GameToWeb","data":0}]
21:15:54  MAP_GET     Player12554  [{"key":"15717548710165239937","data":1}]
21:15:55  MAP_REMOVE  Player12554  keys=key  [{"key":"15717548710165239937","data":0}]
21:35:38  GET,GET     Player12554  shape=getmap:pairs  (login re-read)
21:36:08  MAP_SET     Player12554  GameToWeb
21:36:31  MAP_GET     Player12554  [{"key":"15717548710165239937","data":0}]
21:36:32  MAP_REMOVE  Player12554  keys=key  [{"key":"15717548710165239937","data":1}]
```

### Client PARSE trace (ds2_tls.log) - the three MAP_GET shapes and their verdicts

Login as playerA (steamid 76561198000000002 & 0xffffffff = 100000001). The
login `GET,GET` resolves the profileNames first:

```
19:45:45 PARSE #3 [0,[],[],["Player12554"],0,[[["Player12554"],100000001,100000001]]]
   (profileNames 76561198000000002 -> Player12554 / account 100000001)
```

**A. Login `GET,GET` serves the pairs blob map - ACCEPTED (no error):**
```
19:45:31 GET,GET  (len=2457)
19:45:31 PARSE #1 [0,[[0,"[[\"15717548710165239937\",\"Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3N...","<Stats>..."]]]]
   -> NO "Unable to process string". The login GET route accepts the blob map.
```

**B. `MAP_GET` serving the OBJ blob map - REJECTED:**
```
19:56:05 PARSE #6 (len=898) [0,[[0,"{\"15717548710165239937\":\"Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3N..."},...]]]
19:56:05 PARSE #7 (len=855) {"15717548710165239937":"Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3N..."}
19:56:05 [GAME] Error: Unable to process string: {"15717548710165239937":"Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3N..."}
```

**C. `MAP_GET` serving the PAIRS blob map - REJECTED:**
```
20:56:48 PARSE #6 (len=900) [0,[[0,"[[\"15717548710165239937\",\"Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3N...\"]]",...]]]
20:56:48 PARSE #7 (len=857) [["15717548710165239937","Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3N..."]]
20:56:48 [GAME] Error: Unable to process string: [["15717548710165239937","Qk9E/QQBAQAaAAAAJgAAANQCAAB42mNk3N..."]]
```

**D. `MAP_GET` serving the INTEGER INDEX - ACCEPTED (no error):**
```
19:46:26 PARSE #6 (len=86)  [0,[[0,"[{\"key\":\"15717548710165239937\",\"data\":1}]",0,1791562635,"playerB"]]]
19:46:26 PARSE #7 (len=41)  [{"key":"15717548710165239937","data":1}]
   -> parses clean. This is the ONLY MAP_GET document the client accepts.
```

**THE DECISIVE FINDING: the ingest op is `MAP_GET`, and it ONLY accepts the
integer-slot index `[{"key":"<giftId>","data":<0|1>}]`. It REJECTS any blob map
(obj or pairs) with `Unable to process string`.**

So `DS2_MAPGET_MAP` (serving the gift BLOB on MAP_GET) is a DEAD END - that op
runs the CloudInternal map parser (0xb08b07) which cannot take a base64 blob
string as a value. The gift CONTENT (the `Qk9E...` blob with GifterName + Items)
can only ride the **plain login `GET,GET`** route; `MAP_GET` only ever carries
the gift INDEX (id -> collected-state).

### Exact wire protocol captured (round_1021, both accounts)

All URLs are `https://thqc-darksiders2-pc.thqconnect.com:8443/thqconnect/cloud/execute?...`.

1. **Login read** - `profileNames` is DUPLICATED once per docKey:
   `profileNames=/steam/<id>,/steam/<id>&ops=GET,GET&docKeys=TomeGift,GameToWeb&...`
   Response (accepted): `[0,[[0,"<TomeGift text>",<ts>,"<name>"], [0,"<Stats>...",<ts>,"<name>"]]]`
   Each document element = `[status, text, timestamp, profileName]`.

2. **Save stats** - `profileNames=/steam/<id>&ops=MAP_SET&docKeys=TomeGift&keys=<giftId>` (single profile).
   Response: `[0,[[0,"<echo>",<ts>,"<name>"]]]`.

3. **Inbox index** - `profileNames=/steam/<id>&ops=MAP_GET&docKeys=TomeGift&keys=&values=&policy=0&worldPerms=...`
   `keys=` EMPTY means "give me the whole index". This is the op that lists gifts.
   ACCEPTED body: `[0,[[0,"[{\"key\":\"<giftId>\",\"data\":1}]",<ts>,"<name>"]]]`
   where `data:1` = present/uncollected.

4. **Collect** - `profileNames=/steam/<id>&ops=MAP_REMOVE&docKeys=TomeGift&keys=key&values=&policy=...`
   `keys=key` = remove the entry whose `key` field matches. Response flips `data:0`.

Account/steamid binding (verified via the PARSE #3 profileNames resolve):
```
76561198000000002 & 0xffffffff = 100000001 = Player12554 = playerA
76561198000000001 & 0xffffffff =  100000002 = Player42002 = playerB
```

### Verdict per route (from the client PARSE trace)

| Route | Body served | Client verdict |
|-------|-------------|----------------|
| login `GET,GET` | pairs blob map `[[id,"Qk9E..."]]` | ACCEPTED (no error) - but produces NO save/UI row |
| `MAP_GET` | obj blob map `{id:"Qk9E..."}` | REJECTED - "Unable to process string" |
| `MAP_GET` | pairs blob map `[[id,"Qk9E..."]]` | REJECTED - "Unable to process string" |
| `MAP_GET` | integer index `[{key:id,data:1}]` | ACCEPTED - the ONLY MAP_GET body that parses |

### What this rules in / out

* RULES OUT: serving the blob on MAP_GET (DS2_MAPGET_MAP) - impossible, that op
  rejects blob strings. Rounds 1014/1015 kept MAPGET_MAP on, so their MAP_GET
  served a rejected blob map and the inbox never had a valid index row.
* The CONTENT ingest MUST come from the login `GET,GET` route (the only route
  that will carry the blob). That route accepted the pairs blob map at the
  envelope level but still yielded no row - so the remaining failing link is
  **associating the login-GET blob with the MAP_GET index**, or the login-GET
  blob not actually being base64-decoded/ingested into a save gift record.

### Next lever (isolated, one switch)

Keep `DS2_MAPGET_MAP` OFF so MAP_GET serves the accepted integer index (as
round_1021 already does), and drive ONLY the login `GET,GET` content route.
`DS2_GET_MAP` is now the sole variable to sweep on the login read:
`off` (bare `Qk9E...` value) vs `pairs` vs `obj`. Watch `tmp_savecmp.py` and the
inbox after each, relaunching the recipient. The row should appear when the
login-GET body is the shape the decoder at 0xb4394c expects.

---

## 2026-10-09 23:56 - CURRENT STATE (authoritative; read this first after any compaction)

### Server
- **Up:** round_1022, PID 43300, port 8443 (greet_tls=true). Banner:
  `empty_doc=True, doc_map=True, get_map=pairs, mapget_map=off`.
- Env it was launched with: `DS2_EMPTY_DOC=1 DS2_DOC_MAP=1 DS2_TRACE=1 DS2_GET_MAP=1 DS2_DOC_SLOT=1`.
  `DS2_DOC_SLOT=1` is the line that ARMED the docslot sweep (gated at startup, line 172).
- `server/docshape.txt` has exactly ONE active line:
  `[%E%] => {"key":"%K%","data":1}` (everything else commented).
- `server/docslot.txt` has ONE active sweep: a round-robin of 10 lines that
  overrides record slots 0 (docStatus) and 4 (lastModifiedName), e.g. `0=0,4="playerB"`.
- Tooling: `tools/reframe.py` (log reframer, levels full/info/gift) is the durable
  investigation tool. No real-server golden gift response exists in the repo; every
  gift doc is game-written, so there is nothing to diff against — live trial is the only
  path.

### Store (server/data/cloud) — 2 live gifts
- `100000001` (playerA mailbox): gift `15717548710165239937`,
  gifterName=`playerB`, item=`armor_gloves_wanderer_d01`.
- `100000002` (playerB mailbox): gift `15141810279299964802`,
  gifterName=`playerA`, item=`weapon_axe_a01`.

### Both saves currently hold 0 TriStone gift records
(Verified via `tmp_giftaudit.py`): neither save has an ingested gift row.

### What the last empty-inbox test PROVED (playerA, 22:48-23:05 sessions)
Full client timeline, all `response good`, **zero** `Unable to process string`:
- Login `GET,GET` (PARSE #1) serves the pairs gift blob (`len 2457`) — parses, no error,
  but **no inbox row and no save record**.
- MAP_SET GameToWeb, then MAP_GET serves the index `[{"key":"<giftId>","data":1}]`, parses
  fine, but **inbox still empty**.
- MAP_REMOVE runs (client treats it as "have it" and clears from server), yet the save stays
  empty. Ingestion into the save is failing; the inbox renders from the save's mail records.
- The item **does** land in the save's item counts (attachment attachment), but the MAIL ROW
  (GifterName + item) is never created. Attachment delivered ≠ mail row created.

### RULED OUT (do not re-try)
- **`data` int64 is NOT the inbox render gate.** `data:1` forced on MAP_GET still = empty inbox.
  (23:05:45 was `data:1` + empty → earlier A/B was session coincidence.)
- **Wire responses are byte-clean and accepted on every route.** Not a wire/shape problem.
- **The `data` map index is not the gate.** `doc_shape()` is mandatory one line (`data:1`) and
  serves the index with `data:1` every time; inbox remains empty.

### HOW THE ROUTES ACTUALLY WORK (from the record-construction loop in ds2_server.py,
  lines 1984-2056, and doc_slot)
- MAP_GET serves the rendered index-map (`{"key":...,"data":<int64>}`) to the client. It is the
  decode chain's ONLY consumer on the MAP route, and the index's `data` slot is the ONLY thing
  that varied across empty/non-empty tests. It is **not** what the client passes to the binder.
- The LOGIN read (GET,GET) is what builds the deliverable records: it rewrites the doc text
  under the pairs/template into the five-slot record
  `[status][document][docPolicyBits][lastModifiedTime][lastModifiedName]`, then `doc_slot()`
  can override slots 0/2/3/4 live. `slots={}` currently means the sweep fired with NO value.
- The sweep is round-robin across 10 lines per request, so which slot value lands on the
  login/read is effectively random per reopen. A soak of repeated opens is required.

### NEXT LEVERS (priority order)
1. **Sweep VALUE soak** - the sweep is armed; replay the exchange several times as playerA.
   Slot-0 (docStatus enum) and slot-4 (sender name) are the plausible mail-existence flags. A
   `0=2` (status=2) or a named `4="playerB"` line landing on the login read should render
   the row.
2. **Status enum range** - docStatus byte is enum -2..13 (see comments @2037). `0` may mean
   "no mail"; fix = a positive mail-present status on the received row so the binder keeps it.
3. **Record identity slot** - slot-4 sender name may be required for the binder to recognize
   the received row as an incoming gift (currently the pairs blob carries gifterName, but the
   record's lastModifiedName slot is what the save binder keys on).
4. If still empty after a soak: this is a client-side binder path (~0xb44f96 walk) that is
   never reached, and the capturable ground truth is a **real server capture of a received
   gift** (the project's earlier captures are login+query only). New deliverable would be a
   MITM hook on the client to log exactly what the binder receives.

### VERIFICATION PROTOCOL (repeat after each open)
1. Game opens exchange as playerA (fresh, no send first).
2. Read `server/logs/round_1022_stdout.txt` gift lines + `ds2_tls.log` PARSE entries for
   `GET,GET` and `MAP_GET` — confirm the login pairs blob length and the index `data:`.
3. `tmp_giftaudit.py` → expects a new gift row in save 100000001 with a nonzero status slot.
4. Inbox on-device → expect the row (gift 15717548710165239937, from playerB).

### TOOLS
- `tools/reframe.py` levels: `full|info|gift` (gift = folds RunCallbacks/scan/socket noise,
  counts it in the header). Flags: `--since/--until HH:MM[:SS]`, `--grep <regex>`, `--wrap N`,
  `--tail N`. Reads the multi-day client log fast.


## 2026-10-09 23:57 - TEST SESSION STARTED (round_1022, PID 43300, sweep-armed)

Server UP (round_1022, PID 43300): `empty_doc=True, doc_map=True, get_map=pairs, mapget_map=off`.
Env includes `DS2_DOC_SLOT=1` → `doc_slot()` sweep is ARMED. `docshape.txt` = one active line
`[%E%] => {"key":"%K%","data":1}` (MAP_GET index only). `server/docslot.txt` sweep is
round-robin across 10 lines, one lands per request.

### WRITTEN-MINUTE TEST (test #0)
Primary line placed in docslot.txt (round-robin priority 0):
`0=1`  → docStatus = 1 = "unread / mail present"
Secondary line (keeps identity coverage, lands on later requests):
`0=0 4="playerB"`

### TEST PROTOCOL
1. Launch recipient (playerA) and open the exchange (no send).
2. Check `server/logs/round_1022_stdout.txt` gift line → `slots={0:1}` or similar.
3. Check `ds2_tls.log` PARSE entries for login `GET,GET` and `MAP_GET`.
4. `tmp_giftaudit.py` → expect a new gift row in save 100000001, nonzero status slot.
5. Inbox on-device → expect row (gift 15717548710165239937, from playerB).
6. **Write the verdict into this doc at the matching "## 2026-10-09 ..." section; do NOT retry
   a failing line without re-reading. If the vote is: inbox shows row → fix confirmed;
   inbox still empty → test next line in priority order (1, 2, 13, then 4=identity).**

### FILES THIS SESSION WROTE
- `server/docslot.txt` (test #0 primary + secondary lines)

