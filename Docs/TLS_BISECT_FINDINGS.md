# Certificate Bisect Findings — 2026-10-17

Purpose: document every observation from the TLS regression bisect after the
2026-10-16 cert re-mint, so the next step can be chosen from facts.

---

## 1. Timeline

| When | What happened |
|---|---|
| 2026-08-23..28 | Original patching work — git server certs minted (Aug 23-28). **This setup worked in-game.** |
| 2026-10-16 15:08–16:21 | Server+exe (dbgpatch) still working: full login flow, MAP_GET served |
| 2026-10-16 18:41–18:42 | User ran cert-tools chain, copied new certs to root server/certs, replaced game exe with newpatch |
| 2026-10-16 19:15+ | **TLS broken**: server sees only `tls=False` connections, no handshakes |
| 2026-10-17 | Bisect session (this document) |

---

## 2. The two distinct failures (bisect-proven)

### Failure A — `PEM_read_bio_X509 failed` (client, before ClientHello)

Message appears in client TLS proxy log right after TCP connect; zero hits in
the entire 469 MB pre-regression log. Game's OpenSSL 1.0.0 refuses to parse
the new leaf slot certs during TLS setup; aborts before any TLS bytes.

| Test exe | leaf1 | leaf2 | root | Result |
|---|---|---|---|---|
| newpatch (broken) | new | new | new | FAIL (PEM_read_bio) |
| hybrid_leaf1 | **new** | dbgpatch | dbgpatch | FAIL (×2) |
| hybrid_leaf2 | dbgpatch | **new** | dbgpatch | FAIL (×7) |
| hybrid_bothleaves | **dbgpatch** | **dbgpatch** | new | **0 PEM failures** |

**Conclusion:** each new-form leaf independently breaks OpenSSL 1.0.0's
PEM_read_bio_X509. Both leaf slots must be in working (dbgpatch) form.

### Failure B — `TLS ALERT decrypt_error(51)` (client rejects server cert)

After Failure A fixed, TLS reaches ServerHello + server Certificate, then the
client immediately sends `decrypt_error(51)`. No ClientKeyExchange.

- exe root slot PEM is **byte-identical** to server ca.pem (verified True)
- both serial 0xDF3422A2551D2C72, key modulus head EDD5F781C295AF6C
- hybrid_bothleaves (new root + dbgpatch leaves): 0 PEM, still decrypt_error
- hybrid_leaves+root (dbgpatch root + dbgpatch leaves): 0 PEM, still decrypt_error

**Open question:** why reject a server cert that chains to the trust anchor?
Untested hypotheses:
1. Post-handshake identity check comparing server's presented cert vs embedded
   leaf slots (CONTINUE_HERE.md documents this). Server presents
   leaf1_slot.pem (root-signed *.thqconnect.com, serial 0x4F873196); exe leaf1
   slot (dbgpatch form) is Root CA - Login (serial 0x5A11C0DE) → mismatch.
2. Server may need to present leaf1_slot.pem + ca.pem concatenated as chain;
   OpenSSL 1.0.0 may require the full chain in the handshake.

---

## 3. Certificate set comparison

### 3a. Game exe leaf/root slots

| Slot | dbgpatch (WORKING, Aug) | newpatch (BROKEN, Oct 16) |
|---|---|---|
| leaf1 @0xed76b8 (870B) | 829B, **self-signed** CN=THQ Connect Root CA - Login, serial 0x5A11C0DE, **v3 WITH extensions** (BasicConstraints CA + SKID) | 797B, **root-signed** CN=*.thqconnect.com, serial 0x4F873196, **v3 NO extensions** |
| leaf2 @0xed7a20 (860B) | 793B, **self-signed copy of root**, serial 0xC72, v3 WITH extensions | 793B, **root-signed** CN=*.thqconnect.com, serial 0x4EB9B61D, v3 NO extensions |
| root @0xf712d8 (1248B) | 793B, **old root key**, serial 0xC72, v3 (BasicConstraints + AKID) | 793B, **new root key**, serial 0xC72, v3 (BasicConstraints + AKID) |
| clientcert @0xf717c0 (898B) | 894B, serial 0x3, no extensions | 894B, serial 0x3, no extensions |
| clientkey @0xf71b48 (886B) | 884B PKCS#1 | 884B PKCS#1 |

Key structural difference: dbgpatch leaf1 is **self-signed with extensions**;
newpatch leaf1 is **CA-signed without extensions**. Same pattern for leaf2.

### 3b. Server cert sets

| Cert | git server (WORKING, Aug) | root server (BROKEN, Oct 16) |
|---|---|---|
| ca.pem | serial 0xC72, key **DCF01AC3...** (OLD) | serial 0xC72, key **EDD5F781...** (NEW) |
| leaf1_slot.pem | serial 0x4F873196, signed by OLD ca | serial 0x4F873196, signed by NEW ca |
| leaf2_slot.pem | serial 0x4EB9B61D, signed by OLD ca | serial 0x4EB9B61D, signed by NEW ca |
| client_local_ca.pem | serial 0x2, OLD key | serial 0x2, NEW key |
| ds2_client.crt | serial 0x3, OLD key | serial 0x3, NEW key |

**Root CA serial is the same but keys are completely different** — cross-signing
fails both ways (git ca.pem does NOT sign root leaf1_slot.pem and vice versa).

### 3c. Cross-checks verified

- exe root slot PEM == root server ca.pem PEM: **True** (newpatch root + new ca)
- server ca.pem signs root leaf1_slot.pem: **OK**
- server client_local_ca.pem signs exe clientcert: **OK** (serial 0x3 pairs match)
- All slots parse under OpenSSL 3.5 and 1.1.1k — failure is specific to
  the game's **OpenSSL 1.0.0**

---

## 4. Files and their roles

| File | Role |
|---|---|
| `Client/debug/Darksiders2.exe.original` | pristine exe, SHA-256 39FED141... |
| `Client/debug/Darksiders2.exe.patched` | WORKING patched exe (Aug-era), SHA-256 1A858521... |
| `Client/out/Darksiders2.exe` | newpatch (broken), SHA-256 466A9113... |
| `Client/out/hybrid_*.exe` | bisect hybrids (see table above) |
| `git/Darksiders2Server/Server/server/certs/` | WORKING server cert set (Aug 23-28) |
| `server/certs/` (root) | BROKEN server cert set (Oct 16 re-mint) |
| `git/Darksiders2Server/cert-tools/out/` | cert generation output (Oct 16) |

---

## 5. Current state — RESOLVED (2026-10-17 19:00 session)

**Working combination confirmed in-game:**

| Component | Source | Era |
|---|---|---|
| Game exe | `git\Darksiders2Server\Client\debug\Darksiders2.exe.patched` (dbgpatch) | Aug 28 |
| Server certs | `ROOT\server2\Server\server\certs\` (copy of git server certs) | Aug 23-28 |

Evidence from `server2\Server\server\logs\requests.log` (session 19:00:43-19:03:57):
- `>> TLS handshake OK ... -> local:443 cipher=('ECDHE-RSA-AES256-SHA', 'TLSv1.0', 256) peer_cert=/countryName=US...` — mutual TLS accepted (client cert verified against Aug client_local_ca.pem)
- `>> TLS handshake OK ... -> local:8443` — cloud port TLS OK
- Full flow served: platformLogin → ssoStatus → getNews → cloud execute GET,GET (TomeGift+GameToWeb) → POST 2849 bytes (gift exchange body)
- **No Windows root CA store entry needed** — cert chain validates purely via exe-embedded root slot (install_ca.ps1 removal confirmed unnecessary)

**Conclusion:** the exe patching logic (patch_exe.py) is correct — it faithfully
writes cert material into slots. The regression was entirely in the **cert
material generated on Oct 16** by the cert-tools run: different root key despite
same serial 0xC72, and extension-less CA-signed leaves that OpenSSL 1.0.0
refuses to parse (PEM_read_bio_X509). The dbgpatch exe embeds the Aug 28 cert
material whose leaf1_slot.pem is served byte-exactly by the server — the
identity/pin match designed on Aug 28 works as intended.

---

## 6. Next-step options

1. **Full-chain test**: restart server with leaf1_slot.pem + ca.pem concatenated as cert chain (server supports this via config)
2. **Identity hypothesis**: make exe leaf1 slot contain exactly what the server presents (splice server leaf1_slot.pem bytes into exe leaf1 slot)
3. **Retrace Aug 23-28** tool executions by script modified-date (Step 2)


---

## 7. Aug 23-28 tool provenance (Step 2 assessment)

Tools by modified date during the original working-patch window:

| Date | Tool | Significance |
|---|---|---|
| Aug 23 02:52 | build_certs.py (last modified) | initial CA + server cert generation |
| Aug 23 03:06 | test_tls10.py | TLS 1.0 handshake test |
| Aug 27 19:12 | build_client_kit.py (last modified) | client cert kit (CN=DS2); root server ds2_client.csr Aug 27 19:06, test_v1.crt Aug 27 19:08 |
| Aug 28 14:39 | extract_leaves.py -> leaf1_orig.der + leaf2_orig.der | extracted original leaf certs from exe |
| Aug 28 14:56 | clone_leaf.py (last modified) | self-signed leaf clones; git server leaf*_clone.* dated Aug 28 15:03 |
| Aug 28 15:45 | mint_leaf_ca.py (last modified) | root-signed leaf identity certs; git server leaf*_ca.* dated Aug 28 16:16 |
| Aug 28 16:13 | mint_root2.py (last modified) | re-minted root with SHORT DN (serial 0xC72); git server ca.pem dated Aug 28 16:16 |
| Aug 28 16:21 | (patch_exe run) | git server leaf*_slot.pem dated Aug 28 16:21 |

**Key insight:** git server certs dated Aug 28 16:16-16:21 were produced by
EXACTLY this chain: extract_leaves -> clone_leaf -> mint_leaf_ca -> mint_root2
-> patch_exe. The Oct 16 run executed the same chain BUT in a different
OpenSSL/cryptography environment (cert-tools copy vs original root tools),
which likely explains why Oct 16 mint_root2 produced a different root KEY

---

## 8. Missing link FOUND (2026-10-17 evening experiments)

**Experiment A** — pristine original + Aug git certs via current patch_exe.py:
diff vs dbgpatch = 1522 bytes in 3 regions (leaf1 slot, leaf2 slot, 4-byte
dword). Root CA / client cert / client key / GDS rewrite all byte-identical.

**Key discovery:** dbgpatch does NOT contain leaf1_ca/leaf2_ca (root-signed
*.thqconnect.com, extension-less). Its slots hold:
- leaf1 @0xed76b8: **ca_login.pem** (self-signed "THQ Connect Root CA - Login",
  serial 0x5A11C0DE, v3 WITH BasicConstraints+SKID)
- leaf2 @0xed7a20: **a copy of ca.pem** (root CA cert itself, serial 0xC72)

The server serves leaf1_slot.pem (= leaf1_ca, root-signed identity) over the
wire; the exe leaf slots only need certs OpenSSL 1.0.0 can LOAD locally. The
extension-less leaf_ca form (Aug AND Oct — structurally identical, NULL params
present, verified) breaks the game's PEM_read_bio_X509 when embedded.

**Experiment B** — patcher + certs-b (leaf1_ca.pem:=ca_login.pem,
leaf2_ca.pem:=ca.pem): differs from dbgpatch by only **112 bytes**:
- leaf1/leaf2 slot padding (patcher: all newlines; dbgpatch: one \n + zeros)
- **int32 dword @0xed7d84: -50 (0xCEFFFFFF) vs 0** — an Aug-tooling step our
  patcher never did. Sits right after leaf2 slot, part of a -50/-49/-48 table.

**Experiment C** — expB + padding normalization + dword zero = **BYTE-IDENTICAL
to dbgpatch** (SHA-256 1A858521...CB548 verified equal). The working exe is now
fully reproduced from pristine original + Aug certs + 5 known byte fixups.

### Deliverables in Client\out\
| File | Status |
|---|---|
| expA_Darksiders2.exe | original + Aug certs, leaf_ca in slots (expected to fail PEM) |
| Darksiders2.exe (=expB) | dbgpatch replica minus padding+dword (112B) — **test target** |
| expC_Darksiders2.exe | byte-identical clone of working dbgpatch (hash-verified) |

### Test plan — RESOLVED
1. ~~Deploy expB (out\Darksiders2.exe) against server2 (Aug certs)~~
   **RESULT 2026-10-17: expB SUCCESS in-game** — TLS established, login OK,
   inbox opened, gifts rendered. => slot padding style and the -50 dword
   @0xed7d84 are IRRELEVANT. The single fix was the leaf-slot cert
   arrangement (ca_login in leaf1, root-CA copy in leaf2) within one
   coherent cert set.

### Final verdict
- **Root cause of the 2026-10-16 regression:** patch_exe.py embedded
  leaf_ca certs (root-signed *.thqconnect.com, extension-less) into the exe
  leaf1/leaf2 slots. The game's OpenSSL 1.0.0 refuses to PEM_read_bio_X509
  these in-slot -> TLS aborted before ClientHello.
- **Fix:** leaf slots must contain certs WITH v3 extensions. Proven pair:
  ca_login (self-signed "Root CA - Login") + root CA copy. Wire identity
  stays the root-signed leaf_ca (served by the server, chain-verifies
  against the exe root slot).
- **Reminting was never the issue:** Aug and Oct leaf_cas are structurally
  identical (NULL params present, same sizes); both fail in-slot, both parse
  fine over the wire.
- See PATCHING_GUIDE.md (this directory) for the reproducible workflow.
(same serial 0xC72, new key EDD5F781) and Oct 16 mint_leaf_ca produced leaves
WITHOUT extensions while the Aug 28 working leaves HAVE them.

**Recommendation for Step 2:** rather than further blind bisecting, the fastest
fix path may be:
1. Copy git server certs (Aug 23-28) back to root server/certs/
2. Use dbgpatch exe (already proven working) OR rebuild exe from those exact certs
3. Verify TLS works, then investigate why the Oct 16 cert-tools run produced
   structurally different (extension-less) leaves — likely a library version
   difference between cert-tools and root tools environments
