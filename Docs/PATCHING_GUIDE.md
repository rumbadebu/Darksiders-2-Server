# DS2 Exe Patching & Cert Swap Guide

Status: PROVEN IN-GAME 2026-10-17 (expB: TLS + login + inbox/gifts OK).
Full evidence: see `TLS_BISECT_FINDINGS.md` (same directory).

---

## 1. TL;DR — what was broken and what fixed it

The 2026-10-16 re-mint broke TLS with `PEM_read_bio_X509 failed` before
ClientHello. Root cause: **the exe's leaf1/leaf2 slots received `leaf_ca`
certs (root-signed `*.thqconnect.com`, extension-less)**. The game's bundled
OpenSSL 1.0.0 cannot parse that form *in-slot*. The Aug 28 working exe
instead embeds:

- **leaf1 slot** = `ca_login.pem` — self-signed
  `CN=THQ Connect Root CA - Login` (serial 0x5A11C0DE, **v3 with
  BasicConstraints + SKID**)
- **leaf2 slot** = **a byte copy of the root CA cert itself** (`ca.pem`,
  serial 0xC72, v3 with extensions)

With that arrangement the wire identity can stay the root-signed `leaf_ca`
(the server serves it; it chain-verifies against the exe root slot). The leaf
slots are NOT pinned against the served cert — proven: served `leaf1_ca`
differs from embedded `ca_login` in subject/issuer/serial and TLS passes.

---

## 2. Slot map (Darksiders2.exe, 18,822,656 bytes)

| Slot | Offset | Size | Content rule |
|---|---|---|---|
| leaf1 | 0xED76B8 | 870 | cert WITH v3 extensions (ca_login form) |
| leaf2 | 0xED7A20 | 860 | root CA cert copy (with extensions) |
| root | 0xF712D8 | 1248 | our root CA (`ca.pem`) — the trust anchor |
| clientcert | 0xF717C0 | 898 | CN=DS2 client cert (mTLS, port 443) |
| clientkey | 0xF71B48 | 886 | matching RSA private key (PKCS#1) |
| GDS host | string | — | `ds2.gds.thqconnect.com` -> `gds.thqconnect.com` |

Padding style inside slots is **cosmetic** (all-newlines works — expB proved
it). The int32 `-50` at 0xED7D84 (after leaf2 slot) that the Aug tooling
zeroed is **irrelevant** (expB kept it and works).

---

## 3. Cert set — one coherent family, six roles

Every cert below must come from the SAME minting session/set:

| Role | Where it lives | Proven property |
|---|---|---|
| Root CA (`ca.pem`) | exe root slot + server `certs/ca.pem` | v3 (BasicConstraints); short DN fits 1248B slot |
| Login CA (`ca_login`) | exe leaf1 slot ONLY | self-signed, v3 WITH extensions, fits 870B |
| Root copy | exe leaf2 slot ONLY | byte copy of `ca.pem`, fits 860B |
| Wire identity (`leaf1_ca`) | server `certs/leaf1_slot.pem` (padded to 870B) | root-signed, original `*.thqconnect.com` subject; extension-less is OK ON THE WIRE (parses in TLS chain; just never embed it in leaf slots) |
| Client CA | server `certs/client_local_ca.pem` | signs the client cert |
| Client kit | exe clientcert+key slots, server `certs/ds2_client.*` | CN=DS2, v1/no-extensions, RSA-1024, fits 898/886B |

Server config: `serve_leaf_clone=1`, `verify_client_cert=true` on `[443]`
only (8443 = client-cert-none).

---

## 4. Reproducible workflow

```
cd git\Darksiders2Server\cert-tools        # 1. mint the set into out\
python build_certs.py                      #    root CA + server.crt
python mint_root2.py                       #    short-DN root (fits slot)
python mint_login_ca.py                    #    login root for leaf1 slot
python build_client_kit.py                 #    CN=DS2 client kit
#    wire identity leaf1_ca.pem (root-signed *.thqconnect.com, original
#    subject DN) still comes from mint_leaf_ca.py if re-minting identity;
#    it goes on the WIRE only, never into the exe leaf slots

cd ..\Client                               # 2. assemble cert dir
mkdir certs-<setname>
copy server certs: ca.pem, server.crt, ds2_client.crt, ds2_client.key,
                  client_local_ca.pem, leaf1_ca.pem (wire identity)
copy ca_login.pem -> certs-<setname>\leaf1_ca.pem   (OVERRIDE: slot source)
copy ca.pem      -> certs-<setname>\leaf2_ca.pem   (OVERRIDE: slot source)
copy leaf1_orig.der + leaf2_orig.der (serial mapping inputs)

python patch_exe.py --exe debug --cert-dir certs-<setname>
#    writes out\Darksiders2.exe (+ hash-verify with DS2_EXE_SHA256)

# 3. server side: copy the SAME set into server\certs\
#    incl. leaf1_slot.pem = leaf1_ca.pem padded with \n to 870 bytes
#    (the wire identity -- NOT the ca_login that patch_exe side-writes!)

# 4. deploy out\Darksiders2.exe to game dir; restart server; test
```

### Pitfalls
1. **patch_exe.py side-effect:** it writes `leaf{n}_slot.pem` into the cert
   dir = the padded SLOT content (ca_login / root copy). Under this recipe
   the server must NOT serve those — server `leaf1_slot.pem` must be the
   padded **wire identity** (leaf1_ca). Keep server certs in a separate dir.
2. **Consistency:** mixing sets (e.g., exe root from set X, server ca from
   set Y) fails — even when serials coincidentally match (0xC72 existed in
   both Aug and Oct sets with different keys).
3. patch_exe.py is idempotent and refuses double-patching ("Local DS2"
   marker); always re-patch from `debug\Darksiders2.exe.original`
   (SHA-256 39FED141...BF951B9).

---

## 5. Experiment log (2026-10-17)

| Exp | Built | vs dbgpatch | In-game |
|---|---|---|---|
| A | patcher + Aug certs, leaf_ca in slots | 1522B (leaf slots + dword) | not tested (predicted fail) |
| B | patcher + certs-b (ca_login/root-copy slots) | 112B (padding + dword) | **SUCCESS: TLS, login, inbox, gifts** |
| C | expB + padding/dword fixups | 0B (SHA-identical 1A858521...CB548) | n/a (guaranteed = dbgpatch) |
| D | 2026-10-17 remint: full fresh set (new root mod head c1652478...) via build_certs → mint_root2 → clone_leaf → mint_leaf_ca → mint_login_ca → build_client_kit, certs-exp2 layout | n/a (fresh keys by design) | **armed** — exe SHA-256 DBF45576...395215; server-set in cert-tools\out\server-set\ |

Artifacts under `out\`:
- `Darksiders2.exe` = **exp2** (fresh set, DBF45576...395215) — current test exe
- `expB_Darksiders2.exe` = proven working Aug replica (was `Darksiders2.exe`)
- `expA_Darksiders2.exe`, `expC_Darksiders2.exe`, `hybrid_*.exe` = bisect history