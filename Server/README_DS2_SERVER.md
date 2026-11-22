# Darksiders 2 — Local Server Revival

This project brings back the online functionality of **Darksiders 2 (PC, Steam,
appid 50650)** after THQ's servers (`thqconnect.com`) were shut down. It
implements the game's original service endpoints locally, including the cloud
document storage that powers player-to-player item exchange.

## How it works

The game talks HTTPS to THQ's "THQ Connect" platform. Packet capture plus a
DNS sweep revealed the real topology (the thqconnect.com zone is still live,
but every server behind it is dead):

| Service | Host | Port | Endpoints |
|---|---|---|---|
| Login / main service | `thqc-darksiders2-pc.thqconnect.com` (was 38.103.18.162) | **8443** | `/thqconnect/login/*`, `/news/getNews`, `/cloud/*`, `/tunnel/tunnel`, `/url/getURL` |
| Game Data Service | `ds2.gds.thqconnect.com` (was 38.103.18.186) | **443** | `/gds/rpc` (content type `app/x-gds-rpc`) |
| Health check | either | either | `/version` |

The login hostname is built at runtime as `<appID>.thqconnect.com`
(`thqc-darksiders2-pc` + domain) with the port **hardcoded to 8443** in the
binary (`mov eax, 0x20fb`). The GDS name is built from the app id:
`ds2` + `.gds` + `.thqconnect.com`.

The game verifies TLS against a **Root CA embedded in the exe** and presents a
**client certificate** for mutual TLS. This project:

1. **Patches `Darksiders2.exe`** (backup: `Darksiders2.exe.orig`)
   - replaces the embedded Root CA with a locally generated one,
   - replaces two embedded `*.thqconnect.com` leaf certs with our server cert
     (RSA-1024/SHA-256, SAN wildcards `*.thqconnect.com` + `*.gds.thqconnect.com`,
     sized to fit the fixed 860-byte exe slots),
   - rewrites the GDS hostname to `gds.thqconnect.com`.
2. **Redirects the hostnames** to `127.0.0.1` via the system hosts file
   (elevated, idempotent, marked block — easily removable). This is essential:
   the game resolves `thqc-darksiders2-pc.thqconnect.com` /
   `ds2.gds.thqconnect.com` through
   the still-alive THQ DNS zone, which points at long-dead servers.
3. **Runs a local server** (`server/ds2_server.py`) implementing all endpoints
   on TLS ports **443 and 8443** (+ plain HTTP 80):
   - login with Steam auth tickets (accepted offline),
   - persistent cloud document store with `MAP_SET / MAP_GET / MAP_INC /
     MAP_REMOVE / SET_PERM` operations — this is where exchanged items live,
   - long-poll notification tunnel,
   - GDS RPC stub,
   - full request logging to `server/logs/requests.log`.

## Quick start

```
1 - PATCH GAME.bat      <- run once (self-elevates; accept the UAC prompt)
2 - START SERVER.bat    <- keep open while playing
launch Darksiders2.exe  <- play; online features hit your machine
3 - RESTORE GAME.bat    <- undo everything
```

Requirements: Python 3.8+ on PATH, Git for Windows (for openssl), Windows.

Verification helpers (server must be running):

```
python tools\test_login_host.py  <- real login hostname end-to-end check
python tools\test_login_flow.py  <- full login + cloud round-trip
```

## Item exchange between players

Every client that logs in gets an account derived from its Steam ticket and its
own cloud document namespace under `server/data/cloud/<accountID>/`. The
game's asynchronous multiplayer (finding gear dropped by other players) flows
through the cloud document ops, which are persisted to disk — so multiple
players pointing at the same server share loot.

Inspect/manipulate the store while the server runs:

```
curl http://localhost/admin/status
curl "http://localhost/admin/cloud/get?accountID=123&docKey=default"
curl -X POST http://localhost/admin/send -d "accountID=123" -d "message={"type":"gift"}"
```

## Wire-format iteration

The exact JSON shapes of the original protocol are partially reconstructed from
strings in the executable. The server logs every request/response pair; if the
game rejects something, check `server/logs/requests.log` and adjust the handler
in `server/ds2_server.py`. Response envelopes currently include both
top-level fields and a nested `output` object for maximum compatibility.

## TLS compatibility notes

The game ships OpenSSL 1.0.0 (TLS 1.0 era). The server therefore allows
TLSv1 + legacy ciphers (`SECLEVEL=0`). If your Python build refuses legacy
TLS, see the startup log for guidance.

## Client-side TLS logging (proxy DLL)

To debug certificate problems from the GAME's point of view, a proxy
`dinput8.dll` can be dropped next to the game executable (already built;
sources in `tools/tls_proxy/`). It forwards input functions to the real
system DLL (`dinput8_real.dll`) and hooks WinSock imports process-wide,
logging every TLS handshake the game performs:

* `ds2_tls.log` – annotated: ciphers offered/selected, certificate chain
  received, certificate requests, alerts (`48 = unknown_ca` highlighted).
* `ds2_tls.pcap` – raw capture, open in Wireshark → Follow TCP Stream.

Just launch the game normally; delete/move the two `dinput8*.dll` files to
disable logging again.

## Files

```
tools/build_certs.py      generates CA + server cert sized for the exe slots
tools/patch_exe.py        patches the executable (idempotent, from .orig)
tools/hosts_entries.ps1   adds/removes hosts entries (self-elevating)
server/ds2_server.py      the emulator
server/certs/             generated certificates
server/data/cloud/        persistent cloud document store
server/logs/              request logs
Darksiders2.exe.orig      pristine backup of the game executable