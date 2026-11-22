# Darksiders 2 — Local Server (THQ Connect emulator)

This repo is the **server** half of the Darksiders 2 private-server revival. It
re-implements THQ's shut-down "THQ Connect" endpoints locally so the Steam game's
online features work again — most importantly the **cloud document store** that powers
player-to-player **gift exchange**.

> Full project-wide file inventory (all 4 repos) lives in `../FILE_INVENTORY.md`.

## Layout

```
server/
  ds2_server.py        the emulator (single file, Python stdlib only)
  config.json          ports / TLS flags
  docshape.txt         live-editable document-shape templates (the receive fix lives here)
  docslot.txt          live-editable record-slot overrides (sender name, etc.)
  certs/               TLS certs the server loads (generated; see ExePatcher)
  data/cloud/          persistent cloud document store (one dir per accountID)
  backup/              protocol reference + sample captured gifts
tools/
  gift_capture.py      parse requests.log + replay a gift into the store
  gift_doc_decode.py   decode a TomeGift BOD blob (b64 -> BOD -> zlib -> payload)
  sim_reader.py        simulate the client's document walk (accept/reject oracle)
  reframe.py           bounded log reader (never read big logs whole)
  test_*.py            login / cloud-exchange / gift / log-rotation tests
  build_client_kit.py  assemble the client cert kit
_*.py                  gift reverse-engineering harness (see JOURNAL.md)
STATE.md               READ FIRST — current state (now: RECEIVE SOLVED)
JOURNAL.md             append-only dated experiment log
GIFT_PROTOCOL_FALLBACK.md  deep gift-protocol reference
```

## Run

```
2 - START SERVER.bat        # or: python server\ds2_server.py
```

Requires the game to already trust the server — that is the **ExePatcher** repo's job
(patch the exe + redirect `*.thqconnect.com` to `127.0.0.1`). Python 3.8+ on PATH.

`ds2_server.py` has **no third-party dependencies**. The gift tools import it via
`sys.path` and expect this `server/` + `tools/` layout, so keep them as siblings.

## Current state

**Gift SEND and RECEIVE both work, verified in both directions** (sender name correct,
collect clears the mailbox). See `STATE.md` for the live status and `JOURNAL.md` for how
it was solved. `docshape.txt` is re-read per request, so document shapes can be tuned
with no server restart.
