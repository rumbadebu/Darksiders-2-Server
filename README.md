# Darksiders 2 — Local Server Revival

AI generated Private server emulator for **Darksiders 2 (PC, Steam, appid 50650)** that brings back the online features lost when THQ shut down the "THQ Connect" (`thqconnect.com`) servers.

The project re-implements THQ Connect locally so the Steam game's online features work again — most importantly the **cloud document store** that powers player-to-player **item/gift exchange**.

## What this project does

1. **Patches `Darksiders2.exe`** — replaces the embedded THQ Connect Root CA and leaf certificates with locally generated ones, and rewrites the GDS hostname (`ds2.gds.thqconnect.com` → `gds.thqconnect.com`).
2. **Redirects hostnames** to `127.0.0.1` via the system hosts file (the game still resolves `thqconnect.com` through THQ's live DNS zone, which points at dead servers).
3. **Runs a local server** (`Server/server/ds2_server.py`) implementing the login, cloud document store, long-poll tunnel, and GDS RPC endpoints on TLS ports 443/8443 (+ plain HTTP 80).

## Repository layout

```
Darksiders2Server/
├── README.md                      # this file
├── patch_game.py                  # one-step cert mint + exe patch (root level)
├── mint_certs.py                  # mints a coherent certificate set
├── 1 - PATCH GAME.bat             # run once as admin: mint certs + patch exe
├── 2 - START SERVER.bat           # start the local server emulator
├── cert-tools/                    # certificate generation scripts
│   ├── build_certs.py             # root CA + server cert (uses openssl subprocess)
│   ├── mint_root2.py              # short-DN root CA that fits exe slots
│   ├── mint_login_ca.py           # login CA for leaf1 slot
│   ├── clone_leaf.py              # clones original leaf identities
│   ├── mint_leaf_ca.py            # wire-identity leaf CAs
│   ├── build_client_kit.py        # CN=DS2 mTLS client kit
│   ├── hosts_entries.ps1          # add/remove hosts-file redirects (self-elevating)
│   ├── test_tls.py / test_tls10.py# TLS verification helpers
│   └── out/                       # generated cert set + server-set/
├── Client/                        # EXE patching tools
│   ├── patch_exe.py               # patches Darksiders2.exe (idempotent)
│   ├── patch_game.py              # hash-verify + patch wrapper
│   ├── check_exes.py / check_result.py
│   ├── splice_slot.py
│   ├── debug/                     # pristine Darksiders2.exe.original goes here
│   └── out/                       # patched Darksiders2.exe output
├── Server/                        # the local server emulator
│   ├── server/
│   │   ├── ds2_server.py          # THQ Connect emulator (stdlib only)
│   │   ├── config.json            # ports / TLS flags
│   │   ├── docshape.txt           # document-shape templates
│   │   ├── docslot.txt            # record-slot overrides
│   │   ├── certs/                 # TLS certs loaded by server (generated; deploy here)
│   │   ├── data/cloud/            # persistent cloud document store (per accountID)
│   │   └── logs/                  # request log (requests.log)
│   ├── tools/
│   │   ├── build_client_kit.py
│   │   ├── gift_capture.py
│   │   ├── gift_doc_decode.py
│   │   ├── sim_reader.py
│   │   ├── reframe.py
│   │   └── test_*.py              # login / cloud / gift tests
│   └── *.py                       # gift reverse-engineering harness
└── Docs/
    ├── PATCHING_GUIDE.md          # full exe-patching & cert-swap recipe
    └── TLS_BISECT_FINDINGS.md     # TLS compatibility research
```

## Requirements

| Requirement | Notes |
|-------------|-------|
| **OS** | Windows (hosts-file editing, UAC elevation, PowerShell) |
| **Python** | 3.8+ on PATH (`py` launcher or `python`) |
| **pip package** | `cryptography` — used by cert minting, patching, and verification scripts |
| **OpenSSL** | Needed by `build_certs.py`. Easiest: install **Git for Windows** (provides `openssl.exe`), or add `openssl` to PATH |
| **Game** | Darksiders 2 (Steam, appid 50650) — you must own a legitimate copy |
| **Admin rights** | Needed once to patch the exe and write the hosts file (the `.bat` files self-elevate) |

### Install Python

1. Download Python 3.8+ from [python.org](https://www.python.org/downloads/windows/).
2. During install, check **"Add Python to PATH"** and **"Install pip"**.
3. Verify: open a new terminal and run `python --version` (or `py --version`).

### Install pip dependencies

```cmd
python -m pip install cryptography
```

### Install OpenSSL (Git for Windows)

Download and install [Git for Windows](https://git-scm.com/download/win). This provides `openssl.exe` at:
`C:\Program Files\Git\mingw64\bin\openssl.exe` (detected automatically by `build_certs.py`).

## Step-by-step installation

### Step 1 — Clone the repository

```cmd
git clone <this-repo>
cd Darksiders2Server
```

### Step 2 — Install Python dependencies

```cmd
python -m pip install cryptography
```

### Step 3 — Mint certificates and patch the game (run once)
Copy original game exe to Client\debug\ and rename it Darksiders2.exe.original

Run the patching batch file **as Administrator**:

```cmd
"1 - PATCH GAME.bat"
```

What this does:
1. `mint_certs.py` — generates a fresh, coherent certificate set into `cert-tools\out\` (backs up any previous set to `cert-tools\backups\`).
2. `patch_game.py` — hash-verifies your pristine `Client\debug\Darksiders2.exe.original`, then patches it via `Client\patch_exe.py`, writing `Client\out\Darksiders2.exe` (+ `.orig` backup).

**Deploy the outputs manually** (the script tells you exactly what to copy):

```cmd
copy Client\out\Darksiders2.exe              <game-directory>\
copy cert-tools\out\server-set\*             Server\server\certs\
```

> The pristine `Darksiders2.exe.original` (SHA-256 `39FED14186C343FDF7F7D87B6FA779247A715347084D9E14A2708A0D7BF951B9`) must be placed at `Client\debug\Darksiders2.exe.original` before patching. `patch_exe.py` refuses to double-patch an exe that already contains the "Local DS2" marker — always re-patch from the pristine original.

### Step 4 — Redirect hostnames to localhost

Run the hosts-file helper **as Administrator**:

```cmd
cert-tools\hosts_entries.ps1
```

This adds `127.0.0.1` entries for the `thqconnect.com` hostnames the game contacts. It is idempotent and self-elevating. To undo later, run it again (it removes the entries).

### Step 5 — Start the local server

```cmd
"2 - START SERVER.bat"
```

Keep this window open while you play. It launches `Server\server\ds2_server.py` on ports 443/8443 (TLS) and 80 (HTTP).

### Step 6 — Launch the game and play

Launch `Darksiders2.exe` normally. Online features (login, cloud document store, item exchange) now hit your local machine instead of the dead THQ servers.

To undo everything (restore the original exe and hosts file):
1. Replace the patched `Darksiders2.exe` in the game directory with your pristine backup.
2. Run `cert-tools\hosts_entries.ps1` again to remove hosts entries.

## Directory details

### `cert-tools/` — certificate minting

The cert toolchain runs in this order (orchestrated by `mint_certs.py`):

```
build_certs.py → mint_root2.py → clone_leaf.py → mint_leaf_ca.py → mint_login_ca.py → build_client_kit.py
```

Outputs land in `cert-tools\out\`, and the server-ready kit is assembled at `cert-tools\out\server-set\`. Copy that entire folder to `Server\server\certs\`.

### `Server/` — local emulator

- `server/ds2_server.py` — the THQ Connect emulator. Python stdlib only (no third-party deps).
- `server/config.json` — ports and TLS flags.
- `server/certs/` — TLS certificates the server loads (deploy `server-set\*` here).
- `server/data/cloud/` — persistent cloud document store, one directory per accountID. This is where exchanged items live.
- `server/logs/requests.log` — full request/response log. Check here first if the game rejects something.

### `Client/` — EXE patching

- `patch_exe.py` — replaces embedded CA/leaf/client certs and rewrites the GDS hostname inside `Darksiders2.exe`.
- `patch_game.py` — wrapper that hash-verifies the pristine exe and calls `patch_exe.py`.

## Verification helpers (server must be running)

```cmd
python Server/tools/test_login_host.py   # login hostname end-to-end check
python Server/tools/test_login_flow.py   # full login + cloud round-trip
```

Inspect the cloud store while the server runs:

```cmd
curl http://localhost/admin/status
curl "http://localhost/admin/cloud/get?accountID=123&docKey=default"
```

## TLS compatibility notes

The game ships OpenSSL 1.0.0 (TLS 1.0 era). The server therefore permits TLSv1 + legacy ciphers (`SECLEVEL=0`). If your Python build refuses legacy TLS, check the server startup log for guidance.

For client-side TLS debugging, a proxy `dinput8.dll` can be dropped next to the game executable to log every TLS handshake the game performs (`ds2_tls.log`, `ds2_tls.pcap`).

## License / disclaimer

This is a private, local-server project for educational and preservation purposes. You must own a legitimate copy of Darksiders 2. The project does not distribute game files, the game executable, or any copyrighted game assets — you must supply your own `Darksiders2.exe.original`.
