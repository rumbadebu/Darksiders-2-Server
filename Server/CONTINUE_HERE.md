## 2026-08-27 EVENING — MUTUAL-TLS CONFIRMED AND KIT DEPLOYED

Breakthrough experiment: server sent TLS CertificateRequest (CERT_OPTIONAL,
anchor=server/certs/client_local_ca.pem) -> game's silence after handshake
was because it waits to be ASKED for its client cert (classic mTLS client).

- client cert slot @0xf717c0 = 898 bytes (was '912' estimate), CN=DS2 leaf
- client key slot  @0xf71b48 = 886 bytes, PKCS#1 RSA-1024
- exe also embeds original 'THQ Connect Client CA' cert (left as-is; same DN
  as our stand-in CA, OpenSSL resolves chain via trust-store candidate)

tools/build_client_kit.py (pip install cryptography; OpenSSL 3.5 CANNOT do
this job: x509 -req auto-adds SKID/AKID -> v3 1006B > slot; genrsa emits
PKCS#8; SHA1 signing blocked):
  mints client_local_ca (DN = exact original issuer DN) +
  ds2_client.crt/.key (v1, RSA-1024, PKCS#1, same DNs as original;
  cert signed SHA256 - only OUR server verifies it, game just forwards)
  rewrap() narrows PEM lines to fit slots: cert 895<=898, key 885<=886.
patch_exe.py now also installs cert+key into those slots (KEY_RE regex).

VALIDATED with s_client (-cipher 'ALL:@SECLEVEL=0' -tls1; OpenSSL 3.5 client
needs SECLEVEL=0 for RSA-1024, the game's OpenSSL 1.0 does not):
  server log: handshake OK ... peer_cert=/C=US/.../CN=DS2   <- mTLS WORKS
Server config: use_client_ca_cert=false (Root-CA-signed server.crt),
verify_client_cert=true (CertificateRequest + client_local_ca anchor).
Steam exe redeployed (hash 3E5E80FF = patched+clientkit).

NEXT: launch game -> if it now loads our key+cert from patched slots and
presents them, handshake completes mTLS-style; watch server log peer_cert
and post-handshake traffic (probe still captures first app bytes).

### 2026-08-28 (late) - mTLS WORKS IN-GAME; server-first greeting experiment live
- 20:22 game run: handshake OK, game presented OUR re-minted DS2 cert
  (proxy log: serial 03 DER + 130B CertificateVerify). Client kit proven.
- BUT: game sent ZERO app bytes on every channel, closed TLS ~1s, then
  getaddrinfo -> 8443 PLAIN connect -> read once (~1ms) -> close -> retry
  every ~14s. Conclusion: SERVER SPEAKS FIRST (greeting protocol).
- NOTE: 08-23 "working HTTP flow" in requests.log was OUR Invoke-WebRequest
  tests, not the game. The game has never sent an app request. Do not trust.
- Exe evidence for greeting format: sscanf "[%[^]]]" (bracketed tokens),
  "_defaults"/"_shared" strings, "thqc-gds|https|/rpc|/gds/", 60x u32 jump
  table (0..11) near app/x-gds-rpc -> maybe typed binary messages.
- Server: GREETINGS rotation (11 candidates, see ds2_server.py ~line 364);
  8443 greets BEFORE peek (wins the 1ms race); 443 greets right after TLS
  handshake. Probe fixed (select-based; SSLSocket timeouts are fatal).
  TLS/plain detection = bounded 250ms non-blocking peek poll.
- Verified 21:03 (pid 37804): 3 probes -> 3 different instant greetings;
  s_client mTLS OK + got "[thqc-gds]" over TLS; requests.log healthy.
  (Earlier "logs only to stdout.txt" was a stale-process artifact.)
- Steam dir: binkw32.dll=245KB proxy (decorated EAT), binkw32_real.dll=226KB
  original. WARNING: never deploy raw cargo target output (undecorated EAT
  -> silent loader crash, hash 588D30C1 trap; use build_rust.bat only).
- NEXT: launch game, let retry cycle consume candidates (~14s each, ~2.5min
  for full list). A "hit" = game replies after a greeting -> log shows
  "tls recv" / plain capture hex -> decode that framing and implement.

## ROUTE B STATE (2026-08-28)
- mTLS WORKS IN-GAME: client kit (CN=DS2, serial 03) presented+verified. 443 = network CONFIG channel.
- Config grammar (RE-decoded): INI - [section] via sscanf([%[^]]]), # comment, key=value split on FIRST =, '~' line TERMINATES whole stream, empty skipped. Special sections _defaults + _shared. Applier 0xdb860c runs parser TWICE (defaults pass + live pass). Network consumer 0x403ebc -> 0xdb3219 (build cfg obj, parse+apply, fire ~97 listeners, delete buffer).
- Game ALSO reads LOCAL cfg: ./config/Darksiders2{485530A1-6E65-4316-AD74-52925309045A}.cfg (builder 0x403f88: defaults PC.UsingSteam=true, Language/Game=en, DeveloperMode=Debug, ScreenWidth/Height, Fullscreen, VSync, Renderer).
- Other keys seen: THQConnect.LocalizationLanguage, DemoMode, ShowTitleScreen, TelemetryEnabled, Telemetry, TelemetryTag (values true/DEV/Release). GDS setter 0xdb1570 <- TelemetryManager-like provider + 'Release' tag -> globals 0x18484d8/dc -> URL builder: host+'://'+...+'/gds/'+[0x18484d8]+...+'/rpc'.
- 8443: game resolves thqc-darksiders2-pc.thqconnect.com (2nd, unpatched name) -> PLAIN tcp connect, closes in ~1ms WITHOUT reading greeting, retries 14-21s = reachability-probe-like. Config channel is 443/TLS only (legislation instinct correct).
- 23:59 session: game READ our [thqc-gds] greeting then close_notify'd after ~1s = doc incomplete (no terminator) or wrong framing.
- NEXT: (1) run tools/re_digest.py for full key vocab; (2) deploy server/config_sentinel.cfg as Steam .\config\Darksiders2{485530A1-6E65-4316-AD74-52925309045A}.cfg sentinel oracle; (3) 443 greeting = COMPLETE doc ending with '~'; watch proxy getaddrinfo for s1..s10 sentinels.

## 2026-08-28: identity-check emulation staged (awaiting game run)
- Original leaf certs are SELF-SIGNED *.thqconnect.com wildcards (v3, sha1, RSA-1024, no ext):
  leaf1 serial 4F873196 (2012, C=CA/ST=BC/L=Vancouver/O=THQ Inc./OU=THQ Inc.),
  leaf2 serial 4EB9B61D (2011, .../OU=Connect/O=THQ). => game cannot chain-verify;
  must identity-check (byte pin vs embedded slots / DN compare / issuer==subject).
- tools/clone_leaf.py: field-identical SELF-SIGNED clones (sha256 sig, validity to 2040),
  PEM 867<=870 and 858<=860 slots, same serials/DNs. cryptography ban on sha1 bypassed
  via sha256 (game OpenSSL 1.0.0 verifies sha256 fine).
- patch_exe.py: leaf slots now serial-matched to clones (not server.crt); verify step
  rewritten: original leaf BLOCK BYTES must vanish (old 'Vancouver' heuristic false-
  positive on clones sharing the DN). verification: OK; deployed to Steam (hash match).
- server: serve_leaf_clone=1 -> presents leaf1_clone (bug fixed: clone selection was
  being overwritten back to server.crt). s_client proved presented cert == self-signed
  full-DN leaf1 clone. mTLS + doc_full_tilde_close greeting confirmed delivering.
- NEXT: launch game, compare proxy log: does the client FINALLY recv() app data /
  reply to the config document? If still instant close => identity check may be exact-
  byte pin against a DIFFERENT slot (leaf2?) or involve the root slot; try serving
  leaf2 clone next (config serve_leaf_clone=2), then raw byte-pin analysis.

## 2026-08-28 16:28 - HYBRID IDENTITY CERT ARMED (awaiting game run)
- New root minted (short DN `C=US,O=THQ Inc.,CN=THQ Connect Root CA`, RSA-1024); legacy root backed up.
- Identity clones minted: leaf1_ca 798/870B + leaf2_ca 794/860B - ORIGINAL subject DNs + serials, chained to new root.
- Exe re-patched: root slot = new root, leaf slots = byte-exact `leaf{n}_slot.pem` (served file == embedded bytes).
- Server serves leaf1_slot.pem (config serve_leaf_clone=1); s_client: subject `C=CA,...,CN=*.thqconnect.com`, issuer=new root, **Verify return code: 0 (ok)**.
- Steam exe == patched exe (hash match True); proxy log cleared; game closed.
- HYPOTHESIS UNDER TEST: chain-verify runs vs Root-CA slot (proven: self-signed leaf -> alert 48; Root-signed -> mTLS completes) AND a post-handshake identity check compares presented cert vs embedded leaf slot bytes/DN. Hybrid cert satisfies both.
- NEXT RUN ORACLE: server log `tls recv N bytes` (game read doc -> DECODE PROTOCOL) vs `peer closed cleanly` (identity still failing -> serve leaf2 clone, then byte-pin analysis). Config-applied oracle: proxy log getaddrinfo to sentinel hosts t1..t10 (see server/config_sentinel.cfg).

## SESSION LOG 2026-08-29 (cont.) - anti-loopback FALSIFIED; connect bypass armed
- Test: getaddrinfo rewrite GDS host -> 127.0.0.2 (proxy) + 8443 greeting silenced (server).
- Result: IDENTICAL behavior to 127.0.0.1 - connect -> WSAEWOULDBLOCK -> closesocket 1ms later, zero bytes.
  => Loopback-IP check hypothesis DEAD.
- Route B RE of connect machinery (Darksiders2.exe.orig):
  * SINGLE port-8443 immediate @0xb02ce0 (RVA 0x702ce0) = GDS service-desc ctor {hostname 0x12d9a28, port 8443};
    sibling fn formats endpoint strings.
  * SINGLE connect callsite @0xb19a3a (WSOCK32 connect ord4 IAT 0x1162650).
  * connect -> WSAEWOULDBLOCK -> poll 0xb193f0 (max-1000ms chunks) built on hand-rolled select wrapper
    0xb280d0 -> WSOCK32 select IAT 0x1162644 (writefds only). select RET/err checked; on <1ms non-writable
    -> state check 0xb19770 -> closesocket (0xb19b2c).
  * NULEDUKE THEORY: THQ hand-rolled fd_set/select misbehaves with high socket handles (>FD_SETSIZE 64:
    our handles 0x73c/0x8bd0...) -> select never reports writable -> game closes before ever sending.
  * Modern-Windows regression explains why 8443 never sends even against real infra.
- INTERVENTION (proxy rust lib.rs):
i  * h_connect: for port==8443 and real r!=0 -> FORCE return 0 (skip broken select poll).
i  * NEW h_select hook (WS2_32 + WSOCK32 select, log fd_sets + RET + lasterr) for observation.
i  * getaddrinfo->127.0.0.2 rewrite kept (harmless).
i- Deployed to Steam (FORCE-OK + h_select markers present, EAT 77/78). Server unchanged (17:10 build:
i  443 mTLS greeting doc, 8443 silenced).
i- NEXT: user relaunches; if game SENDS on 8443 -> we finally see the GDS request; if still closes,
  select RET lines will say why.
## SESSION LOG 2026-08-29 Part 2 - connect bypass FAILED; SO_ERROR gate identified
- Run: connect(8443) forced -> 0 (skip select-poll). Game STILL closes 1ms later, zero bytes.
  => connect return value is NOT the gate. (No select hook lines fired - game never polled.)
- Route B re-disas of state-check 0xb19770 (the real gate, called AFTER connect):
    SleepEx(0,0); getsockopt(SOCKET, SOL_SOCKET 0xffff, SO_ERROR 0x1007, &err, &len)
    success iff err==0 || err==0x2748(WSAEISCONN 10056); else al=0 -> closesocket
  => game does a getsockopt(SO_ERROR) health-check on the socket after connect.
  On modern Windows a non-blocking connect can still be pending -> SO_ERROR nonzero
  -> game closes before ever sending. Explains ALL prior 8443 closes (and the 1ms timing).
- INTERVENTION (proxy rust lib.rs v2):
  * keep: connect FORCE-0 for 8443
  * NEW: h_getsockopt hooks (WS2_32 + WSOCK32): log real SO_ERROR, and FORCE it to 0
        for the GDS socket (port 8443) so the health-check passes.
  * NEW: h_select hook already in (observes poll if ever reached).
- (Also deleted stray 'Darkers 2 server' folder from a typo path.)
- Deployed to Steam: SO_ERROR FORCE marker present, EAT 77/78. Log cleared.
- NEXT: relaunch; expect getsockopt SO_ERROR lines; if FORCE->0 fires the game should
  proceed to send its GDS request on 8443 -> finally see the app protocol bytes.
## 2026-08-30 SESSION - config-inject crash + safe proxy redeploy

### Crash root cause (game would not start, WER APPCRASH c0000005 @ Darksiders2.exe+0x94f2b)
- The proxy's in-process `inject_config` called RVA 0xdb3219 as a "config applier".
  Capstone RE proves 0xdb3219 is NOT a function entry: no prologue; it is
  MID-FUNCTION inside a UTF-8 string-scan loop (it `lea eax,[ebp-4]; call 0x9b2ec2`
  where 0x9b2ec2 is a UTF-8 multibyte head-byte classifier). The call trashed
  the config singleton / heap; the game AV'd ~1s later at 0x94f2b.
  => The shared_ptr<std::string> applier hypothesis was WRONG. Never call it.
- FIX: inject_config is now gated behind env DS2_TLS_INJECT=1 and OFF by default.
  Safe proxy rebuilt & redeployed to Steam dir (77/78 decorated EAT verified;
  bin timestamp 30.08 20:13). Game boots again.

### Discoveries this session
- exe file-offset 16192020: GDS descriptor blob `thqc-gds|https|/rpc|/gds/`
  immediately followed by `-----BEGIN CERTIFICATE-----` (embedded THQ leaf).
- NO INI-style config doc literal exists in the exe: [_defaults], [_shared],
  [thqc-gds], Scheme=, Host=, Port= ALL absent. The game uses code-defaults +
  network bootstrap; the `~`-line doc format/terminator was RE-derived.
- Config-file oracle (.\config\Darksiders2{GUID}.cfg, sentinels s1..s10) is
  NOT honored: the game still connects to 127.0.0.1:8443 via the hosts-redirected
  default (gds.thqconnect.com), never getaddrinfo()s any sentinel.

### Server current state (restarted 20:18, local-port logging added)
- 443: 5s TLS peek -> mTLS (game presents CN=DS2) -> doc-greeting rotation
  (doc_full_tilde_close/keep etc). Game closes CLEANLY right after reading doc
  in all 08-29 runs = it consumed the bytes; then moves to 8443 pairs (silent).
- 8443: greeting DISABLED (anti-loopback experiment) - server silent, peeks 5s.
- handler log now includes the LOCAL port (from->:port tls=).

### Next run to run
Launch game (~10 min window, its 14s retry cycle): with 5s peek on both ports,
late ClientHellos (measured ~1.1s) are caught on BOTH 443 and 8443 -> expect a
TLS handshake per retry, each consuming the next doc candidate. Read:
  1) server\logs\requests.log : TLS handshake count, which doc, local ports.
  2) E:\SteamLibrary\...\ds2_tls.log : getaddrinfo() sentinel hits + connect targets.
## 2026-08-30 SESSION (2) - ROOT CAUSE found: proxy hacks killed GDS

### Proxy log (Steam-dir ds2_tls.log) revealed the REAL client-side story
- Startup flow: connect 127.0.0.1:443 -> full mTLS (ClientHello, ServerHello
  c014, cert, ServerHelloDone, client cert 909B, Finished, close). Config
  bootstrap WORKS.
- THEN: getaddrinfo(thqc-darksiders2-pc.thqconnect.com, 8443) -> REWRITE
  127.0.0.2 -> connect 127.0.0.2:8443 (FAILED, real at OS level) ->
  FORCE-OK returns 0 -> game sits on a socket that was NEVER connected.
  => It NEVER SENDS any GDS request. GDS dead by construction.

### The two proxy hacks (now REMOVED)
1. getaddrinfo rewrite GDS host -> 127.0.0.2 (anti-loopback experiment).
2. h_connect FORCE-OK for port 8443 when real connect fails.
Both were armed 2026-08-29 during the (FALSIFIED) anti-loopback test and
left in place. Combined effect: phantom connected socket, no traffic.

### FIX (clean production behavior)
- h_getaddrinfo: rewrite block REMOVED => hosts file resolves
  thqc-darksiders2-pc.thqconnect.com -> 127.0.0.1 (real, server listens 0.0.0.0).
- h_connect: FORCE-OK removed => real connect semantics (game already
  proceeds on WSAEWOULDBLOCK like it did for 443).
- Rebuilt+decorated 77/78 EAT. Root binkw32.dll 30.08 21:08 (hacks gone).
  Steam copy blocked until game closes (PID 45208 holds DLL).

### User finding: test ONLY startup
- Wireshark on mail fetch: ZERO network activity. Mail is passive/reads an
  already-established connection. Game does its real handshaking at startup:
  443 TLS config bootstrap + GDS channel (thqc-darksiders2-pc:8443).
- So the correct test loop is: launch game fresh -> watch startup only -
  not mail clicks.

## 2026-08-31 - server accept-loop NameError found & fixed
- Symptom: TCP connect succeeded on 80/443/8443 but NO handler log line and requests hung forever.
- Cause: greet_here referenced at line ~868 but never defined (lost in greeting restructure).
  get_request() raises NameError -> that server's serve_forever thread dies silently
  (threading excepthook goes to hidden stderr) -> socket stays LISTENING (kernel backlog)
  but nobody accept()s. Health checks time out with zero log output.
- Lesson: py_compile does NOT catch NameError; always health-check the server after edits.
- Fix: greetings now config-driven: greet_8443 / greet_tls keys (both absent = OFF ->
  fully silent server for the 127.0.0.1-rewrite single-variable test). Banner prints flags.
- Current state: exe+kit patched, proxy CA8723E6 (GDS->127.0.0.1 rewrite, honest labels),
  server silent on 8443, leaf1_slot byte-exact cert, mTLS on, proxy log cleared.

## 2026-08-31 07:32 - GDS wiring complete, TEST RUN ARMED
- RE conclusion: GDS = libcurl HTTPS POST (needs HTTP 200 + Content-Type: app/x-gds-rpc + Content-Length body); URL built from runtime config globals fed by the 443 bootstrap doc ([section]/key=value/~ parser, decoded at 0xDB8286).
- config.json: greet_tls=true (443 delivers doc right after mTLS), greet_8443=false (8443 is client-first libcurl TLS - a server greeting would corrupt it).
- TLS_GREETINGS: doc_realhost_tilde_close/keep added FIRST (endpoints=gds.thqconnect.com -> hosts -> 127.0.0.1). Consumption oracle = game dials 127.0.0.1:8443 with TLS ClientHello and POSTs app/x-gds-rpc.
- Proxy deployed (718E87FF): honest GDS rewrite thqc-darksiders2-pc.thqconnect.com -> 127.0.0.1; hacks (127.0.0.2/FORCE-OK) removed.
- NEXT LAUNCH: watch requests.log for 'greeting[doc_realhost...]' then either 'tls probe/recv' on 443 (game reply) OR a new TLS handshake on 8443 + raw POST capture = GDS protocol in hand.

## 2026-08-31 - decisive test armed
- PROXY D1BDA1B8 (Steam=root): sync-connect fix in h_connect - when real connect returns
  -1/WSAEWOULDBLOCK, hook waits for writability (select, instant on loopback), verifies
  SO_ERROR==0, returns 0. The 8443 client treats -1 as instant fail (connect->close in 1ms
  liveness probes); now it gets a genuinely-connected socket synchronously.
- SERVER 07:53: PIN_TLS_GREETING=True -> every 443 mTLS launch gets doc_realhost_tilde_close
  (candidate 0, PROVEN consumed 07:36: clean 443 close + GDS client woke up 3s later).
  Rotation suspended until 8443 is decoded; set False to resume.
- EXPECT on next launch: mTLS OK on 443 -> doc sent -> game dials 8443 -> connect now
  succeeds synchronously -> game proceeds to TLS/POST -> raw capture of the GDS request
  (HTTP 200 + app/x-gds-rpc + Content-Length body is what the validator 0xDB2B42 wants).

## 2026-08-31 10:15 - STATE: ready for decisive doc+greeting run
- Server restarted (pid 46752, 10:15:29): greet_tls=True (pinned doc_realhost_tilde_close) +
  greet_8443=True (own rotation, 450ms peek before greeting), mutual-TLS on, HTTP 200 health OK.
- NEW server logging: every TLS handshake line now carries local PORT (443/8443) + first-record
  fingerprint (game ClientHello ~ \x16\x03\x01\x00c9 = 201-byte record; Chromium-style preconnect
  noise = 300-517 bytes). 09:39 'TLS FAILED 10053' x4 diagnosed as NOISE: no proxy banner at that
  time -> game was not running; other local apps probing newly-listening 443.
- Proxy deployed: D1BDA1B8 (sync-connect fix: WSAEWOULDBLOCK -> wait writable -> SO_ERROR==0 ->
  return 0). Verified Steam=root. Exe 3EDFCC78 (patched, client kit).
- Established facts: 443 mTLS completes (game presents our CN=DS2 cert); doc consumed (clean 443
  close + GDS client wakes 3s later); 8443 connects now succeed + socket held ~800ms waiting
  server-first; game sends ZERO bytes until server greets. This run: 8443 greeting finally lands
  on a live listening socket.
- EXPECT on hit: server log greeting[...] sent on 8443, then raw-capture hex of game reply (or
  TLS ClientHello if 8443 is TLS) -> decode GDS framing, implement responder (HTTP 200 +
  app/x-gds-rpc + Content-Length body per validator @0xDB2B42).
## 2026-08-31 EVENING - RESEARCH: what the game expects / TLS alert decoded
### The TLS alert is NOT a rejection - it is a decrypted close_notify
- Client proxy log on 443 shows `!!! TLS ALERT [c2s]: ?alert(189) level=77` fired
  ONCE, at 16:04:28.308, a 37-byte send = a SINGLE TLS record 15 03 01 00 20 + 32
  ciphertext bytes. Type byte 0x15 (alert) is plaintext; the level/desc fields are
  INSIDE the encrypted payload (post-ChangeCipherSpec), so 0x4D/0xBD are random AES
  CBC bytes of a real close_notify (2B plaintext + 20B SHA1 MAC + padding = 32B).
- Server-side counterpart: `greeting[doc_realhost_tilde_close]: peer closed cleanly
  after doc` -> the server read EOF right after sending the doc. Clean TLS shutdown.
- NO alert 48 (unknown_ca) anywhere in the 16:04 session -> our patched-root chain
  IS trusted. The old unknown_ca is fully resolved.
### What the 16:04 session ACTUALLY looked like (byte-level, both sides)
- 443: connect -> SYNC-FORCED OK -> 206B ClientHello (TLS1.0) -> full mTLS:
  ServerHello c014 ECDHE-RSA-AES256-SHA, Cert, ServerKeyExchange,
  CertificateRequest (cert types 1,2,0x40, no CA DNs), ServerHelloDone ->
  client Cert/CKE/CertVerify/CCS/Finished 909B -> server CCS+Finished -> client
  sends the encrypted ALERT + closes. The client NEVER recv'd our 323B doc
  (zero h_recv between server-Finished and close) and NEVER wrote app data.
- 8443: getaddrinfo(thqc-darksiders2-pc.thqconnect.com, 8443) -> rewritten to
  127.0.0.1 -> connect SYNC-FORCED OK -> closesocket 1ms later with NO
  send/recv/select/getsockopt logged on that socket. Two such probes, then
  permanent silence (game process stayed alive 12+ min, scan passes continue).
### Consequences (correcting earlier conclusions)
1. "doc consumed / candidate 0 PROVEN" was over-optimistic: the 8443 dials happen
   even with a SILENT server (08-24/08-29 runs), so they are not proof the doc
   was read. The game closes its 443 TLS before reading anything the server sends.
2. The SO_ERROR gate (exe 0xb19770, WSOCK32 #7 getsockopt) is NOT the 8443 killer:
   it is hooked but NEVER fired on those sockets -> the closesocket is issued by
   game code that aborts before the health check. Both forced-0 (now) and -1
   (before) connects give the same 1ms close.
3. The game NEVER exchanges a single application byte on 443 or 8443 in any
   session, ever (all older "POST" lines in requests.log are the PowerShell
   test-suite User-Agent, not the game).
### What the game expects (RE evidence, all in Darksiders2.exe.orig)
- WorldwideLoginService state machine (strings VA 0x12da0xx, code ~VA 0xb02cxx):
  STATE_GetAuthentication / STATE_WaitForAuthentication -> then "*** Connecting
  to server: %s:%hu" (VA 0xb02cf0, port hardcoded 0x20fb=8443 @0xb02cdf) -> login.
- The FIRST-party (Steam) auth token is mandatory before ANY account call:
  "STATE_GetAuthentication - First party not initialized",
  "GenerateAuthToken - failed. invalid auth ticket handle is returned",
  "GetAuthToken - failed. You should call GenerateAuthToken",
  "The authtoken should not be empty for account and profile calls."
  (SteamAuthManager::GenerateAuthToken = Steam_GetAuthSessionTicket).
- Conclusion: the game sits at the auth-token gate; it has nothing to write, so
  every socket dies at the TLS/probe layer. The login UI flow ("Signing in to
  THQ.com") never starts either (no schannel ClientHello anywhere).
- Steam client launched the game via Play (console_log: ProcID 34228 16:04:23 ->
  16:16:31, appid 50650) but NO ticket-issuance records exist in Steam logs.
### ACTION TAKEN: steam_api observability added to the proxy (binkw32)
- tools/tls_proxy/rust/src/lib.rs now hooks (IAT, name-based, log-only):
  SteamAPI_Init (bool), SteamUser (ptr), SteamAPI_RunCallbacks (counter),
  SteamAPI_RegisterCallback/RegisterCallResult (ids), SteamAPI_Shutdown.
  Exe imports steam_api BY NAME (tools/dumpimports.py) so the existing
  name-matching patcher needs no address map - only the descriptor/dll_ok
  branch was extended.
- Built + deployed to the Steam game dir (binkw32.dll 257536 B, SHA
  D8448F2BFAFF7796E2D64F0251F42550606E31D016972F36E7FD7F2E39D74215).
  NOTE: build_rust.bat exits nonzero in PowerShell due to cargo stderr noise
  even though build+decorate succeed; copy manually if needed.
## NEXT RUN (one variable: the steam instrumentation)
1. Launch the game via Steam normally while the local server runs.
2. Read Steam-dir ds2_tls.log for the new `>> SteamAPI_Init/SteamUser/
   RunCallbacks/RegisterCallback` lines.
   a) Init=false or SteamUser=0 => Steam first-party dead; make sure Steam is
      running & the game launches via Play, or patch SteamAuthManager to fake a
      ticket (nuclear).
   b) Init=true, no RegisterCallback for the ticket response => the game never
      requests a ticket; dig the caller of GenerateAuthToken.
   c) Init=true + RegisterCallResult fires => ticket path alive; then the 443
      close-without-write has a DIFFERENT cause and the server-side doc/order
      work resumes.
3. New RE tools added this session: tools/grepticket.py, tools/xref_login.py,
   tools/dumpregion.py, tools/dumpimports.py, tools/pcap_dump.py.
## 2026-09-02 - NUL-mismatch bug found: steam hooks were NOT patched
## 2026-09-02 LATE - REAL root cause: import name-table window OOB (steam still 7)
- Even after the NUL-trim fix, runs at 22:55 (pid 34488) and 23:11 (pid 15856)
  still logged `main exe IAT: 7 thunk(s)` and no SteamAPI lines. The steam
  instrumentation was NEVER patched in.
- Root cause found by reading the proxy code: the name-based IAT matcher built
  the image slice as `from_raw_parts(base, val)` (i.e. [base, base+val)) and then
  read the C string at offset `val+2` - ALWAYS out of bounds (slice end = val),
  so cstr_at() returned None every time and name matching NEVER worked for any
  module. WS thunks only ever got patched through the ADDRESS-BASED amap
  fallback, which is why the proxy worked at all despite a dead name path.
- Exe imports steam_api.dll BY NAME (16 entries, verified via
  tools/check_steam_imports.py on the patched Steam exe: SteamAPI_UnregisterCallResult
  ... SteamAPI_RegisterCallback at OFT=0x10da164/IAT=0xd627a8). Steam is NOT a
  bound/ordinal import, so there is no address fallback — the name path is the
  ONLY way to hook it. Hence steam could never be patched until now.
- FIX (lib.rs ~line 1098): window at the ENTRY (`base.add(val)`), length 256,
  read cstr from offset 2 (u16 hint, then name). Verified in Python that all
  names come back clean.
- Rebuilt + deployed (binkw32.dll 257536 B, 23:17:41, repo==Steam SHA match).
  NEXT LAUNCH: banner should read `main exe IAT: 13 thunk(s)` and SteamAPI_*
  lines appear if the path runs.
- Tools added: tools/check_steam_imports.py (replays the proxy's fixed name
  read against the on-disk exe so the match can be verified WITHOUT launching
  the game).
- The 09-02 22:40 run (game pid kept alive) proved the steam instrumentation
  was compiled in but never redirected: session banner still said
  `main exe IAT: 7 thunk(s) redirected` and scan passes stayed `+0`.
- Root cause: name-based IAT match compared `eq_icase(fname, k.name)` where
  `fname` came from the exe import table (NO trailing NUL) but `k.name` is
  `b"SteamAPI_Init\0"` (trailing NUL for GetProcAddress). eq_ignore_ascii_case
  is length-sensitive -> steam thunks NEVER matched. The WS hooks worked only
  because they hit the address-based amap fallback (steam_api not in amap:
  not loaded at DllMain time, amap is OnceLock).
- FIX (lib.rs line ~1108): compare `&k.name[..k.name.len()-1]` instead of k.name.
- Rebuilt (exit 0), deployed to Steam dir by hand; hashes now match
  7EA3B3047D96303C83EFAA3DA7A7CB3F4278C21401D40604DAACCEFC1804A86D (repo + Steam,
  258048 B). build_rust.bat's own deploy step got silently skipped earlier
  because the game held the file while running.
- NEXT RUN A: expect banner `main exe IAT: 13 thunk(s)` (7 WS + 6 steam) and
  `>> SteamAPI_Init/SteamAPI_RunCallbacks/SteamUser/...` lines in ds2_tls.log.
  If RunCallbacks/Init missing there, Steam first-party is the gate and 443
  close-without-read is explained; we then patch SteamAuthManager or any
  fake-ticket layer.
- This run's server events (09-02 22:40) matched every prior one: 443 mTLS OK
  + doc + clean close, 8443 pairs tls=False + silence. No new info before the
  DLL fix.
## 2026-09-03 00:02 - STEAM FIRST-PARTY CONFIRMED ALIVE + cb163 response capture ARMED
### Run 09-02 23:24 (pid 50080) - hooks finally fired (13 thunks)
- `>> SteamAPI_RegisterCallback cb=1861268 iCallback=331` (at attach)
- `>> SteamAPI_Init -> true`
- `>> SteamUser -> 85b0220` (valid interface, repeated)
- `>> SteamAPI_RegisterCallback cb=10146bxx iCallbacks=1101/1102/1103` (SteamUtils/HTTP inboxes)
- `>> SteamAPI_RegisterCallResult cb=30073350 apiCall=24415f041d49cc90`
- `>> SteamAPI_RegisterCallResult cb=300733a8 apiCall=94cce2ead83d9f4c`
- `>> SteamAPI_RegisterCallback cb=2ffc4808 iCallback=1005`
- **`>> SteamAPI_RegisterCallback cb=102ed7e0 iCallback=163`** <- GetAuthSessionTicketResponse_t (the game DID register the ticket-response callback)
- Then the usual: connect 443 -> full mTLS -> doc -> close ("peer closed cleanly after doc"), 8443 pairs tls=False at :46/:47 and 23:30:10/11. NO app data anywhere, ever.
- CONCLUSION: Steam first-party is NOT the gate - the game initiates the auth-ticket flow (Registered 163) AND SteamAPI_Init=true + SteamUser present. The ticket response (163) NEVER FIRES in the log because the response is delivered by Steam directly to the game callback object's vtable.Run - we were only logging the reg ops, not the invocation.
- So the outstanding question narrows to: does Steam actually deliver a 163 callback with a ticket (eResult OK)? If it fires OK, the game HAS its token and the "closes before writing" is about the 443/8443 APP layer, not Steam. If it never fires or eResult!=OK, THAT is the silent gate.

### NEW INSTRUMENTATION (built + deployed this moment)
- tools/tls_proxy/rust/src/lib.rs: when the game registers iCallback=163, we patch
  the callback object's vtable[0] (Run) with an x86 trampoline that logs the raw
  8-byte response (field0/field1; decodes whichever is a small enum as EResult)
  then tail-jumps to the original Run. Non-invasive: Steam's dispatch calls the
  game's own CCallback::Run through the vtable slot, so the response data is
  captured at the exact point the game's handler would receive it.
  Trampoline bytes: mov eax,[esp+4]; push eax; call <log>; add esp,4;
  mov ecx,<cb>; jmp <orig> (32B static, VirtualProtect'd RX).
- Built (exit 0, 4.4 MB log), deployed to Steam dir: binkw32.dll 260096 B,
  SHA C659ED5C2AE1DE8EBFC7D9406FB0D591D1C9860A05E10830F61CFD6336FFB9D6 (repo==Steam).
## NEXT RUN
1. Launch via Steam; watch ds2_tls.log for:
   - `>> cb163 object=... vtable=... Run=...` then either
   - `>> cb163 *** GetAuthSessionTicketResponse eResult=... (m_hAuthTicket=...)` = Steam
     delivered the ticket to the game (then the close-before-write is NOT Steam's
     fault -> server/doc/order work resumes on 443/8443)
   - OR the cb163 line stays absent / shows `(null)` or unknown -> Steam's auth answer
     is the gate + implement a fake ticket / patch SteamAuthManager.
- Also 163 might be fired only after a user action (game must reach the login
  state). If the game sits on the title screen it may never call GetAuthSessionTicket.

### ALSO NOTES
- Order of fields in the response struct varies by steam_api.h rev (m_hAuthTicket vs
  m_eResult first); the logger prints both raw u32s and decodes the small-enum one.
- The earlier 08-31/09-02 runs are interpreted: game registers 163 then immediately
  does the 443 mTLS+dump; the 163 response only matters when the login state machine
  actually asks for a ticket.

## 2026-09-03 00:19-00:45 RUN + DECISIVE RE: ROOT CAUSE FOUND (Steam ticket)

### Run 00:19 (pid 32092) with cb163 trampoline
- Trampoline ARMED: `>> cb163 object=102ed7e0 vtable=12da7e4 Run=b0a770` then
  `>> cb163 patched: vtable[0]=6205e000 tramp@6205e000 (orig run b0a770)`.
- 679,372 RunCallbacks pumps across the session; **cb163 dispatch lines: 0**.
  Steam NEVER delivered GetAuthSessionTicketResponse to the game.
- `SteamAPI_Init -> true`, `SteamUser -> 82e0220` (same instance every call).
- Wire unchanged: 443 mTLS OK -> 323B doc -> clean close; 8443 pairs, zero bytes.

### RE (tools/find_steamuser_calls.py + disasm) - SteamAuthManager decoded
- IAT thunk map: steam_api IAT RVA 0xd627a8; SteamUser=0x11627e0.
- **SteamAuthManager::GenerateAuthToken @ 0xb0a874** (full state machine):
  state(+0x20): 0=idle, 1=requested, 2=ready, 3=failed.
  1) SteamUser() null -> "failed. SteamUser() is not available." (state=3)
  2) SteamUser(); vtbl+0x04 BLoggedOn() -> false -> "failed. no active
     connection to Steam." (state=3)
  3) SteamUser(); **vtbl+0x34 GetAuthSessionTicket(&m_ticket[+0x24], 0x800,
     &m_pcbTicket[+0x824])**; handle -> +0x828.
     handle==0 (k_HAuthTicketInvalid in 2012 SDK) -> "failed. invalid auth
     ticket handle." (state=3)  handle!=0 -> state=1, wait for callback 163.
  state==3 on next call -> reset to 0 -> RETRY (matches observed 3-call bursts).
- **OnAuthToken handler @ 0xb0a6df** (callback 163, registered @0xb0a7bc):
  logs "OnAuthToken callback: result=%d ticketHandle=%d" with args
  (fmt, [resp+4], [resp+0]) => **resp+0 = HAuthTicket, resp+4 = EResult,
  k_EResultOK == 1** (old comment had fields swapped - fixed in code).
  Success requires [resp+0] == m_hAuthTicket(+0x828) AND [resp+4] == 1.
- Cleanup fn @0xb0a83a: SteamUser(); vtbl+0x40 = CancelAuthTicket(m_hAuthTicket).
- GetSteamID = vtbl+8 (sret via stack arg); BLoggedOn = vtbl+4; PC_STEAM @0xb0a6ba.

### Conclusion (three-way cross-check)
3x-SteamUser bursts + state==3 retry loop + zero 163 dispatches =>
**steam_api's GetAuthSessionTicket returns 0 every call.** SteamUser valid,
BLoggedOn true. The login token is never produced -> WWLS never arms ->
8443 sockets die before any write. THE gate is the ticket, not the doc,
not TLS, not the SO_ERROR hook.

### Implemented (binkw32.dll 263680B, sha 581CEE18...F784E8, repo==steam dir)
- `steam_patch_gst`: on first SteamUser() return, patch ISteamUser vtable+0x34
  -> `gst_hook` (thiscall; calls original first, logs result).
- Fake path (default ON; `DS2_TLS_NOFAKE` env disables): when orig returns 0,
  fabricate ticket = real SteamID64 (via vtbl+8) + xorshift filler, 1024 bytes,
  *pcb=1024, return fake handle 1,2,3..., then **synthetically dispatch cb163**
  with {handle, eResult=1} through the game's original Run (parked if 163 not
  yet armed - flushed by steam_patch_163).
- 163 logger decode fixed to proven layout (+0 handle, +4 result, OK=1).
- New tools: find_steamuser_calls.py, xref_auth.py.

### NEXT LAUNCH - EXPECT IN ds2_tls.log
1. `>> GST hook armed: user=82e0220 ... [vt+0x34] -> ...`
2. `>> GST(this=82e0220, buf=..., max=800) -> 0 (INVALID - Steam refused)` (proof)
3. `>> GST FAKE: steamid=... / handle=N ticket=1024 bytes -> dispatch cb163 OK`
4. `>> cb163 SYNTH dispatched ...` then game-side "OnAuthToken ... succeeded"
   (state=2). Then **8443 must finally see a SEND** - capture it (pcap dumps at
   tools/pcap_*.bin + `feed()` dissector) and implement the GDS login exchange.
If 8443 still sends nothing: read GenerateAuthToken callers (WWLS may gate on
something else before/after token; check "The authtoken should not be empty"
xrefs) - but the ticket loop is definitively the first wall.


## 2026-09-03 14:05 - cb163 synthetic delivery ARMED (the decisive device)

13:55 session (pid 15488) - first session with GST hook live - REWROTE the diagnosis:
- GetAuthSessionTicket RETURNS REAL HANDLES (35/36/37, incrementing) into the manager
  buffer (buf=102ed9ac = cb163 obj+0x1c). The old 'returns 0' theory is dead.
- steam_api.dll in game dir: Valve version strings 1.30.50.46 but Authenticode
  HashMismatch => cracked/emulated DLL. It issues fake handles and NEVER posts
  callback 163: zero dispatches across 97,681 RunCallbacks pumps (trampoline
  would have logged 'cb163 *** GetAuthSessionTicketResponse').
- 443 DECODED: it is only a certificate probe. Client finishes mTLS (recv server
  Finished 48B), sends close_notify (37B), closes - NEVER reads our 323B doc.
  The doc greeting is irrelevant. 443 validates the patched root chain.
- 8443: getaddrinfo -> connect -> closesocket within 1ms, twice (30.995, 31.873),
  zero bytes. Probe loop gives up because WWLS never arms (no 163 OK).
- Game's own retry timeout ~3s: GST->36 at 28.395, GST->37 at 31.435.

FIX DEPLOYED (lib.rs): RunCallbacks hook now synthesizes the 163 response -
1.5s after a real GST handle with no real dispatch, call the game's own Run
(vtable[0] trampoline, T163_ORIG) with {handle, k_EResultOK=1} (layout proven
by disasm: resp+0=HAuthTicket, resp+4=EResult). Once per handle; disabled if a
real 163 ever arrives (T163_DISPATCHED flag set in the trampoline logger);
DS2_TLS_NOFAKE disables everything. 1.5s beats the game's 3s retry timeout.
Build exit=0, deployed to Steam dir, hashes identical.

NEXT LAUNCH: expect '>> cb163 SYNTH: no real dispatch ...' then 'SYNTH dispatched',
then the game should log 'OnAuthToken - succeeded' internally, advance WWLS to
STATE_Connecting ("*** Connecting to server: thqc-darksiders2-pc.thqconnect.com:8443")
and FINALLY send the GDS login packet on 8443. Server: watch requests.log for the
first nonzero 8443 bytes - that packet is the Rosetta stone for the GDS protocol.
If 8443 still sends nothing: dump the m_ticket blob at manager+0x24 and check
wwls state transitions via xref_auth.py around 0xb0a6df.

## 2026-09-04 22:30 - CRASH ROOT-CAUSED + FIXED (ABI bug in synth dispatch)

22:06 session crashed at startup: BEX/StackHash c0000005, fault offset 0x19f30c
(stack), delayed ~2.5s after the synth. Log proves it: SYNTH dispatched printed
obj=6201f07c run=3f handle=0x19f604 eResult=0x6201f0d8 - all garbage = ESP-shifted
frame reads AFTER the call.
CAUSE: b0a770 (cb163 vtable[0]) is Run(pvParam,bIOFailure,hSteamAPICall,iCallback)
with 
et 0x10 = FOUR stack dwords (disasm-proven). Synth declared 1-arg thiscall
callee pops 16, we pushed 4 => +12 ESP corruption on return => smashed unwind.
FIXES: (1) RunFnT = thiscall(usize,*const u32,u32,u32,u32); dispatch passes
(resp,0,0,163) - thunk ignores 2-4 but MUST have them. (2) parked-handle flush
moved OUT of RegisterCallback re-entrancy onto the RunCallbacks pump thread
(SYNTHED_HANDLE deduped). Build exit=0, deployed, hashes identical.
NEXT LAUNCH: expect SYNTH dispatched with SANE values (obj=102exxxxx run=b0a770
handle=<real GST handle>), then game-internal 'OnAuthToken - succeeded', WWLS
state=2, and the FIRST EVER nonzero bytes on 8443 = GDS login packet.
If it crashes again: WER offset + last ds2_tls.log lines + disasm of the faulting
site; if it does not crash but 8443 still silent, check m_hAuthTicket match
(manager+0x828 vs GST handle) via the 'ignored. m_token' log path.

NOTE (22:35): current deployed build verified by CONTENT, not hash: marker
'on RunCallbacks pump' (only exists post-fix) present in both repo-root and
Steam-dir copies; source lines 904/1118 confirm 4-dword ABI; timestamps fresh.
(Hash equality with the pre-fix build report was a bookkeeping artifact -
content markers are authoritative. The build bat also clears ds2_tls.log on
every deploy, so pre-crash log evidence lives only in this journal.)


## 2026-09-05 00:30 - CRASH #2 ROOT-CAUSED: Rust thiscall fn-POINTERS are cdecl
- 00:08 session: SYNTH fired (2313ms after GST->66), game lived 2.3s more (getaddrinfo->
  8443 connect), then c0000005 executing stack garbage (WER fault 0x19f30c, module unknown).
- Smoking gun: dispatch log printed obj=6205f07c (our .bss!), run=0x42 (=handle 66!),
  handle=0x19f2c4 (stack), eResult=6205f058 -> ESP left shifted by exactly 4 after the call.
- ROOT CAUSE: this toolchain emits extern "thiscall" FN DEFINITIONS correctly (gst_hook
  receives this/args fine) but transmuted thiscall FN POINTERS are emitted as cdecl:
  all 5 args pushed (20B), game Run (b0a770, ret 0x10) pops 16 -> ESP+4; ECX never set ->
  Run's member-thunk (mov eax,ecx; call [eax+0x10]) ran on garbage 	his -> eventual
  execute-stack crash. The GST->orig call 'worked' only because with this-on-stack the 3
  real args coincidentally land at the depths a thiscall callee reads.
- FIX: hand-assembled thiscall caller (T163_THUNK, 33B, VirtualProtect'd):
  push 163 / push 0 / push 0 / push [esp+0x14]=pResp / mov ecx,[esp+0x10]=obj /
  mov eax,run / call eax / ret 8. dispatch_163 now goes through it as cdecl(obj, pResp).
- NOTE: build5 failed (raw-ptr autoref lint) -> fixed via write_bytes+copy_nonoverlapping.
  A racing verify command overwrote the Steam copy with the old DLL once - re-deployed.
- DEPLOYED: binkw32.dll 265216 B, SHA E0D93E29...E9036D, root==steam, markers
  'call-thunk'/'dispatched cleanly'/'via thunk' present, old format string gone.
- NEXT LAUNCH EXPECTED: '>> cb163 SYNTH: dispatch obj=102exxxx handle=<h> eResult=1 via
  thunk' then 'dispatched cleanly' with SANE values (no .bss/stack junk), game-internal
  'OnAuthToken - succeeded' (mgr+0x20=2), WWLS state=2, and FINALLY nonzero bytes on 8443
  (GDS login packet = the protocol Rosetta stone). If 8443 still silent but no crash,
  next suspect: emu's ticket buffer content (inject real-shaped blob in gst_hook).

## 2026-09-05 16:40 - CRASH #3 ROOT-CAUSED: hand-thunk ABI bug -> FINAL DESIGN: never call game code
- 03:23 session: dispatch printed SANE values (obj=102ed800 handle=69 eResult=1 via thunk
  6201f020) then NO 'dispatched cleanly' -> crash INSIDE the hand-built thunk (WER fault
  offset 0x1f098a1, module unknown).
- MY BYTE-CODE BUG (found on inspection): at thunk entry (cdecl) [esp+4]=obj, [esp+8]=pResp.
  I pushed (163, 0, 0, pResp) so at Run entry [esp+4]=0 (Run read pvParam=NULL) and
  'mov ecx,[esp+0x10]' hit [esp+16]=retaddr-of-dispatch, NOT obj. Run then did
  call [garbage_eax+0x10] -> jumped to 0x1f098a1 -> crash. Arg order + ECX source both wrong.
- FINAL DESIGN (adopted, no game-code execution AT ALL):
  * OnAuthToken's success path (b0a6df) does ONLY printf + mgr->state(+0x20)=2.
  * mgr = [obj+0x14] + [obj+0xc] (Run's own deref, b0a770; cross-checked earlier:
    obj=102ed990 -> mgr+0x24 == the exact GetAuthSessionTicket buffer).
  * dispatch_163 now: computes mgr, logs mgr+0x828 m_hAuthTicket vs synth handle, and on
    MATCH+eResult==1 writes [mgr+0x20]=2 directly (VirtualProtect'd). No thiscall, no asm,
    no crash surface.
  * steam_patch_163 now dumps obj+0x00..0x1c for sanity instead of building the thunk.
- BUILT (exit 0, 77 EAT decorated), deployed root->Steam (bat's auto-deploy silently didn't
  take; manual copy done): binkw32.dll 266240 B, SHA 969C0051...E0B8A, root==steam.
  Markers: 'SET SteamAuthManager', 'mgr+0x828'; old 'via thunk'/'call-thunk'/'dispatched
  cleanly' GONE.
- NEXT LAUNCH EXPECTED:
  >> cb163 SYNTH: obj=102exxxx -> mgr=102exxxx (base .. +delta ..) handle=.. eResult=1
  >> cb163 SYNTH: mgr+0x828 m_hAuthTicket=.. vs synth .. [MATCH]
  >> cb163 SYNTH: SET SteamAuthManager[+0x20]=2 (state 2) - auth accepted
  -> WWLS state=2 -> FINAL goal: FIRST nonzero bytes on 8443 (GDS login packet).
  If MATCH but no 8443 data, next: check the emu ticket blob content / WWLS update loop.

## 2026-09-05 23:30 - REAL BUG FOUND (session 23:08 DID match, write was guarded off)
- Session 23:08 (pid 15780) ran STABLE (no crash since the direct-state-write design) BUT
  dispatch_163 NEVER wrote state: my guard if base == 0 { return; } fired because for the
  REAL object obj+0x14 == 0. Disasm b0a770 (Run): ecx = [obj+0x14] + [obj+0xc]; the manager
  is the SUM 0 + 0x102ed988 = 102ed988 (mgr+0x24 == the exact GST ticket buffer 102ed9ac).
  So the guard must be on MGR not base. FIXED: if mgr == 0.
- ALSO: the 'handle mismatch' I half-suspected is NOT one: GST logs HEX, SYNTH logs DECIMAL.
  0x47 = 71, 0x48 = 72, 0x4a = 74, 0x4c = 76 - all MATCH. And GenerateAuthToken (b0a874)
  stores the GST return at [mgr+0x828] (mov [esi+0x828], eax) = our mh read. OnAuthToken
  success path is exactly cmp handle==mh && eResult==1 -> [mgr+0x20]=2. Our write replicates.
- Build exit 0 (23:14:48, root sha 6A5C825B...), markers 'SET SteamAuthManager'+'mgr+0x828'
  present in binary (the 'guard must be on MGR' string is only a comment, stripped).
  DEPLOY BLOCKED: game running with OLD dll (16:33:27) -> Copy-Item AccessDenied. NEED GAME
  CLOSED to deploy root->Steam, then relaunch.
- NEXT (after deploy): expect
  >> cb163 SYNTH: obj=102ed990 -> mgr=102ed988 (base 0+delta 102ed988) handle=0x47 eResult=1
  >> cb163 SYNTH: mgr+0x828 m_hAuthTicket=0x47 vs synth 71 [MATCH]
  >> cb163 SYNTH: SET SteamAuthManager[+0x20]=2 (state 2) - auth accepted
  -> WWLS state=2 -> GDS login SHOULD finally hit 8443 (server: raw bytes logged)

## 2026-09-05 23:40 - BREAKTHROUGH + next-close investigation (session 23:19 pid 15720)
- THE AUTH STATE WRITE FINALLY LANDED:
  >> cb163 SYNTH: mgr+0x828 m_hAuthTicket=4e vs synth 4e [MATCH]
  >> cb163 SYNTH: SET SteamAuthManager[+0x20]=2 (state 2) - auth accepted
  The game reacted 100ms later with a FAST 8443 connect (vs old lazy cadence).
  Handle equality was 0x47=71, 0x48=72, 0x4a=74, 0x4c=76 (GST logs hex, SYNTH decimal).
  Every subsequent attempt shows m_hAuthTicket=0 [MISMATCH] (manager re-requesting,
  clears handle before re-calling GenerateAuthToken).
- BUT 8443 STILL SENDS NOTHING: connect -> closesocket in ~1ms, ZERO I/O on the socket
  (no select/send/recv at all). Server accepts and logs nothing.
- CRITICAL RE FINDING - WWLS connect wrapper 0xb198b0:
  * b19460 (pre-resolve) -> connect [0x1162650] at 0xb19a3a (ONLY connect call site)
  * connect>=0 -> cmp [esp+0x10],1 -> then SO_ERROR gate 0xb19770(sock, errout)
  * gate does getsockopt(SOL_SOCKET, SO_ERROR); returns al=1 only if err==0 or 10056
  * gate FAIL path 0xb19b07 -> closesocket at 0xb19b2c -> return -1  (THE KILLER)
  * gate PASS path 0xb19ad9 -> set *errout=1, log 'Connected', keep socket
  * closed sockets array at manager+0xc8; 4 closesocket call sites: 0x416419,
    0xb19b2c, 0xb19d3b, 0xb1db2f (proxy-CONNECT zone w/ 'PVC TLS' strings)
- gotcha: our getsockopt IAT hook NEVER FIRES (0 lines in whole 53MB log): the exe
  imports WSOCK32 BY ORDINAL, and the amap fallback didn't match getsockopt. So the
  gate runs against REAL getsockopt and we were BLIND to its pass/fail.
- NEW INSTRUMENTATION (built 23:36:56, 268288B, sha to verify, deploy blocked by game):
  1) h_closesocket logs caller=0x%08x (ret addr via asm [esp]) + port => WHICH of the
     4 close sites kills 8443 (gate-fail b19b2c vs timeout b19d3b vs proxy b1db2f).
  2) 0xb19770 PATCHED with a stub: logs sock + real SO_ERROR then FORCES al=1 (pass),
     cdecl 1-arg (caller pushes 1 + add esp,4) - identifies gate as the blocker
     and defeats it in one move.
  3) DS2_TLS_HOLD8443=1 env: h_closesocket REFUSES to close 8443 sockets (keeps slot)
     so we can see what the state machine does with a live socket (does it then
     send the login packet?).
- NEXT RUN plan: set DS2_TLS_HOLD8443=1 + default gate-force. Expected log:
  >> GATE 0xb19770: sock=xxxx SO_ERROR=<n> -> FORCED PASS
  == closesocket ... port=8443 caller=0xb19b2c (gate-fail KILLER confirmed) -> HOLD
  then either the game sends (WIN) or hangs (close is semantically required).

## 2026-09-06 17:45 - STACK-TRACE diagnostic deployed
- 14:48 session (pid 7636): REAL-BLOCKING 8443 connect -> 0 (genuinely connected),
  GATE pass (SO_ERROR=0, real getsockopt still invisible - gs=-1), then 1ms no-I/O close.
- This PROVES the half-connected-connect theory WRONG and the auth write is NOT the
  blocker either: the socket is genuinely OS-connected, the game's own gate passes,
  yet it closes without sending or selecting. An IN-PROCESS precondition bails after
  connect returns but BEFORE any send on 8443.
- 443 (sock 730, 14:48): full mTLS, server Finished at .054, close_notify at .162
  (108ms) - the client NEVER reads the doc. The EOF-delivery fast-close server change
  (150ms beat + close) is now LIVE (server restarted PID 33284).
- NEXT-1 theory: the game needs the config document BEFORE it will send on 8443, and
  443 is where it comes from. If fast-close EOF delivery makes the client actually
  READ the doc (grep ds2_tls.log for 'recv sock=.* len=' right after the Finished),
  the config may finally reach the game -> 8443 send.
- DEPLOYED (17:45): binkw32.dll 269824B SHA 19EC480A... with RtlCaptureStackBackTrace
  on closesocket -> '== closesocket sock=.. port=8443 stack=[...]' giving the REAL
  closure path (frames are RVA-ish; Darksiders2.exe base=0x400000, binkw32=0x62xxxxxx,
  ws2_32/ntdll elsewhere. Map: stack[0]=h_closesocket proxy, stack[1..]=callers).
- HOW TO READ THE NEXT RUN:
  1) ds2_tls.log: does a recv on 443 read our 323B doc after the Finished?
  2) 8443 closesocket stack frames: which exe function (0xb19b2c gate-fail vs
     0xb19d3b watchdog vs 0x416419 TLS-teardown vs 0xb1db2f proxy-CONNECT)?
  3) If 8443 still no send + stack shows a game fn, RE that fn for preconditions.

## 2026-09-06 18:05 - MANUAL STACK SCAN deployed (RtlCaptureStack failed to walk)
- 17:40 session (pid 3124): stack=[62592b8b, 0,0,...] for EVERY close (443 AND 8443).
  0x62592b8b = binkw32+0x2b8b = our own DLL => RtlCaptureStackBackTrace CANNOT walk
  past our Rust frame (FPO), so only frame 0 (our module) appears. USELESS for the
  caller. The close IS the game's (via IAT redirect), but which of the 4 sites is
  unknown.
- REPLACED with a manual stack scan: read ESP..+0x600, collect decoded DWORDs in the
  exe image range [0x400000, 0x1300000) (dedup, cap 12) -> the game's actual return
  addresses that sit further up the stack. Next run should show e.g. 0xb19b31/
  0xb19d3c/0xb1db30/0x41641e.
- ALSO today's finding: even with REAL-BLOCKING connect + gate FORCED PASS, the game
  closes 8443 1ms later with NO i/o - so the blocker is an in-process precondition
  that fails after connect returns, before any send/select. The 4 close sites are:
  0x416419 (TLS teardown), 0xb19b2c (gate-fail), 0xb19d3b (connect timeout/watchdog),
  0xb1db2f (proxy CONNECT teardown).
- Server-side: fast-close EOF delivery LIVE. Next run: watch for a 443 recv of the
  323B doc after the Finished (would mean the client READ it this time).

## 2026-09-06 18:20 - CRASH #4 ROOT-CAUSED: manual stack scan derefs unmapped page
- 18:01 AND 18:03 crashes: binkw32.dll c0000005 at offset 0x2b17 (twice, deterministic).
- Disasm of deployed DLL at 0x2b17:
   0x2b15  mov eax, esp
   0x2b17  mov edx, [eax+ecx]   <- reads [esp+off] BEFORE any page validation -> AV
- Root cause: the closesocket manual stack scan walks ESP..+0x600 dereferencing DWORDs;
  when esp+off lands on an unmapped/guard page it faults. RtlCapture backtrace didn't
  work (only our own frame), and the manual scan crashed.
- FIX (lib.rs): VirtualQuery-backed guarded_readable() checks MEM_COMMIT + not
  PAGE_NOACCESS/GUARD for the start AND end dword byte before the deref; scan capped at
  0x400. get_vq() lazily resolves VirtualQuery via GetModuleHandleW/GetProcAddress.
- Deployed: binkw32.dll 269824B SHA 8D0E0847... root==steam.
- NEXT RUN EXPECTED: no crash; the 8443 closesocket stack should finally show the GAME's
  return addresses (e.g. 0xb19b31/0xb19d3c/0xb1db30/0x41641e) revealing which close path.

## 2026-09-06 19:05 - GAME CONSOLE-LOG SINK HOOK DEPLOYED
- Now we know the 8443/443 closes come from the THQ request/state-machine teardown
  (stack revealed: 0xb1120e connection-dtor close from message handler 0xb00e40).
- Root pieces: 0xb00e40 = 16-state connection FSM (jump tb 0xb018e8); state 1 handler
  0xb00f2c does push 7; call 0xb18a00 (a '7' op?), then 0xb13830 (0xb132a0 -> checks
  [esi+0x358]==0 -> ret 3 => teardown if the op fails), then 0xb00510. If any result
  nonzero -> state 16 -> destroy -> 0xb1120e closes [esi+0xc8].
- The GAME's decision text is what's missing -> NEW HOOK: 0xaef5a0 (formatted console
  log, vsnprintf into 0x1000 stack buf) then calls runtime sink POINTER at VA
  0x1162114 (in .rdata, writable via VirtualProtect). We reroute that slot to our
  cdecl game_log_sink() -> tlog!('[GAME] ...') then forward to original. Retried in
  the scan loop if empty. This should print WWLS/connect/STATE messages.
- DEPLOYED: binkw32.dll 271360B SHA D00BCEB9... (build exit 0, 33 warnings).
- NEXT RUN: watch ds2_tls.log for '[GAME] ...' lines - they will name the exact
  failure (e.g. 'Failed to connect to %s: %s' / 'STATE_... failed').

## 2026-09-08 08:10 - CRASH #5 ROOT-CAUSED + FIXED: game sink is STDCALL, hook was cdecl
- 08:05 session: the sink hook FIRED ('[GAME] THQServerManager::Create - creating
  network manager') then c0000005 at 0x0cfaee24 module UNKNOWN = execute-garbage = stack
  corruption. Disasm proved the sink (0xaef5a0 -> call [0x1162114]) pushes 1 arg and
  calls TWICE (buf, then CRLF) with NO caller cleanup -> sink pops its own arg =
  STDCALL. Our hook was declared extern 'cdecl' (no pop) -> after the 1st call ESP+4
  -> 2nd call misaligned -> caller frame unwound into garbage.
- Evidence extern 'system' == stdcall on this toolchain: every VirtualProtect/kernel32
  call across the proxy works with zero manual fixup.
- FIX: game_log_sink + SinkFnT both -> extern 'system'. Rebuilt (the earlier deploy
  raced with the build and shipped the old cdecl DLL - caught by identical SHA,
  rebuilt clean). DEPLOYED: binkw32.dll 271360B SHA EFD5E073... root==steam.
- NEXT RUN: should be stable AND give us '[GAME] ...' lines naming the exact login
  failure (e.g. 'Failed to connect', 'STATE_... failed', 'The authtoken should not
  be empty...').

## 2026-09-08 08:40 - BREAKTHROUGH: game console captured; 8443 = libcurl HTTPS
- The GAME LOG SINK hook (stdcall fix) now captures the FULL login protocol:
  * GenerateAuthToken handle 109/110/111 -> OnAuthToken - succeeded (our fake works!)
  * 'Running https://thqc-darksiders2-pc.thqconnect.com:8443/thqconnect/login/
    platformLogin?...&auth=140000...' AND '/thqconnect/cloud/execute?...auth=140000...'
    AND '/thqconnect/login/ssoStatus?...auth=140000...' -> 8443 is libcurl HTTPS
    GET/POST with the auth ticket as a QUERY PARAM (not TLS client cert!).
  * 'PEM_read_bio_X509 failed...' right after connect -> libcurl chokes because OUR
    8443 server sent a CertificateRequest (verify_client_cert was GLOBAL).
  * Then 'CURLMSG: request complete' -> HttpRequestAsync::OnComplete -> close/retry.
  * HttpRequestAsync / CallCloudAsync / AccountPlatformLoginAsync / CheckSSOStatusAsync
    describe the orchestration. Only 4-5 jobs per 30s cycle.
- FIX (server): per-port verify_client_cert via 'verify_client_cert_ports'=['443'];
  8443 no longer sends a CertificateRequest. config: greet_8443=false. 8443 now
  client-first HTTP(S). Verified at 08:38: banner shows 443 client-cert-requested /
  8443 client-cert-none.
- NOTE auth token format: 0x1400 0000 + steamid64 + opcodes; the game signs nothing;
  the URL is the full protocol. server needs to answer these HTTPS GET/POSTs as HTTP
  (thqconnect/cloud/execute, /thqconnect/login/platformLogin, /thqconnect/login/
  ssoStatus) with JSON-ish envelopes.
expe

## 2026-09-08 18:00 - 8443 IS LIBCURL HTTPS; trust-anchor slots fixed
- GAME LOG SINK hook (stdcall) captures the FULL protocol now:
  * WWLS -> GenerateAuthToken(h) -> OnAuthToken - succeeded (our fake works)
  * 'Running https://thqc-darksiders2-pc.thqconnect.com:8443/thqconnect/cloud/
    execute?...&auth=<1400-ticket>' AND /thqconnect/login/platformLogin AND
    /thqconnect/login/ssoStatus  -- the 8443 protocol is HTTP(S) GET with the
    STEAM auth ticket hex-encoded in the URL query ('auth=14000000...').
  * The requests run through a SCOPED CURL POOL (libcurl)
  * 443 is STILL a raw TLS mTLS connection (CertificateRequest -> CN=DS2 client
    cert -> server greeting doc).
- ANALYZED: the game embeds TWO cert slots (0xed7a20, 0xed76b8) that are the
  LIBCURL TLS CA anchors for 8443 (self-signed *.thqconnect.com, expired) - NOT
  part of the 443 mTLS chain (which uses the root slot 0xf712d8 + client cert
  0xf717c0). Original slots were a bad PEM chain; our patched slot was a single
  cert + CRLF padding that BROKE BIO_new_mem_buf(,-1) -> 'PEM_read_bio_X509
  failed' -> no TLS handshake at all.
- FIX SEQUENCE (this session):
  1) Restored ORIGINAL slot bytes -> PEM parse OK -> libcurl now learns the addr,
      connects, and runs a FULL TLS handshake to 8443 -> but gets
      'TLSV1_ALERT_UNKNOWN_CA' because the SLOT anchors are the expired self-
      signed certs which don't chain to our root-signed server leaf.
  2) PATCHED both slots (872-byte regions each) with OUR ROOT CA PEM
      (server/certs/ca.pem, NUL-terminated). Verified: PE intact, both regions
      contain exactly 1 parseable root cert + \n\0 pad. leaf1_slot.pem chains
      to ca.pem (openssl verify OK). 8443 TLS probe: handshake OK.
- NEXT LAUNCH: libcurl's trust set = our Root CA => validates server leaf =>
  sends GET /thqconnect/cloud/execute... POST /thqconnect/login/platformLogin.
  Server (greet_8443=false, verify_client_cert only on 443) should now serve
  these as HTTP. If 200 + valid JSON response, the login state advances.

## 2026-09-08 18:15 - 'error adding certificate' (dup slot certs) FIXED
- New run (18:07-18:08, exe had OUR root ca.pem in BOTH slots): game got
  '[GAME] Running https://...:8443/thqconnect/login/ssoStatus?...' then 35ms later
  '[GAME] error adding certificate' -> CURLMSG complete -> abort before TLS.
  Server saw 8443 tls=False (no ClientHello; libcurl failed building its store).
- ROOT CAUSE: X509_STORE_add_cert() rejects a DUPLICATE. We wrote the SAME root
  (ca.pem) into both 872-byte trust slots; the game's curl loader adds both slots
  to one store -> add#2 fails 'error adding certificate'. Original slots were two
  DIFFERENT certs.
- FIX: slot1 (0xed7a20) = ca.pem root1 (C=US,O=THQ Inc.,CN=THQ Connect Root CA),
  slot2 (0xed76b8) = NEW distinct CA ca_login.pem (CN='THQ Connect Root CA - Login',
  serial 5A11C0DE, minted 1024-bit RSA/SHA256 self-signed CA:TRUE) -> no dup.
  Both regions: exactly 1 cert + \n\0 + NUL pad (verified). Server leaf still
  chains to root1 (openssl verify OK). PE intact.
- NEXT LAUNCH: libcurl adds root1 + root2 (both OK) -> verifies server leaf vs
  root1 -> PASS -> sends the actual HTTPS GET/POST /thqconnect/... requests.
  Watch server_new_stdout for 8443 TLS handshake OK + do_GET/POST with auth=.

## 2026-09-11 01:54 - GAME REQUESTS HIT THE WIRE (tls recv on 8443!)
- 09-11 01:45/01:52 run (exe with slot1=ca.pem root, slot2=ca_login.pem distinct):
  * NO 'error adding certificate' (dup-slot fix WORKED)
  * 8443 TLS handshake OK (libcurl accepted our server leaf vs root1)
  * SERVER RECEIVED the game's real requests (tls recv ...):
    - 01:45:51 GET /thqconnect/cloud/execute?profileNames=%2fsteam%2f<steamid>%2c...&ops=GET%2cGET&docKeys=TomeGift%2cGameToWeb...&auth=1400...
    - 01:45:52 cloud/execute ops=SET
    - 01:52:11 GET /thqconnect/login/platformLogin?version=1&region=0&country=NL&appID=50650&platform=PC_STEAM...&auth=...
    - 01:52:12 GET /thqconnect/login/ssoStatus?action=1&platform=PC_STEAM...&auth=...
  * Headers: 'GET /... HTTP/1.1', 'Host: thqc-darksiders2-pc.thqconnect.com:8443',
    'Accept: */*', 'Accept-Language: no-cache'
- BUT: server had greet_tls STILL ON for 8443 -> sent the doc greeting FIRST,
  corrupting the HTTP request/response (game got garbage to its GET).
- FIX DEPLOYED: greet_tls gated to lport != 8443 (443 keeps the doc greeting,
  8443 is pure client-first HTTPS). Server restarted 01:54:14 (PID new).
- NEXT LAUNCH: game HTTPS GET/POST on 8443 -> server _dispatch -> handlers
  (initsession/platformlogin/ssostatus/cloud/execute) -> JSON envelope back.
  If auth= ticketing still blocked, handler logs will show which param fails.

## 2026-09-12 02:02 - ASYNC RESPONSE SCHEMA DECODED + FIXED (the 'not formatted' key)
- 02:08 run: ALL HTTPS responses (platformLogin, ssoStatus, cloud/execute) got
  '%s - response was not formatted correctly' -> the game logs 'response good
  <N> bytes' (HTTP OK) then rejects the JSON SHAPE.
- DECODED the shared async response parser (exe 0xafa77d, xrefs from job-complete
  at 0xb02ab4): it REQUIRES these TOP-LEVEL keys on every response:
    status (string, 0xaf91e6), url (string, 0xaf8288), email (string, 0xaf8645),
    username (string, 0xaf8645), age (int, 0xaf8243), users (list, 0xafa559),
    jobID (int, 0xaf8269) - optional (flag bit2; caller passes 1).
  If ANY required key is missing/type-mismatched -> 'response was not formatted
  correctly' -> the async job dies and the login loops.
- FIX (server): _ok() now injects status/url/email/username/age/users (from the
  account/profile if known) into BOTH the top-level env AND the nested output.
  ssoStatus handler also passes username from the session account.
- Server restarted 02:01:39 (new PID). Next run should show job-complete logs
  WITHOUT 'not formatted' -> then the login can advance to the actual game menu.

## 2026-09-13 02:36 - RESPONSE SCHEMA TYPE FIX: status must be INT not string
- 02:24 run STILL showed 'response was not formatted correctly' for ALL jobs
  after adding the schema keys - because status was a JSON STRING ("OK"/"signedIn").
- DECODED the shared async response parser types (0xafa77d + readers):
   status   -> INT enum in [-2..5], read as ONE BYTE (0xaf8223/0xaf8092),
              a string parses as its first ASCII byte (e.g. 'O'=79) -> OOR -> fail
   url      -> string (0xaf8288)
   email    -> string (0xaf8645)
   username -> string (0xaf8645)
   age      -> int (0xaf8243)
   users    -> list (0xafa559)
   jobID    -> optional int (0xaf8269)
- FIX: _ok() maps legacy string statuses to int (ok/signedin/online/success->0,
  failure->1, connected->2, disconnected->3) and emits status as int 0.
- VERIFIED live: ssoStatus now returns {"resultCode":0,"result":0,"status":0,
  "url":"","email":"","username":"Player5675","age":0,"users":["Player5675"],
  "signedIn":1,...} - int status confirmed in server log 02:35:42.
- Server restarted 02:34:32. Game is running and auto-retries ~every 30s;
  next retry should log 'response good' WITHOUT 'not formatted'.

## 2026-09-13 03:57 - RESPONSE KEY ORDER: schema fields FIRST
- Even with int status:0 + all keys (status,url,email,username,age,users), the
  game STILL logged 'not formatted correctly' for EVERY job (platformLogin,
  ssoStatus, cloud/execute, getNews) at 02:38.
- Parser mechanics decoded: readers share a CURSOR over the response buffer
  (0xaf7d27 advances a shared offset; 0xaf7d65/0xaf7d86 read/skip). The async
  'format check' (0xafa77d) reads status->url->email->username->age->users in
  sequence. If the cursor is not reset per-key, the JSON key ORDER matters and
  our envelope-first order (resultCode,result,status,...) put garbage first.
- FIX (server _ok): emit schema keys FIRST in order
  status,url,email,username,age,users[,jobID] then resultCode,result then any
  handler extras, then output. Rebuilt + restarted 03:56:12.
- TEST: the game's own console is the oracle. On next launch look for
  'response good' with NO 'was not formatted correctly'. If it STILL fails,
  the format is not JSON-keyed but a compact binary/ordered structure - then
  the schema blob (0x13c5271 'Object') matters and we must match its field
  table (status=0x01..., users=0x08/0x0c/0x8c... table).

## 2026-09-14 02:46 - DEFINITIVE: response MUST be a JSON ARRAY [ {...} ]
- 14 consecutive 'response was not formatted correctly' across schema/type/order
  variants. Final decode of 0xaf7e16 (the response->container reader):
    0xaf7e39 push 0x5b '['
    0xaf7e42 call 0xaf7db8   ; skip-ws, is first char '['?
    0xaf7e4b cmp byte[ebx],0
    0xaf7e50 (if '[') expect '{'
    0xaf7e5e xor al,al  ; NOT '[' -> return FALSE
    0xaf7e62 mov al,1    ; '[' seen -> SUCCESS
  => bare JSON object {} returns FALSE immediately. The envelope is a JSON
     ARRAY wrapping the object: [ {...} ] (serverside: json.dumps([obj])).
- FIX: _dump() now always wraps: json.dumps([obj]). All handlers go through it.
- Server restarted 02:46:15 (new PID). Next launch: ssoStatus/platformLogin/
  cloud/getNews all respond as [ {schema...} ] -> format check should pass.
  If still failing: the shared parser has per-job schema expectations (the
  0x13c5271 'Object' blob + type table status=byte/url/email/username=string
  aged=int users=list) - next instrument 0xafa77d to log per-key result.

## 2026-09-15 03:5x - SHARED ASYNC PARSER FULLY DECODED (0xafa77d chain) - LIST-FIRST
The game response-was-not-formatted gate (0xafa77d) is a LIST-first tokenizer, NOT JSON-keyed:
- status -> 0xaf91e6/0xb320cb/0xaf8223/0xaf8092/0xaf803e: numeric token (range [-2,5])
- age    -> 0xaf8243/0xaf80e2: numeric token
- url    -> 0xaf8288: JSON ARRAY of strings (expects [)   <- our "url":"" was the killer
- email/username -> 0xaf8645 -> 0xaf8288: JSON ARRAY of strings
- users  -> 0xafa559 -> 0xaf8f37: ARRAY of OBJECTS {name:[..],accountID:n,profileID:n}
- 0xaf7e16 accepts top-level { or [; for [ the cursor is left at { so members misread -> BARE OBJECT required
Server _ok() now emits lists: {status:0,url:[],email:[],username:[X],age:0,users:[{name:[X],accountID:N,profileID:N}]}
NEXT: launch -> ssoStatus/platformLogin should show response good with NO was-not-formatted for the first time.

## 2026-09-15 04:0x - PEEK HOOK CRASH#6 ROOT-CAUSED + FIXED (resume at +6)
04:05 crash: Darksiders2.exe+0x6f7d6a = 0xaf7d6a -> my 5-byte patch resumed at +5,
splitting 'mov eax,[ebp+8]' (bytes 8b 45 08 at +3..+5) => garbage exec. Fix: prologue is
6 bytes (push ebp; mov ebp,esp; mov eax,[ebp+8]); stub now re-runs those 6 bytes and
jmps to 0xaf7d6b (+6). The hook DID capture one dump before the crash:
>> PEEK dump@+0 (160B): {"status":0,"url":[],"email":[],"username":[],"age":0,
   "users":[{...  => the checker tokenizes OUR RAW JSON (keys and all).
Deployed 556696626BB052DE (markers 6B prologue/PEEK dump@ present). Next: launch;
read ALL >> PEEK dump@ lines to map the exact tokenization path & why it fails.

## 2026-09-15 05:5x - PEEK trail decodes the format: POSITIONAL (keys never consumed)
PEEK dumps show the tokenizer stepping +0 { -> +1 " -> +2 s  (consumes { then the KEY quote,
then tries to read a NUMBER from the key text = 0 digits) => status read FAILS immediately.
The shared checker consumes NO object keys; it expects a POSITIONAL VALUE sequence in
field order: status(num) url(list) email(list) username(list) age(num) users(objs).
Hypothesis format (comma-less since numbers/lists don't skip ',' between fields):
  ssoStatus -> [0[][]["X"]0[[["X"]123123]]]
Server now ROTATES 3 ssoStatus formats per request (fmt#0 positional, #1 keyed control,
#2 array control) + logs which fmt each request got -> one launch, three verdicts.
Game exited by itself 05:53:25 after login failures (WorldwideLoginService dtor).
NEXT: relaunch; read server fmt# lines + [GAME] CheckSSOStatusAsync verdict per request.

## 2026-09-15 06:1x - POSITIONAL FORMAT FAILED TOO -> ret-tagged PEEK hook
fmt#0 positional [0[][]["X"]0[[["X"]123123]]] served 06:05:36, game: NOT FORMATTED STILL.
Peek dumps for the positional parse STOP at @+1 (status number peek) - the number
read never advances. Static model says it should read '0' - contradiction. Upgraded
PEEK hook to tag each peek with the CALLER return address (ret=0x...) so the next
run maps EXACTLY which reader peeks and where the parse bails. Server now serves
positional on EVERY ssoStatus retry (controls proven fail). Deployed 5CD8F576943867CF.

## 2026-09-19 22:25 - THE TRACE DECODE (positions are HEX!) + 2 handlers LOCKED

The long-standing confusion about the ARG trace was an arithmetic error on my
side: `@+36/45` is HEX, so it means position 54 of a 69-byte buffer -- and it
matches our body byte-for-byte. With that corrected, the grammar is settled:

PROVEN ACCEPTED (ssoStatus, 69B, `[0,[],[],["Player26495"],0,[[["Player26495"],3091226495,3091226495]]]`):
```
@+00 '[' open root array                    ret=af7e47
@+01 '0' status (scalar; list-probe soft)   ret=af7f47
@+03 '[' url    @+04 ']' empty, check ']' @23 OK
@+06 '[' email  @+07 ']' empty, check ']' OK
@+09 '[' username @+0a '"' string, check ']' @23 OK
@+19 '0' age (scalar)                       ret=af7f47
@+1b '[' users list                         ret=afa5a0
@+1c '[' element  @+1d '[' name-list  @+1e '"' name
check ']' @43 OK   -> element = [[names], aid, pid]
@+2d aid (scalar) @+38 pid (scalar)  check ']' @66 OK
@+43/@+44 trailing ':' probes FAIL SOFT (tolerated)  -> EOF = ACCEPT
```
-> users element = `[[names...], accountID, profileID]`; the two `want=':'`
   probes at the tail are soft and harmless.

REJECTED platformLogin (69B, users in FIELD 4 + `,0` tail):
```
@+19 '[' users-list BUT reader = afa489 (a DIFFERENT reader than afa5a0!)
@+1a '[' element @+1b '[' @+1c '"' name  check ']' @41 OK
@+2b want '[' got '3' (af82e2) then want '"' got '3' (af830e)
check(']') @+36 got '3'  -> HARD FAIL
```
-> platformLogin's field-4 reader (afa489) wants its element's 2nd slot to be
a LIST/STRING, not the accountID number. Therefore platformLogin's field 4 is
NOT the users list: the users triples belong in field 5, exactly as ssoStatus
(where the scalar `age` sat in field 4) proved.

REJECTED CallCloud-SET (21B, `[0,[["Player54196"]]]`):
```
@+00 '[' @+01 '0' status
@+03 '[' list (reader b32619)
@+04 '[' element
@+05 want '[' got '"'  -> element must open ANOTHER list (af7f47)
```
-> element = `[[names...], ...]` i.e. the same user-record shape. And the
EMPTY form `[0,[]]` was ACCEPTED by the same reader.

FIXES (server/ds2_server.py, restart 2026-09-19 22:24:44):
1. h_thqconnect_login_platformlogin: rotation REMOVED -> always serve the
   ssoStatus-PROVEN 6-field shape `[0,[],[],[name],0,[[[name],aid,pid]]]`.
2. _cloud_body: rotation REMOVED -> always serve `[0, users]` (triples), which
   satisfies b32619's demand for '[' at element depth 2 and keeps the proven
   empty-list behaviour when the list is empty.

Still unshaped (only if the game ever calls them): initSession/readSession
(_pos_arr + tail) - their `tail` adds a 7th positional that no run has yet
validated. The game hit only 4 endpoints this session:
cloud/execute(GET) OK, platformLogin FAIL, ssoStatus OK, cloud/execute(SET) FAIL.

NEXT RUN EXPECTS: zero "was not formatted correctly" for BOTH platformLogin and
CallCloud -> the async driver completes -> login advances.

## 2026-09-19 22:30 - ONE-LAUNCH SWEEP ARMED (platformLogin tail + cloud shape)

### Why a sweep instead of another guess
The shared async format-checker evidence so far:

| endpoint | body | verdict |
|---|---|---|
| ssoStatus | `[0,[],[],["X"],0,[[["X"],aid,pid]]]` | **ACCEPTED** (`response good`, no "not formatted") |
| platformLogin | `[0,[],[],["X"],[[["X"],aid,pid]],0]` | FAIL at +0x2b ("expect '[' got '3'") -> field 4 must be the scalar `age`, user triples belong in field 5 |
| CallCloud | `[0,[]]` | ACCEPTED; `[0,[["X"]]]` died at +0x05 |

platformLogin's *prefix* is therefore known (the same proven 6-field prefix
ssoStatus uses); only its **tail** (session/auth delivery) was open. Rotation
beats guessing: the game retries every ~2-3 s, so ONE launch walks every
candidate and the server tag + game verdict identify the winner.

### What is now served (server/ds2_server.py)
- `h_thqconnect_login_platformlogin` rotates 8 tails over the PROVEN prefix,
  tagged `platformLogin PLSWEEP#N/8 tail=...`:
  0 `[]` (format-only probe) - 1 `[sid]` - 2 `[auth]` - 3 `[sid,auth]` -
  4 `[[sid,auth]]` - 5 `[[uname],sid,auth]` - 6 `[[uname],sid,auth,aid,pid]` -
  7 `[[[uname],aid,pid],sid,auth]`
- `_cloud_body` rotates 2 shapes, tagged `cloud <tag> CLSWEEP#N/2`:
  0 `[0,[]]` (proven format-accepted) - 1 `[0,[[["X"],aid,pid]]]`
- Globals added next to the session state: `_SWEEP_LOCK`, `_PL_SWEEP`, `_CL_SWEEP`.

### Verified before handing over to the game
- `py_compile` clean; server restarted 22:29:18 (443 mTLS / 8443 client-first).
- Local probe (`tools/probe_sweep.py`, raw socket + `shutdown(SHUT_WR)` so the
  server's TLS peek completes) drove 8 platformLogin + 2 cloud requests and the
  server log shows every variant served in order (PLSWEEP#0..#7, CLSWEEP#0..#1).
  => the rotation logic is live and correctly tagged.
- Deployed client DLL is repo==steam identical (275968 B, SHA 028B1EFA...,
  09-16 build carrying the ARG/PEEK-with-return-address trace hooks).
  Game exe is the 09-08 trust-slot-patched build (18822656 B, 1A858521...).

### Next launch = winner identification
Correlate, for each `PLSWEEP#N`, the game's own verdict:
  `[GAME] ... response good`            -> format accepted (candidate winner)
  `[GAME] response was not formatted correctly` -> rejected
and the `>> ARG`/`>> PEEK ret=...` traces for the failing ones.
Whichever variant stops producing "not formatted correctly" is the login
shape; lock it, drop the sweep, then address the *semantic* need (the game
must find its sessionID/auth in the tail to leave the login state).

# Darksiders 2 Local Server — STATUS & CONTINUATION GUIDE
_(Written 2026-08-23 after ~60% progress; read this first in any new session.)_

## 2026-09-19 22:35 - SWEEP VERIFIED LIVE + winner prediction + journal hygiene

No game run needed this session; the sweeps were verified against the
running server with `tools/probe_sweep.py` (the probe does
`shutdown(SHUT_WR)` so the server's TLS peek releases and we get the body).

**Verified: the rotation is live and every variant is well-formed.**
Server log (`server/logs/server_new_stdout.txt`) shows the full cycle
served in order, each tagged:

```
platformLogin PLSWEEP#0/8 tail=[]                       body=[0,[],[],["X"],0,[[["X"],aid,pid]]]
platformLogin PLSWEEP#1/8 tail=["<sessionID>"]          body=<proven prefix>+tail
platformLogin PLSWEEP#2/8 tail=["<auth>"]               ...
platformLogin PLSWEEP#3/8 tail=["<sid>","<auth>"]       ...
platformLogin PLSWEEP#4/8 tail=[["<sid>","<auth>"]]     ...
platformLogin PLSWEEP#5/8 tail=[["X"],"<sid>","<auth>"]
platformLogin PLSWEEP#6/8 tail=[["X"],"<sid>","<auth>",aid,pid]
platformLogin PLSWEEP#7/8 tail=[[["X"],aid,pid],"<sid>","<auth>"]
cloud execute CLSWEEP#0/2 body=[0,[]]
cloud execute CLSWEEP#1/2 body=[0,[[["X"],aid,pid]]]
```

All bodies are valid positional JSON (no keys) — matching the decoded
grammar where the shared checker (`0xafa77d`, tokenizer `0xaf7d65`)
consumes a bare value sequence and NEVER keys.

**The prediction to test on the next launch (stated before the run):**
`ssoStatus`'s descriptor `0xd15f814` and `platformLogin`'s `0xd15f854`
are exactly 0x40 = **64 bytes apart**, consistent with the uniform
64-byte-strided descriptor table hypothesised earlier. Same stride + same
table => **same field layout**, therefore:

- **`PLSWEEP#0` should be the winner** (body is byte-identical in shape to
  the body ssoStatus was already proven to accept: 6 fields,
  `users` in position 5). The historical platformLogin failures are then
  fully explained by the OLD body putting `users` in position 4 (where the
  grammar has the scalar `age`) — not by a different schema.
- `CLSWEEP#0` (`[0,[]]`) is already proven accepted; `#1` is the reasoned
  non-empty fix. If the game needs document data it must accept `#1`.

Falsifiable outcome: if `#0` is rejected for platformLogin while the same
shape is accepted for ssoStatus, the descriptor stride hypothesis is wrong
and the descriptor pointers must be read at runtime (they are
heap-allocated: `0xd15f814` is outside the image, .data ends 0x18614e0).

**Journal hygiene done this session:** the `## CURRENT STATE` and
`## IMMEDIATE NEXT STEPS` static sections were still describing the
August `unknown_ca` blocker (fixed long ago) and the obsolete DER hunt.
Both now carry a clearly-marked `> SUPERSEDED 2026-09-19` block with the
current reality and the real next step, so a future session cannot be
misled by them. The dated log above remains the authority.


## GOAL
Revive Darksiders 2 (PC/Steam, appid 50650) online functionality locally,
including player item exchange. User launches game from this folder.

## CURRENT STATE (as of last session)
> **SUPERSEDED 2026-09-19.** The bullets below are from the August
> `unknown_ca` era and are NO LONGER TRUE. Current reality (read the
> dated session log at the end of this file for detail):
> - TLS/trust: **SOLVED.** 443 mTLS works (game presents CN=DS2), 8443
>   libcurl validates our leaf against the patched exe trust slots
>   (`0xed7a20` + `0xed76b8`, two DISTINCT CAs — duplicates break
>   `X509_STORE_add_cert`).
> - Steam ticket: **SOLVED** (proxy DLL synthesizes the cb163 response;
>   game logs `OnAuthToken - succeeded`).
> - The game sends REAL HTTPS requests to 8443 (`cloud/execute`,
>   `login/platformLogin`, `login/ssoStatus`).
> - **REMAINING BLOCKER (single):** the shared async response grammar
>   (`0xafa77d` / tokenizer `0xaf7d65`). `ssoStatus`'s 6-field positional
>   body is ACCEPTED; `platformLogin` and `cloud/execute` response shapes
>   are still being settled by the armed sweeps (PLSWEEP/CLSWEEP).
- **Working**: all hostnames -> 127.0.0.1 (hosts block); server listens TLS
  443+8443 (auto-detect) + HTTP 80; full local test suite passes (TLSv1.0
  handshakes on both endpoints, login flow, cloud store round-trip).
- **BLOCKING BUG**: the GAME still rejects our server cert: its TLS client
  aborts with alert 48 `unknown_ca`. Seen in server log as
  `[SSL: TLSV1_ALERT_UNKNOWN_CA] tlsv1 alert unknown ca`.
- Server currently presents `server/certs/server-clientca.crt` (signed by the
  ORIGINAL embedded Client CA key). Config flag `use_client_ca_cert: true`.
  Previous attempts with our own root (incl. EXACT original DN) also failed.

## KEY DISCOVERIES (verified)
### Topology (thqconnect.com DNS zone STILL LIVE, servers dead)
- Login/main: `thqc-darksiders2-pc.thqconnect.com` :8443 (was 38.103.18.162)
  - hostname built at runtime: `<appID>` + `.thqconnect.com`
  - port 8443 HARDCODED in binary: `mov eax,0x20fb` @ VA 0xB02CDF
    (ctor at VA 0xB02C62, caller 0xB04006 <- 0xB0A41B)
- GDS: `ds2.gds.thqconnect.com` :443 (was 38.103.18.186), path `/gds/rpc`,
  content-type `app/x-gds-rpc`; name built `ds2` + `.gds` + domain
- Also live: dev.thqconnect.com->38.103.18.181, staging->209.251.177.33
- Health check: `{serverPrefix}/version`

### Endpoints (all under https://<login host>)
Response envelope guess: `{resultCode:0,...fields..., output:{...}}` (both
shapes sent). Steam auth: hex-encoded GetAuthSessionTicket; SteamID64 at
offset 12 (LE) heuristic works for tests.
Strings: "Connected To THQ.Com, launching webkit", "Signing in to THQ.com"
=> LOGIN UI RUNS IN EMBEDDED EAWebKit BROWSER (likely schannel/WinInet ->
validates against WINDOWS CERT STORE, not just embedded CA).

### Embedded PKI material in Darksiders2.exe (.orig backup exists!)
File offsets in ORIGINAL exe:
- 0xf712d8  Root CA PEM slot, 1248 B  ("THQ Connect Root CA",
  DN: C=US/ST=California/L=Agoura Hills/O=THQ Inc./OU=THQ Connect/CN=...)
  NOTE: sits right after GDS config block `thqc-gds|https|/rpc|/gds/`
        => likely the GDS-trusted anchor specifically!
- 0xed76b8  leaf PEM slot, 870 B (*.thqconnect.com, self-signed-looking old)
- 0xed7a20  leaf PEM slot, 860 B (*.thqconnect.com, expired 2012)
- 0xf717c0  "THQ Connect Client CA" cert, 912B-ish (extracted to
  server/certs/client_ca.pem)  -- NOT patched, kept original
- 0xf71b48  Client CA RSA PRIVATE KEY (extracted to server/certs/client_ca.key)
- Only these 5 PEM blobs exist as plaintext. No other IPs/hostnames hidden.
- Original root DER len ~624 B (RSA-1024, SHA1).

### TLS facts
Game = OpenSSL 1.0.0 static: TLSv1.0 max; ciphers incl. ECDHE-RSA-AES256-SHA,
ECDHE-ECDSA-AES*-SHA, AES256-SHA, AES128-SHA, DES-CBC3, RC4.
ssl3_alert_code() maps X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT(_LOCALLY) ->
alert 48 unknown_ca => the observed alert means ISSUER LOOKUP BY DN FAILED:
the loaded CA set does not contain an issuer for our presented cert.

### Why previous fixes failed (hypothesis)
We patched the Root CA PEM slot + both leaf slots, but those slots appear to
be the GDS config's anchor (root sits inside GDS config struct). The LOGIN
service likely loads ITS trusted CA from a DIFFERENT blob that is NOT stored
as plaintext PEM (raw DER? XOR/compressed? built at runtime?). Hence every
anchor swap so far left the login path untrusted => unknown_ca.

## IMMEDIATE NEXT STEPS (in order)
> **SUPERSEDED 2026-09-19.** The list below is from the August era (DER
> hunt / proxy bootstrap) and is DONE or obsolete. Current next step:
> 1. **Launch the game** (server already running with the armed sweeps).
> 2. The game retries `platformLogin` every ~2-3 s, so ONE launch walks all
>    8 `PLSWEEP` tails; each response is tagged in
>    `server/logs/server_new_stdout.txt` as
>    `platformLogin PLSWEEP#n/8 tail=... body=...`.
> 3. Correlate each tag with the game's own verdict in
>    `E:\SteamLibrary\steamapps\common\Darksiders 2\ds2_tls.log`:
>    `[GAME] ... response good: N bytes` (= format ACCEPTED) vs
>    `... was not formatted correctly` (= rejected).
> 4. Whichever variant is accepted gets locked in and the sweep removed.
>    Same for cloud (`CLSWEEP#0/2` = empty list, proven accepted;
>    `#1/2` = user-triple element, reasoned fix).
> 5. **Prediction to test:** the endpoint descriptors sit on a uniform
>    **64-byte stride** (`ssoStatus` `0xd15f814`, `platformLogin`
>    `0xd15f854`), i.e. the SAME field layout — so `PLSWEEP#0` (no tail,
>    exactly the proven ssoStatus body) is the most likely winner, and the
>    earlier platformLogin failures were caused only by `users` sitting in
>    position 4 instead of 5.
1. **RUN** `python tools\find_der.py` (already written, never executed).
   Searches exe(.orig) + all game files for the original certs as RAW DER,
   plus single-byte-XOR variants of them. Any hit = the real loaded blob(s).
2. If DER hits found in exe.orig:
   - Replace those DER bytes with OUR CA/server DER. Length must match:
     tune by serial size (-set_serial 1..17-byte), optional SKI/AKI exts.
     For DER replacement do NOT newline-pad; byte-exact length required.
   - Update patch_exe.py accordingly (it currently handles PEM slots only).
3. If XOR hit found: locate region, decode around it, understand container,
   re-encode ours similarly.
4. If NOTHING found in exe: scan media/*.upak, DLC packs, webkit dir too
   (find_der.py already walks all files <300MB except Soundtrack).
5. Parallel track (user requested): build an x86 logging proxy DLL to trace
   TLS from client perspective. No 32-bit gcc present; easiest toolchain:
   `pip install ziglang` then
   `python -m ziglang cc -target x86-windows-gnu -shared proxy.c -o ws2_32.dll`
   exporting getaddrinfo/freeaddrinfo/connect/WSAStartup/WSACleanup +
   forwarders, rewriting *.thqconnect.com -> 127.0.0.1 and dumping socket
   buffers to a log. (Old Rust attempt existed; user said ignore it.)
6. NUCLEAR fallback (definitive): disable verification in the exe. Locate
   ssl_verify_cert_chain (pattern: call X509_STORE_CTX_init; call
   X509_verify_cert; mov [reg],error; call X509_STORE_CTX_cleanup) and force
   its return >=1. Use tools/find_xrefs.py + disasm.py (capstone installed).
   Confirming detail: ssl3_alert_code maps UNABLE_TO_GET_ISSUER* -> 48.

## INFRASTRUCTURE NOTES / GOTCHAS
- Shell = PowerShell: use `curl.exe` (not `curl`); NO `&&`; NO inline
  multi-line `python -c` (write .py files instead).
- write_to_file mangles `\n` `\r\n` escapes inside content -> build with
  chr(13)/chr(10) or bytes([...]) when writing code containing escapes.
- .bat files MUST be CRLF: regenerate via `python tools\make_bats.py`.
- hosts file: managed marked block `# >>> DS2 LOCAL SERVER >>>` ...
  `# <<< DS2 LOCAL SERVER <<<`; entries include prod/s/thqconnect/gds/
  ds2.gds/dev/staging/thqc-darksiders2-pc.thqconnect.com. User's unrelated
  entry preserved: `94.228.116.208 utl-tlt.loc`.
- Resolve-DnsName IGNORES hosts; use [System.Net.Dns]::GetHostAddresses.
- Server auto-detects per-connection TLS (first byte 0x16) vs plain HTTP;
  logs handshake failures, malformed requests, unknown protocols w/ hex.
- Config server/config.json: http_port 80, https_ports [443,8443],
  enable_tls true, use_client_ca_cert TRUE (current mode),
  verify_client_cert false, tunnel_poll_timeout_s 25.
- Tests (server running): tools\test_tls10.py (game-like TLSv1.0 both
  endpoints), tools\test_login_host.py (real login name), 
  tools\test_login_flow.py (login+cloud round trip).
- Cert slots (PATCHED exe): root@0xf712d8<=1248B, leaves@0xed76b8<=870B &
  0xed7a20<=860B. Current kit: ECDSA P-256 leaf CN=*.thqconnect.com SAN
  *.thqconnect.com+*.gds.thqconnect.com (856B) + root w/ EXACT original DN
  (1044B). Alt: server-clientca.crt signed by embedded Client CA key.
- capstone installed via pip (tools/disasm.py disassembles VA regions).

## KEY FILES (project map)
- `server/ds2_server.py` — emulator (endpoints, cloud store, tunnel, admin)
- `server/config.json` — runtime config (ports, flags, news)
- `tools/build_certs.py` — cert kit gen (slot-fitting combo search)
- `tools/patch_exe.py` — exe patcher (idempotent, from .orig)
- `tools/hosts_entries.ps1` — hosts add/remove (elevated, atomic writes)
- `tools/install_ca.ps1` — installs CA into Windows cert stores
- `tools/make_bats.py` — regenerates the 3 launcher .bat (CRLF!)
- `tools/find_der.py` — **CREATED, NOT RUN YET**: DER/XOR blob hunter
- `tools/disasm.py` — capstone disassembler (VA start, length)
- `tools/find_xrefs.py` — string VA -> code xref finder
- `tools/find_callers.py` — find E8/E9 callers of a function VA
- `tools/test_tls10.py` — game-like TLSv1.0 test (both endpoints)
- `tools/test_login_host.py` — real login hostname check
- `tools/test_login_flow.py` — full login+cloud flow
- `tools/scan_*.py` — various recon scripts (see names)
- `Darksiders2.exe.orig` — pristine backup
- `README_DS2_SERVER.md` — user-facing docs
- `tools/inventory_blobs.py` — enumerates ALL PEM blobs in both exes (-> tools/blob_inventory/)
- `tools/dump_imports.py` — PE import table dump (pefile)
- `tools/tls_proxy/tls_proxy.c` — client-side TLS logger, ships as proxy DINPUT8.DLL
- `tools/tls_proxy/build.bat` — builds it (VS2022 vcvarsall x86 + cl)
- `tools/tls_proxy/test_host.c` — tiny 32-bit loader for smoke tests

## SESSION LOG — 2026-08-24

### `unknown_ca` root cause FOUND (old config was hopeless)
- `server/certs/client_ca.pem` is **NOT a CA**: Subject=`CN=DS2`,
  Issuer=`THQ Connect Client CA`. It is the game's **client certificate**
  (mutual TLS). The string "Client CA" lives only in its ISSUER DN —
  `build_certs.extract_client_ca()` misidentified it because of that.
- The REAL THQ Connect Client CA cert is **not embedded** in the exe at all
  (`find_der.py`: zero plain/XOR DER copies of any embedded cert outside
  their PEM slots; no other file contains them either).
- Hence `server-clientca.crt` was minted with the wrong key (a leaf key)
  and carries nonsense Issuer `CN=DS2` -> nobody can trust it.
  `openssl verify` confirms: "unable to get issuer certificate".
  => `use_client_ca_cert` set back to **false** in server/config.json.

### Complete embedded-trust inventory (Darksiders2.exe.orig)
| offset | size | what |
|---|---|---|
| 0x00ED76B8 | 870 | login leaf `*.thqconnect.com`, issuer "Vancouver/THQ Inc." style, **expired 2022-04-10**, possibly self-signed/pinned |
| 0x00ED7A20 | 860 | GDS leaf `*.thqconnect.com`, **expired 2012-02-06** |
| 0x00F712D8 | 1248 | THQ Connect Root CA (valid till 2030) |
| 0x00F717C0 | 898 | client cert CN=DS2 (expired 2021), issuer CA cert absent |
| 0x00F71B48 | 886 | RSA private key matching the CN=DS2 client cert |

Patched exe verified: leaf slots A/B + root slot replaced with ours;
client cert + key untouched. Exe hash differs from .orig (patch applied).

### NEW: client-side TLS handshake logger (proxy DLL) — READY
- Ships as `dinput8.dll` next to the game (real one copied to
  `dinput8_real.dll`; all 6 exports forwarded through naked thunks).
  NOTE: exports are attached via `__pragma(comment(linker,...))` INSIDE
  tls_proxy.c — passing /EXPORT on the cl/link command line did NOT
  resolve symbols; keep using the pragmas.
- Hooks WSOCK32/WS2_32 IAT entries (send/recv/connect/closesocket/
  sendto/recvfrom/getaddrinfo) of EVERY module, rescans every 0.7 s.
- Live TLS dissector -> `<game dir>\ds2_tls.log`: ServerHello+cipher,
  Certificate chain sizes, CertificateRequest incl. acceptable-CA DNs
  hexdump, alerts by name (48 => "GAME REJECTED THE SERVER CERT").
  Also writes Wireshark-ready `ds2_tls.pcap`.
- Runtime smoke test PASSED (test_host.exe loads it; banner logged).
- USAGE: just launch Darksiders2.exe normally while the local server runs;
  read `ds2_tls.log` afterwards. Remove/delete dinput8*.dll to disable.

### NEXT STEPS
1. Restart server (now presents `server.crt`: our leaf signed by our root
   which IS patched into the exe's root slot). Play/launch game once with
   proxy active, then inspect `ds2_tls.log`:
   - Still alert 48 AFTER our Certificate arrives => game ignores the
     patched ROOT slot; likely pins the ORIGINAL (self-signed?) leaves
     exactly. Plan B: generate OUR leaf as **self-signed** (issuer==
     subject) sized into the 870/860 slots, so an exact-pin match hits.
     We cannot reuse original leaves — their keys are gone.
   - If our server sends CertificateRequest (verify_client_cert on) the
     client offers CN=DS2 — leave verify off for now.
2. Optional: hook more (gethostbyname result logging) if DNS issues show.
3. Housekeeping: first half of this file duplicates lines 32–61 (truncate
   artifacts); safe to delete one copy.

## SESSION LOG ADDENDUM — 2026-08-24 later ("silent proxy" solved)

Symptom after first game run: ds2_tls.log empty/absent while game ran.
Findings, in order:
1. Loader behaviour PROVEN fine: tools/tls_proxy/test_import.c is a tiny
   exe statically importing dinput8!DirectInput8Create; run from the game
   dir it reports "loaded from: ...\\DINPUT8.dll" (our proxy wins the
   search order). NOTE pitfalls when reproducing: taking only the ADDRESS
   of a dllimport lets /OPT:REF strip the whole import (exe imported just
   KERNEL32!); you must make a REAL call, and force retention via
   #pragma comment(linker,"/INCLUDE:__imp__DirectInput8Create@20").
2. Actual cause of silence: open_logs() used CREATE_ALWAYS without
   FILE_SHARE_DELETE; a leftover test process kept holding the old log,
   so every later CreateFileW hit a sharing violation -> handles stayed
   INVALID_HANDLE_VALUE and ALL logging silently no-oped (tlog checks the
   handle). One 0-byte truncation at 15:08:26 was such a race.
   FIX: open_one() now opens with FILE_SHARE_READ|WRITE|DELETE and falls
   back to "ds2_tls_<pid>.log", then to %TEMP%. Zombie test processes get
   killed before sessions (Get-Process test_host,test_import | kill).
3. Hardened worker: banner written BEFORE any scanning, entire PEB walk
   wrapped in outer __try, pointer sanity checks (alignment + user-space
   range + PE magic + x86-only magic 0x10b), heartbeat line every ~30 s
   ("scan pass N complete - alive"), env kill switch DS2_TLS_OFF=1.
4. Verified post-fix: banner + "scan pass 1 complete" both logged.

Current status going into next game run:
- Server restarted 15:04 with use_client_ca_cert=false AND STILL got
  unknown_ca (15:04:43 .. 15:07:41, three-shot retry pattern each time).
  So even our-root->leaf presentation fails => expect Plan B (self-signed
  leaf pinned into slots A/B) OR the proxy will show something else
  entirely (e.g., CertificateRequest handling, hostname check, etc.).
- ACTION FOR NEXT RUN: launch Darksiders2.exe once, quit at error, then
  read ds2_tls.log (+ .pcap in Wireshark if needed) and decide:
    a) no ClientHello logged => traffic never leaves game (DNS/proxy?)
    b) ClientHello+Certificate then alert 48 => pinning confirmed -> Plan B
    c) anything else => follow evidence.

## SESSION LOG ADDENDUM 2 — 2026-08-24 evening (Rust pivot)

C binkw32 build hit an inexplicable linker state: obj demonstrably
contained all `_t_*` SECT4 External definitions (dumpbin-verified), yet
link.exe reported every one LNK2001 — via pragma /EXPORT, cmdline
/EXPORT AND .def routes alike. User proposed Rust; pivoted.

### Rust implementation (tools/tls_proxy/rust/, pure std, zero crates)
- cdylib named binkw32, target i686-pc-windows-msvc (pre-installed),
  static CRT via .cargo/config.toml rustflags crt-static.
- src/lib.rs: full port of the C engine — logging w/ fallback chain
  (share-all handles!), pcap writer, sock table, TLS dissector,
  WinSock hooks (send/recv/connect/closesocket/getaddrinfo), IAT
  patcher (hand PE parse), Toolhelp32Snapshot module enum (NOT raw PEB
  walk), worker thread, DllMain override, DS2_TLS_OFF kill switch.
- src/exports_gen.rs: GENERATED by gen_exports_rs.py from
  binkw_exports.def (77 entries). Rebuild:
    cd tools\tls_proxy\rust && cargo build --release --target i686-pc-windows-msvc

### GOTCHA that ate hours (record for posterity)
On i686-pc-windows-msvc LLVM decorates `extern "stdcall"` symbols
itself (`_name@N`). `#[export_name = "_BinkOpen@8"]` therefore produced
DOUBLE-decorated `__BinkOpen@8@12`. Fix: export_name must be the
UNDECORATED base ("BinkOpen"); automatic decoration then lands exactly
on the `_BinkOpen@12`-style names the game imports.
(The C failure with near-identical symptoms — symbols present but
"unresolved" even via .def — was never root-caused; sources kept under
tools/tls_proxy/*.c for reference.)

### Status now
- root\binkw32.dll       = RUST proxy (232,960 B, build exit 0)
- root\binkw32_real.dll  = pristine original RAD library
- Smoke test PASSED: banner + "main exe IAT: N thunk(s)" logged from a
  live x86 process; all 77 forwards resolved against the real dll.
- dinput8 proxy files REMOVED from game root (retired vector).
- Server runs corrected config (use_client_ca_cert=false); unknown_ca
  persists server-side -> client-side truth is the next unlock.

### MILESTONE: TLS HANDSHAKE COMPLETES ✅
Deploying our PATCHED exe to the Steam directory (the game launches from
E:\SteamLibrary\steamapps\common\Darksiders 2\ via Steam) resolved the
unknown_ca issue. Proxy log (ds2_tls.log in Steam dir) proves the full
TLS 1.0 handshake completes: ClientHello -> ServerHello -> Certificate
-> KeyExchange -> ServerHelloDone -> ClientKeyExchange ->
ChangeCipherSpec (both sides) -> Finished. NO unknown_ca alert!
The game then sends a small encrypted message (~37 bytes = likely a
close_notify or app-level check) and closes. Server processes silently.
Remaining investigation: why the game disconnects after handshake

---

## 2026-09-24 04:41 - platformLogin GATE CLOSED (verified in-game)

The "EXPECTED ON NEXT LAUNCH" block above is **superseded**. The assumption
`platformLogin == ssoStatus` (byte-identical 6-field body) was the last wrong turn:
platformLogin's body is bound by the **config-document binder**, whose 5th document
slot is `globalSettings` - a LIST/OBJECT. Parking that body's scalar `0` there is
what aborted at byte 25 every run from 09-18 to 09-23.

### Proof (run 04:40:44 -> 04:42:12, `ds2_tls.log` / `round_0924b_stdout.txt`)
Server: `ORACLE platformLogin v0/8 #1 bytes=15 body=[0,[],[],[],[]]`
Client: one clean pass, no mismatch, no retry:

```
04:41:00.207 AccountPlatformLoginAsync - response good: 15 bytes, [0,[],[],[],[]]
  '['@+0 af7e47 | '0'@+1 af7f47 (result) | '['@+3 afa25b (tunnels)
  '['@+6 afa372 (certificates) | '['@+9 afa489 (gameSettings)
  '['@+c afa489 (globalSettings = the old byte-25 gate) | check ']'@+e af7edf OK
04:41:00.209 DeleteJob[7] AccountPlatformLoginAsync
```

**No `response was not formatted correctly`** - first time in the project's history.
ssoStatus re-passed unchanged in the same pass (`PARSE #4`, 45/45 bytes, `DeleteJob[8]`),
and both Steam tickets were accepted (`OnAuthToken - succeeded` for 28 and 29).

### Frozen
`server/oracle/platformLogin.json` = `[0,[],[],[],[]]` (single-file oracle path = no
variant cycling; live-verified `ORACLE platformLogin #2 bytes=15 ...`). The 8 sweep
candidates are parked in `server/oracle/parked/` for future mining of non-empty
`tunnels`/`certificates`/settings if a later stage needs them.

### Not a gate (do not chase)
`cb163 SYNTH: mgr+0x828 m_hAuthTicket=0 vs synth .. [MISMATCH] / NOT setting state` is the
+1500 ms late synth firing after the manager consumed the ticket. Identical line in the
09-23 session; cosmetic.

### Session shape after login
cloud GET -> cloud SET -> platformLogin -> ssoStatus -> idle (client's resting state; 09-23
idled 9.5 min the same way). The 09-24 session was closed by the user 70 s later with a clean
`AccountInternal::Disconnect` / `~WorldwideLoginService` / close_notify / `SteamAPI_Shutdown`.

### Next
Launch and play: the same 4-request pattern should repeat, all responses accepted. Verify the
online feature that matters (cloud docs `TomeGift`/`GameToWeb` = item exchange) in-game.

(possibly hostname/CN check, protocol detail, or app-level logic).

### RESOLVED: Entry Point Not Found on first game launch
Game dialog: "_BinkWaitStopAsyncThread@4 could not be located ...".
Root cause (eat_names.py proof): rustc publishes UNDECORATED EAT names
(BinkWaitStopAsyncThread) while keeping decorated SYMBOLS (_BinkWait...
@4) — link succeeds, but the game imports the DECORATED string.
FIX: tools/tls_proxy/decorate_eat.py post-processes the built dll —
each EAT name RVA is repointed at the matching DECORATED string that
already exists in .rdata (the shims' fwd() constants). Result: EAT
byte-identical in style to the original (77 decorated + DllMain).
Rebuild one-shot: tools\tls_proxy\build_rust.bat
(cargo -> copy to root -> decorate). Verified: dumpbin now lists
_BinkWaitStopAsyncThread@4 plainly, smoke test still passes.

### ITERATION: dll loads in game, but only 1/5 hooks + worker hang
Game run 21:16 proved in-game load: ds2_tls.log had fresh banner AND
"main exe IAT: 1 thunk(s) redirected" (that one = WS2_32!getaddrinfo).
Two bugs identified & fixed:
1. WSOCK32 IAT match failing -> only getaddrinfo hooked, so the game's
   send/recv/connect flowed unhooked. Likely missing/garbage
   OriginalFirstThunk handling. FIX: address-based fallback matching —
   patch_module now resolves each target's REAL address via
   GetProcAddress(GetModuleHandleW("WS2_32/WSOCK32")) and hooks any IAT
   slot whose current value equals it, regardless of name tables.
2. Worker hung inside first scan_all() (no "scan pass 1 complete" ever;
   Rust has no SEH so a hang is silent). Diagnostics added:
   "scan pass N starting" line before each scan, PANIC HOOK writing to
   ds2_tls_panic.log + main log. NOTE: even if background scans hang,
   the exe IAT hooks installed at t=0 capture all game traffic, so TLS
   data collection no longer depends on the scanner.

Rebuilt+deployed via build_rust.bat (exit 0, EAT re-decorated, smoke OK).

### ROOT CAUSE FOUND: game imports WSOCK32 BY ORDINAL + missing NUL
Diagnostics (dump_ws_imports) revealed:
- exe imports WSOCK32.dll (26): #13, #1, #101, #10, #52, ... — ALL BY
  ORDINAL (not by name!). These map to ws2_32 ordinals:
  #19=send #16=recv #4=connect #3=closesocket #23=socket etc.
- exe imports WS2_32.dll (0): the WS2_32 descriptor has OFT=0 (bound
  imports), so the name-table walk sees nothing.
This means name-based IAT matching can NEVER work for this game.
The address-based fallback SHOULD work — but it found 0 matches
because of a second bug: tgts() names were b"send" (no NUL
terminator!) — GetProcAddress needs a C string, so every call
returned 0 and amap ended up EMPTY.
FIX: NUL-terminate all target names (b"send\0" etc). Also added
amap diagnostics (module handles, entry count, GetProcAddress
failures) so any future issue is visible in the log.

### RESOLVED (final): decorated EAT — decorate_eat.py IS the fix
After rebuilding with /EXPORT link-args, dumpbin showed
"BinkWaitStopAsyncThread = _BinkWaitStopAsyncThread@4" which LOOKED
decorated — but `eat_names.py` (reads actual EAT name strings) proved
0/78 names contained '@', and a 32-bit test_probe.exe calling
GetProcAddress("_BinkOpen@8") returned MISSING. /EXPORT creates an
export-symbol ALIAS (dumpbin's "=" column) yet leaves the EAT name
string undecorated -> the loader still won't match the game's
decorated import.
DEFINITIVE FIX: post-process EAT name RVAs so they point at the
decorated strings already present in the image (tools/tls_proxy/
decorate_eat.py). Verified via test_probe.exe:
  _BinkWaitStopAsyncThread@4 FOUND, _BinkOpen@8 FOUND,
  _BinkDoFrame@4 FOUND, _BinkBufferBlit@12 FOUND; EAT 77/78 '@'.
build_rust.bat now always: cargo build -> copy -> decorate_eat.py.
NOTE: the .cargo/config.toml /EXPORT link-args are harmless but not
the mechanism that worked; kept out of the critical path.


## 2026-09-22 03:1x - platformLogin GRAMMAR PROVEN + SWEEP RETIRED (static proof)

### THE STATIC PROOF (no launch needed)
The `X` sweep for platformLogin was a wild goose chase. The reason: the login
checker `0xafa77d` (`"response was not formatted correctly"`) has exactly ONE
caller, at `0xb02ac8`, and that call site pushes:

    push 1            <- flags = 1   (bit1 CLEAR -> NO 7th jobID field)
    push 0x13c5271    <- the SHARED "Object" schema descriptor

That is the IDENTICAL checker + descriptor pair that ssoStatus already
satisfies. Conclusion: **there is no platformLogin-specific grammar.** The
proven 6-field positional body IS the platformLogin body.

Where the old (wrong) theory came from: reader `0xafa442` was attributed to
platformLogin element 4. It is NOT. `0xafa442` belongs to the **443 config-doc
parser** (`gameSettings` / `certificates` / `globalSettings` - the document the
game pulls on the 443 channel), whose element grammar really is
`[{ "key": [...], "value": [...] }]`. The login checker reads its `users`
field through `0xafa559` instead. Confusing the two produced the phantom
"element 4 = list of key/value objects" requirement and ~8 wasted sweep
variants.

### WHAT CHANGED
* `h_thqconnect_login_platformlogin` now returns exactly
  `self._pos_arr(self._pos_base(actor, status=0))` - byte-for-byte the same
  expression as ssoStatus. The handler's rich `output` dict (sessionID, auth,
  profileInfo, tunnels, ...) was the source of the extra/misordered fields and
  is no longer used for the wire body. Session state now travels OUT OF BAND
  through the session registry + `readSession` (required anyway, since flags=1
  forbids a 7th field).
* platformLogin sweep retired (`_PL_SWEEP` removed); oracle-override mechanism
  kept for future grammar probing. `server/oracle/` is EMPTY (verified 0
  entries) so nothing shadows the handler.

### VERIFIED LIVE (server restart 2026-09-22 03:13:13)
443 mTLS + 8443 client-first HTTPS + :80 all up. Direct TLS probes over 8443:

    platformLogin  HTTP/1.1 200 OK  bytes=67
      [0,[],[],["Player2112"],0,[[["Player2112"],3216317459,3216317459]]]
    ssoStatus      HTTP/1.1 200 OK  bytes=67
      [0,[],[],["Player2112"],0,[[["Player2112"],3216317459,3216317459]]]

`platformLogin == ssoStatus` -> **True** (byte-identical).
`py_compile` exit 0.

### FOLLOW-UP RISK (not yet blocking - nothing exercises it)
`readSession` (109 B) and `initSession` (102 B) append tail fields AFTER the
6-field base, producing a 7th+ element. If either is ever routed through the
same checker with flags=1, bit1 clear means the 7th field is illegal. The game
has only ever requested platformLogin / ssoStatus / cloud/execute / getNews, so
this is latent. If a launch ever shows a format error on readSession or
initSession, drop their tails to the bare 6-field base first.

### EXPECTED ON NEXT LAUNCH
platformLogin should now log the same shape that makes ssoStatus pass:

    [GAME] AccountPlatformLoginAsync - response good: 67 bytes
    [GAME] (NO "response was not formatted correctly")

That is the last known gate before the game proceeds from login into the menu.

## 2026-09-24 05:3x - CLOUD DOC STORE FIXED + getNews FORMAT DECODED (item exchange)

Three separate bugs killed the online item exchange (`TomeGift`/`GameToWeb`)
and the news ticker. All fixed and verified live over real TLS.

### BUG 1 - identity: the write and the read were DIFFERENT accounts
`account_from_request()` matched none of the parameters the cloud endpoints
actually send, so every request fell through to the params-hash fallback and
hashed the per-request `auth` ticket blob. Steam re-encrypts that blob on
EVERY request, so the MAP_SET and the following MAP_GET mapped to two different
accounts. Evidence in the store: ~23 hash-named dirs, each holding only the
210-byte `{"":""}` permission registration, while the real document landed in
yet another hash dir.
FIX: `profileNames=/steam/<steam64>` resolves FIRST (the game sends
`profileNames=%2fsteam%2f2534977164%2c...`), `auth` second (the ticket does
embed the SteamID at offset 12), then the numeric ids; the hash fallback is
last-resort only. The game's real parameter set never reaches it now.

### BUG 2 - `_cloud_body()` ignored the doc store
It answered a hardcoded `[0,[]]` for every op except getKeys, so a READ could
never return the document the game had just written.
FIX: serve the stored documents as

    [0,[{status,document,docPolicyBits,lastModifiedTime,lastModifiedProfileName}]]

built from the fields the client's reader actually walks. The game sends NO
`document` parameter on a read, so the payload comes from the store
(`values[docKey]` of the registration) - that is the whole point of the store.

Plumbing corrected with it: `requested_doc_keys` (a request carries
`docKeys=TomeGift,GameToWeb` / `docKeys=GET,GET`; the singular `docKey` is also
accepted), `param_slot` (parallel comma-separated lists), `doc_content`,
`apply_doc_perms`, `_load_doc`, `cloud_execute`. An empty `keys`/`values`
registration no longer writes a junk `{"":""}` document.

### BUG 3 - getNews is NOT the shared login grammar
`news/getNews` has its OWN format checker: `0xb32012`, single caller `0xb20c71`.
It opens the container (`0xaf7e16`) and then demands a NAMED object directly
inside it: `{ "result": <num>, "news": [ ... ] }`. The positional login body
could never pass this. The news-ITEM reader `0xb31c90` is positional and still
undecoded, so the safe body is an EMPTY list:

    h_thqconnect_news_getnews -> [{"result":0,"news":[]}]

(added `param_int()` so a repeated/blank/junk `limit` cannot 500 the handler).

### BONUS BUG - a READ's `policy=0` placeholder clobbered the stored permission
`cloud_execute` applied the perm parameters on EVERY op, so the `policy=0` a
client puts on a read overwrote the `docPolicyBits` that came from the
registration write. FIX: perm application is skipped for read-only ops
(`MAP_GET`/`GET`/empty op). Negative perm slots still mean "unchanged"
(see the `apply_doc_perms` docstring).

### VERIFIED
* `tools/test_cloud_exchange.py` (new, hermetic - redirects `CLOUD_DIR` to a
  temp dir, reads the real store read-only): **28/28 checks pass**.
  `python tools\test_cloud_exchange.py`
* Live end-to-end over 8443 (`tools/test_login_flow.py`, updated to the three
  real grammars; server restarted 05:28:43, `round_0924c_stdout.txt`):

      initSession  sessionID = c50081...  slots = 7
      platformLogin body = [0,[],[],[],[]]
      ssoStatus    status = 0  username = ['76561197960287930']  users = [[[...],22202,22202]]
      cloud MAP_SET  status = 0  docs = 1
      cloud MAP_GET  docs   = 1        <- was 0 ([0,[]] hardcoded) before the fix
         doc fields = ['status','document','docPolicyBits','lastModifiedTime','lastModifiedProfileName']
         text = "<Stats> \n\t<ComboNumber Value='18'/>...  (byte-identical)
      exchange read (TomeGift) = 0 docs  <- nothing registered this session; correct
      cloud getKeys  body   = [0,[]]
      news result = 0  items = 0  root = list   -> [{"result":0,"news":[]}]
* `py_compile` clean for `server/ds2_server.py` and both tools.

### EXPECTED ON NEXT LAUNCH
No `response was not formatted correctly` for `getNews`, and the item-exchange
UI lists the documents the game itself registered (the GET after a SET now
returns them). A fresh save registers no TomeGift document yet, so an empty
exchange list there is legitimate; the game writes one as soon as it has items
to publish.

### HOUSEKEEPING / ACCOUNT-DIRECTORY CHANGE (read this before deleting anything)
The game logs in as `profileNames=/steam/76561198000000001`, so the fixed server
now uses accountID **100000002** (`steam64 & 0xFFFFFFFF`) for every cloud call.
Every other dir in `server/data/cloud/` is a PRE-FIX artifact:
* ~20 junk dirs holding only the 210-byte `{"":""}` registration (the read's
  empty `keys`/`values` + the old hash identity),
* `2534977164/GameToWeb.json` (1706 B, 04:49:29) - the hash-derived dir that
  received the REAL Stats document the game uploaded; this is also the fixture
  the new hermetic test reads (`real_stats_doc()`).
None of them can hurt: they are inert, and the game MAP_SETs its full Stats
document on every login, so `100000002/` repopulates by itself on the next
session. To carry the old stats over before that, copy
`2534977164/GameToWeb.json` to `100000002/GameToWeb.json` (same format: a
`values` dict keyed by document name plus the perm fields).

### OPTIONAL NEXT
Decode the news-ITEM field names (`tools/news_schema.py`, region
`0xb31c00-0xb32012`) if a populated `news` array is ever wanted.

## 2026-09-24 05:48 - IN-GAME LAUNCH FALSIFIED "NAMED FIELDS"; THE WIRE IS POSITIONAL

The first launch after the 05:3x fixes reached the exchange point. Result:
BOTH bodies were still rejected, and both printed the "good" AND the "bad"
verdict for identical bytes (the "good" line is just the byte-count/parse step):

    GetNewsAsync  - response good: 24 bytes, [{"result":0,"news":[]}]
    GetNewsAsync  - response was not formatted correctly: <same body>
    CallCloudAsync- response good: 1598 bytes, [0,[{"status":0,"document":"<Stats> ...
    CallCloudAsync- response was not formatted correctly: <same body>

### THE DECODE (hook trace + disassembly, no guessing)

The trace lines are the checker walking the RAW body; `ret=` is the call site
INSIDE whichever primitive called `expect()` (not the checker):

    news  [{"result":0,"news":[]}]                      24B
      expect '[' got '[' @+0/18 ret=af7e47   container open (0xaf7e16)  OK
      expect '[' got '{' @+1/18 ret=af7f47   <- DIES: scalar reader (0xaf7f06)

    cloud [0,[{"status":...}]]                          1598B
      expect '[' got '[' @+0/63e ret=af7e47   container open            OK
      expect '[' got '0' @+1/63e ret=af7f47   resultCode (scalar)       OK
      expect '[' got '[' @+3/63e ret=b32619   `data` list (0xb325d2)    OK
      expect '[' got '{' @+4/63e ret=af7e47   element: no '[' -> '{'    OK
      expect '{' got '{' @+4/63e ret=af7e5a   ... object branch taken   OK
      expect '[' got '"' @+5/63e ret=af7f47   <- DIES: scalar reader hit the KEY

`ret=af7f47` is the `[` probe inside 0xaf7f06, the SCALAR token scanner
(optional '[' prefix, optional '"' prefix, then digits/'.'/'-'), reached through
0xaf8243 -> 0xaf80e2 -> 0xaf803e -> 0xaf7f06. `0xaf803e` uses ONLY its
parser/out arguments: the pushed name literals ("result", "news", "resultCode",
"data", "status", "document", ...) are DEAD METADATA and no key is ever
compared. On a keyed element the scanner sees the key's `"` as a quoted NUMBER,
consumes it, then needs digits and finds a letter -> the whole walk dies.

Proof the bodies are positional: the ACCEPTED `[0,[]]` (05:47:01, the 6-byte
CallCloud answer) walks exactly

      expect '[' got '[' @+0/6 ret=af7e47      expect '[' got '0' @+1/6 ret=af7f47
      expect '[' got '[' @+3/6 ret=b32619      expect ':' got ']' @+5/6 ret=af7eb0
      check  ']' got ']' @+5/6 ret=af7edf      expect ':' got EOF @+6/6 ret=af7eb0

and every accepted login/sso body is a bare positional array too.

Decoded checkers (callers found with tools/find_calls.py):

    CallCloud  0xb3280a   open '['/'{' -> 0xaf8243 "resultCode" -> 0xb325d2 "data"
                          -> close          (call site of 0xb325d2: 0xb3288a)
    doc      0xb325d2     loop -> 0xb32450 per element (bracket-matched at
                          0xb326c3: ']' only closes a '['-list)
    doc elem 0xb32450     open -> status (0xb3240c/0xb320cb: BYTE, enum -2..13;
                          out of range logs "Please upgrade client, enum changed")
                          -> document (0xaf8288: escape-aware string, \\ and
                          \\uXXXX, 4096-byte buffer)
                          -> docPolicyBits (0xaf8243 dword)
                          -> lastModifiedTime (0xaf803e scalar -> _atoi64)
                          -> lastModifiedProfileName (0xaf8645 -> 0xaf8288)
                          -> close          (the string reader DOES call 0xaf7e6b)
    getNews   0xb32012    open -> 0xaf8243 "result" -> 0xb31f0a "news" -> close
    news list 0xb31f0a    '['/'{' then 0xb31c90 per item (still undecoded)
    misc      0xaf7e6b    after every value: ',' or a lenient ':' probe (hence
                          the `want=0x3a ':'` lines - harmless)
              0xaf7d86 skip_ws   0xaf7d65 peek   0xaf7d27 consume
              0xaf7db8 optional-char+flag (trace tag "expect")
              0xaf7def required-char      (trace tag "check")




### FIXES (server/ds2_server.py)

* `h_thqconnect_news_getnews` -> `[0, []]` (positional result + empty item list;
  the item reader 0xb31c90 is still undecoded, so the list stays empty).
* `_cloud_body` -> each document is a positional record:
  `[status, document, docPolicyBits, lastModifiedTime, lastModifiedProfileName]`
  i.e. body `[0,[[0,"<Stats> ...",1,1790218098,"Player42002"]]]` - no key names.

### NEW VALIDATION (no game launch needed)

* `tools/sim_reader.py` - offline x86-32 port of the client walk (every primitive
  with its VA). Self-test: `python tools/sim_reader.py` -> 7/7 cases as expected.
  It reproduces the recorded traces LINE FOR LINE (offsets, want, got, ret):
  `[0,[]]` 6/6, keyed 1598B body 6/6, keyed news body 2/2 - see `tmp_simcmp.py`.
* `tools/strs.py` - dump the literal at a VA (the disassembler cannot annotate
  the type-name/SEH constants).
* `tools/test_cloud_exchange.py` - 30/30 checks, now asserting the POSITIONAL
  record and using sim_reader to prove a keyed element is rejected.
* `tools/test_login_flow.py` - updated to the positional grammars + run live:
  cloud MAP_GET docs=1 `status=0 policy=0 time=... name=...`, news `[0,[]]`
  (result 0, items 0). Server restarted with the fix (log
  `server/logs/round_0924d_stdout.txt`; the flow test also rewrote the leftover
  `22202/GameToWeb.json` account dir - harmless, the game uses 100000002).

### EXPECTED ON THE NEXT LAUNCH

No "was not formatted correctly" for `GetNewsAsync` and none for the
`CallCloudAsync` that returns the stored document. The `data` list now carries
the game's own document, so the exchange read (`MAP_GET TomeGift`, plus the
GameToWeb stats read-back) arrives as a proper record instead of dying on the
first key.

### STILL OPEN

* The news ITEM reader `0xb31c90` (positional) - needed only for a populated
  `news` array.
* The 05:48:18 GET proves the client does read a served document; if the UI
  still lists nothing, decode how the caller of `0xb3280a` consumes `out+8`
  (the `data` vector) after the format gate passes.

## 2026-09-25 09:25 - LIVE PROOF: THE FORMAT GATE IS FIXED (ZERO REJECTIONS)

Session `09:25:29 - 09:27:28`, served by pid 23480 (the FIXED code, log
`server/logs/round_0924d_stdout.txt`). Client log
`E:\SteamLibrary\steamapps\common\Darksiders 2\ds2_tls.log`:

    09:25:29.169 [GAME] scheduling WorldwideGetNewsAsync
    09:25:29.575 [GAME] Running .../news/getNews?limit=32&...&auth=...
    09:25:29.716 [GAME] GetNewsAsync - response good: 6 bytes, [0,[]]
    09:25:30.426 [GAME] Running .../cloud/execute      (POST, Content-Length: 2791)
    09:25:30.599 [GAME] CallCloudAsync - response good: 1517 bytes,
                          [0,[[0,"<Stats> \n\t<ComboNumber Value='18'/>...
    09:26:22.543 [GAME] Running .../cloud/execute?...&ops=MAP_GET&docKeys=TomeGift
    09:26:22.682 [GAME] CallCloudAsync - response good: 6 bytes, [0,[]]

NOT ONE `response was not formatted correctly` in the whole session. A scan of
the entire 272 MB client log finds only 12 ever, and the newest two are
05:48:17/05:48:18 - the pre-fix failures. The last rejection now predates the fix.

What each line proves:

* getNews `[0,[]]` accepted -> the positional pair; the item reader `0xb31c90`
  is still undecoded, so `news` stays empty (that is the intended shape).
* the 1517-byte CallCloudAsync is the FIRST TIME a SERVED DOCUMENT passed the
  gate: the game's own `ops=MAP_SET&docKeys=GameToWeb` (stats) stored
  `100000002/GameToWeb.json` (document 1392 chars, `lastModifiedDate=1790317530`)
  and the reply `[0,[[0,"<Stats> ...",<policy>,<time>,<name>]]]` walked all five
  positional slots (server: `CLbody=1517 bytes docs=1`, `<- 200`).
* the 09:26:22 GET is the EXCHANGE ACCESS (`ops=MAP_GET&docKeys=TomeGift`); it
  answered `[0,[]]` (`CLbody=6 bytes docs=0`) because the store's
  `100000002/TomeGift.json` holds `"values": {}` - no gift document was ever
  published. Empty here is CORRECT, not a format failure. The game only
  registers that document (`ops=SET,SET&docKeys=TomeGift,GameToWeb&keys=&values=
  &policy=1,1`, seen 05:47:02 and 08:19:38) and fills `values` when it actually
  has a tome to give.

### OPEN - DATA, NOT FORMAT

The exchange list can only be non-empty once a `TomeGift` document WITH values
exists. Our store and read path are ready for it (`_cloud_body` serves
`values[docKey]` verbatim); the game writes one when the player
publishes/gifts a tome. If a populated exchange is wanted without that in-game
action, the next decode target is how the `CallCloudAsync` caller consumes the
`data` vector (`out+8` of `0xb3280a`) and what document grammar the exchange UI
expects, then seed `100000002/TomeGift.json`.


## 2026-09-25 12:10 - TOME-GIFT EXCHANGE: SEEDING TOOL VERIFIED; GRAMMAR STILL NEEDS A CAPTURE

The exchange list is empty for a DATA reason (`100000002/TomeGift.json` holds
`"values": {}` - no gift was ever published), not a format reason: the
09:37:12 `MAP_GET TomeGift` answered `[0,[]]` and the client accepted it
(`CLbody=6 bytes docs=0`, no rejection). What was finished here is everything
around that gap.

### `tools/gift_capture.py` - replay the game's own document writes
The server logs every request in full, so a document the game uploaded can be
read back verbatim long after the session that produced it:

    python tools/gift_capture.py                       # 74 cloud/execute requests
    python tools/gift_capture.py --doc TomeGift        # newest such document
    python tools/gift_capture.py --seed 100000002       # store it for that account
    python tools/gift_capture.py --text '<Gift/>' --seed 100000002
    python tools/gift_capture.py --clear 100000002      # back to "nothing stored"
    python tools/gift_capture.py --raw                 # + the wire fields
    python tools/gift_capture.py --log <stdout.txt>

`--seed` is how a RECEIVED gift is simulated with one Steam account: the server
re-reads the store on every request (`_load_doc`), so the next `MAP_GET` returns
the seeded document without restarting anything. A seeded document on a FRESH
account now carries the same permissions the game's own login registration
leaves behind (`ops=SET,SET&docKeys=TomeGift,GameToWeb&policy=1,1&worldPerms=2,2`
at 09:34:52): `docPolicyBits=1`, `worldPerms=2`. Verified end to end on
100000002/77770001 with `doc_content()` returning the seeded text and `--clear`
dropping only the document text, never the permission bits.

Fixed while testing: the CLI advanced its index twice for the value-less
`--raw`, so `--raw --log <path>` swallowed `--log` and died on the path
("unknown argument"); value-taking options now consume their value in place and
`--clear` without an account is a usage error (rc 2).

New regression suite `tools/test_gift_capture.py` - 31 checks, exit 0: slot
decoding (`docKeys=TomeGift%2cGameToWeb` must be DECODED before the split on
','), request-line vs whole-TLS-record vs POST-body captures, the duplicate GET
capture collapsing to one entry, `raw[plain]` (port 80) captures, the seed/clear
round trip against a temp CLOUD_DIR, the served-body preview, the CLI cases, and
a replay of the live `requests.log` (the 09:36:17 `MAP_SET GameToWeb` document
comes back verbatim).

Seeding and clearing now also print the body the NEXT read returns, produced by
the server's own `_cloud_body` (a 3-line handler stub, no duplicated record
layout), followed by the verdict of the offline format gate:

    seeded -> server\data\cloud\100000002\TomeGift.json
    next read returns: [0,[[0,"<TomeGift>...",1,1790440138,"seed"]]]
    client walk       : OK (tools/sim_reader.py)
    (and after --clear: [0,[]] ... client walk: OK)

so a candidate grammar can be checked before the game is even launched. Two
traps fixed on the way: `tools/sim_reader.py` re-wraps `sys.stdout` at import,
and dropping the previous wrapper closes that buffer - which killed this tool's
own output (it now hands sim_reader a throwaway stream); and on Windows the
shell eats double quotes inside a single-quoted `--text` argument
(`'<Tome Id="7"/>'` arrives as `<Tome Id=7/>`), worth remembering because a
gift document is JSON-escaped.

### What the exe says about the gift document (and what it does NOT)
* `tmp_region.py` dumps the `<Stats>` markup builder literal pool
  (`0xe711d4`-`0xe716f8`: `<ComboNumber Value='%i'/>` ... `<Stats>`) - the ONE
  cloud-document grammar that is literal-recoverable.
* No gift markup exists: `<Gift`, `<Gifts>`, `Gifts>` and even `Gift ` do not
  occur anywhere in `Darksiders2.exe`, so `TomeGift` is not XML with gift tags
  and its grammar cannot be reconstructed from literals.
* The gift SAVE data object does name its fields (`tmp_giftgrammar.py`, the
  0x80-byte descriptor table at `0xdcd7c0`-`0xdcde40`):
  `Number | IsFromPlayer | IsLegendaryFromFullInventory | GifterName |
   Location | Letter | Visited | Version | UniqueId | Gifts | GiftCounter |
   ShouldAddToNewGiftCounter | Gift | CharacterComponent | GiftComponent |
   addGift | Tomes | MaxTomeGifts`
  - that is the script/savegame side (`GiftComponent`, `addGift`), which is what
  the UI (`TomeGiftFocusItem` + `txtGiftCount/txtLocation/txtProfile/txtGiftNo/
  giftheader_mc` at `0xda72xx`) renders. `TomeGift` also names a UI resource
  package (`0xee826c`, next to `Slide/Int_SlideShow/.upak/ResourceManager::
  runLoading`), so the literal `TomeGift` in the request is a package/UI name
  too, not a tag.

### Next (needs the game, not the server)
1. Launch, publish/gift a tome in the exchange UI.
2. `python tools/gift_capture.py --doc TomeGift` <- that output IS the grammar.
3. `python tools/gift_capture.py --seed 100000002` - the seed prints the exact
   body the next read returns (`[0,[[0,"<doc>",1,<time>,"<name>"]]]`) and the
   format-gate verdict, so the capture can be validated before the game is
   touched. In-game the next `MAP_GET TomeGift` must answer with that record
   (`CLbody=NNN bytes docs=1` in the stdout log, `CallCloudAsync - response
   good` in `ds2_tls.log`) and the exchange list should list it.
4. `python tools/gift_capture.py --clear 100000002` restores the honest empty
   store. (`server/data/cloud/99999999` and `77770001` test accounts removed.)


### Where a gift write will actually appear on the wire (2026-09-27)

The capture recipe above needs one correction: a gift send is NOT necessarily a
`cloud/execute` request, so `--doc` alone may find nothing. The send path was
disassembled to its end:

* `sendGift` (`0x5ae758`) builds the gift (recipient name -> numeric id at
  `gift+0x18`, `isFromPlayer` at `gift+0x20`, the item vector at `gift+0x40`),
  then calls `0x5ade5b` - the UPLOAD routine - and that one does not touch the
  cloud-document API at all:
  - `push 4; call 0x4010d5` (0x1c-byte request object), `0x4bf835` (refcount),
  - `call 0xf8f82c` / `0xf8edf0` (async request construction),
  - `0x405972` + `0x758bbf` = the global data provider -> `[eax+0x150]`, whose
    `+0x20` byte must not be 1 (else `push -7`);
  - failures `-6` (no provider), `-7` (busy), `-8` (no global) all go to
    `0x5aaa13`, i.e. the on-screen "could not send" status.
* `0xf8xxxx` is the **THQServer** module (`0xf8f325` =
  `ExtensionAsync@THQServer@@`). Its API names sit at `0xedd004`-`0xedd04c`:
  `CallExtensionAsync`, `ExtensionGetURLAsync`, next to
  `WorldwideExtensionService.cpp` (`0xedd098`) and the wire parameter names
  `requestExtensions` / `singleRequestExtensions` / `responseExtensions` /
  `singleExtensions` (`0xedaa2c`-`0xedac1c`).
* The COMPLETE endpoint list in the exe is only: `/thqconnect/login/ssoStatus`,
  `/readsession`, `/initSession`, `/platformLogin`, `/thqconnect/news/getNews`,
  `/thqconnect/url/getURL`, `/thqconnect/tunnel/tunnel`,
  `/thqconnect/cloud/execute`, `/thqconnect/cloud/getKeys`. There is NO
  extension endpoint - so `CallExtensionAsync` must ride on `cloud/execute`, and
  the gift write can therefore carry a `requestExtensions=...` parameter instead
  of the `keys=`/`values=` pair that the cloud filter looks for.
* The live log (288 requests, 2026-08-25..09-26) confirms the game has never sent
  url/getURL, tunnel/tunnel or anything extension-shaped:
  `78 GET platformLogin, 68 GET + 37 POST cloud/execute, 42 GET + 8 POST
  ssoStatus, 14 POST + 1 GET initSession, 10 GET getNews, 10 GET /version,
  8 POST cloud/getKeys, 1 GET readSession`, plus one body whose request line the
  hook missed (a `GameToWeb` MAP_SET, listed honestly as `(no path)`).
  Every one of those paths already has a handler in `server/ds2_server.py`
  (`_dispatch` maps the path to `h_<lowercased path>`): `h_version`,
  `h_thqconnect_login_{initsession,platformlogin,readsession,ssostatus}`,
  `h_thqconnect_news_getnews`, `h_thqconnect_cloud_{execute,getkeys}`,
  `h_thqconnect_url_geturl`, `h_thqconnect_tunnel_tunnel`.

So the first command after the in-game send is now:

    python tools/gift_capture.py --any             # what did the game just send?
    python tools/gift_capture.py --find TomeGift   # its exact params, decoded

and only then `--doc TomeGift` (if the write did turn out to be a cloud MAP_SET)
or `--find requestExtensions` (if it is an extension call riding cloud/execute).
`--any` also prints a per-endpoint tally, so an unfamiliar path is obvious at a
glance. Both new modes read the same `log_requests()` scan that `--doc`/`--seed`
use; `--find <token>` searches the encoded text AND its decoded form (so
`--find '<TomeGift'` works even though the wire has `%3cTomeGift`).
`python tools/test_gift_capture.py` -> all 41 checks (was 31).

Also settled this round (base64, and what the document dispatch does):

* `0xb4394c` is a **base64 DECODER** (accepts `=`, `+`, `/`, alphabet table,
  4 chars -> 3 bytes); `0xb437ec`/`0xb44229` is its ENCODER. Both are inside the
  cloud client module (`0xb44xxx` = Worldwide cloud + its doc map).
* One dispatcher branch walks the returned document entry by entry (stride
  `0x30`; value string at `entry+0x10`, size at `entry+0x08`), base64-DECODES
  each value and feeds it to `0xaef713(ptr, size, 1)`. That branch handles a
  *binary blob* document, so if the gift document is that one, its text is
  base64 of a serialized object and `--doc` output must be decoded before it
  reads as anything.
* The `TomeGift` branch itself only calls `[edi+4]`'s `vtable+0x58` (a "the
  document arrived" notification), which is why an accepted empty body
  (`[0,[]]`) is silently fine in-game.
* Gift logic stays scripted: `media/scripts.obsp` plus the save-side descriptor
  table (`Number/IsFromPlayer/IsLegendaryFromFullInventory/GifterName/Location/
  Letter/Visited/Version/UniqueId/Gifts/GiftCounter/...`), matching the UI
  (`txtGiftNo/txtProfile/txtLocation/giftheader_mc`).


### The document is a string -> int64 MAP, not the gift payload (2026-10-28)

Settled from the client's own validator trace, not by guessing:

```
[{"key":"15932905779752315075","data":0},{"key":"5394434090946791192","data":0}]
```

| slot | consumed by | so it must be |
|---|---|---|
| `"key"` value | string reader (`want '"' got '"' ret=af830e`) | a string |
| `"data"` value | `0xaf7f06` int64 reader (`want '[' got '"' ret=af7f47`; optional `[`, digits, `_atoi64`) | an **int64** |

Everything else failed with `Unable to process string`: a string, a list, or a
nested object in the `data` slot, **for any member name** (`data`, `value`,
`object`, `lastModifiedName`). The names `value` / `lastModifiedName` /
`lastModifiedDate` do exist in the interpreter (`0xb05e49`: `0xb05ea9 value`,
`0xb05ebf lastModifiedName`, `0xb05ed5 lastModifiedDate`), but this path never
gets there. So the names are NOT what selects the reader here - the position is.

**Consequence:** the base64 `BOD` blob the game wrote
(`keys=<giftId>&values=Qk9E...`) is **not** a map value. The `TomeGift` map is an
index/counter (`giftId -> int64`), and the gift payload reaches the client by a
different route. `server/docshape.txt` now serves the confirmed shape (one entry
per stored gift id), so the exchange is usable again; the payload route is the
open question.

### Where the gift actually stands
* Write path works: `MAP_SET ... keys=<giftId>&values=Qk9E...` addressed at the
  recipient's profile lands in that account's store (both gift ids are in
  `100000001/TomeGift.json`).
* Identity is fine: `76561198000000002 -> Player12554 -> 100000001`, consistently.
* Read path is understood but yields an empty inbox, because the map carries ids,
  not the payload. Remaining leads: the record's other slots (`0xb32450` reads
  five: status/document/policyBits/time/name), the *tunnel* (`/tunnel/tunnel`
  exists in the server and has never been hit by the game), and the sender-side
  script at `0x5ade5b`.

### The client's own verdicts are now readable too (2026-09-27)

`--any`/`--find` read the *server's* log. That cannot show a send that dies before it
touches the network (the `-6`/`-7`/`-8` statuses at `0x5ade5b` never leave the process),
so the capture tool now also reads the *client's* log:

    python tools/gift_capture.py --game-log [PATH]
    python tools/gift_capture.py --game-log --find TomeGift

`ds2_tls.log` (in the game folder, 300+ MB, deleted by every deploy) is the loader's log
with the engine's own `[GAME]` lines inside it. Two forms matter and both are parsed:

    [GAME] Running https://thqc-darksiders2-pc.thqconnect.com:8443/thqconnect/cloud/execute?<params>
    [GAME] CallCloudAsync - response good: 6 bytes, [0,[]]
    [GAME] CallExtensionAsync - response was not formatted correctly
    [GAME] CallCloudAsync - (used 3/4) extra data ignored: [0,[...]]

`--game-log` alone lists them (`SENT` request lines and `RECV <Job>Async` verdicts, plus a
tally); `--find <token>` prints the newest match, decoded, including the verdict and body.
`--doc`/`--seed`/`--text`/`--clear` are refused with it (they write the server's store).
The default path is the same one `tools/correlate_fmt.py` uses, so both tools agree.
`tools/test_gift_capture.py` -> 49 checks.

What that log proved for the 2026-09-27 20:11-20:21 session (game closed cleanly at
20:21:12, `THQServerManager::Destroy - exit allocation counts Network (cur/max) = 0 / 81`):

* every response in the session was accepted - `AccountPlatformLoginAsync`,
  `CheckSSOStatusAsync`, `CallCloudAsync` (three times) and `GetNewsAsync` all logged
  `response good` - including the exchange read
  (`20:13:34 CallCloudAsync - response good: 6 bytes, [0,[]]`), i.e. the empty TomeGift
  document is served in a shape the client parses happily;
* over the whole log (148 entries: 68 sent, 80 recv) the tally is
  `38 CallCloud, 12 AccountPlatformLogin, 12 CheckSSOStatus, 6 GetNews` all good, and the
  12 `not formatted correctly` verdicts are ALL historical (newest `2026-09-24 05:48`) and
  all traceable to the old JSON-object shapes or pre-fix sweeps - nothing since 09-24 has
  failed;
* **no gift was sent**: the only TomeGift requests are the login `GET,GET`, the `SET,SET`
  registration and the `MAP_GET` at 20:13:34 - no `CallExtension`, no `requestExtensions`,
  no friends call of any kind (only the `friendPerms=` wire parameter even matches
  "Friend"). Opening the exchange produced exactly ONE request, which is the strongest
  available evidence that **the gift list is fed by the TomeGift document alone** - so a
  correctly seeded document is the whole job for LISTING gifts.

Watch out: `E:\SteamLibrary\steamapps\common\Darksiders 2\server\` is a STALE copy of the
server (its `server\logs\requests.log` stops at 2026-08-22 and its `ds2_server.py` is older
than the one here), while `2 - START SERVER.bat` in the game folder runs exactly that copy
(`cd /d "%~dp0"` + `python server\ds2_server.py`). The live server is the one in this
workspace - verified because THIS `server/logs/requests.log` received the 20:11-20:13
requests - so start the server from here, or that .bat will serve a months-old emulator.


### The exchange's "connection error" and the empty-document switch (2026-09-27)

Reported in-game: the exchange now fails with a connection error. The logs say
the wire side is clean (session 20:41-20:43: the same seven requests as before,
`MAP_GET TomeGift` answered `[0,[]]` and the client logged `CallCloudAsync -
response good: 6 bytes, [0,[]]`, walking `[0,[]]` to its closing `]`), so the
message is either the UI's own view of a doc that DOES NOT EXIST or its
link-status gate. Two facts support the doc theory:

* the client's login registration (`ops=SET,SET&keys=&values=`) creates the
  document on the real service, so a read there answers with a record whose
  document is EMPTY - our `[0,[]]` is "no record at all", a state the real
  service never has for a registered docKey;
* one of the gift callbacks the exe names is `onRetrieveGiftFailure`, i.e. the
  script has an explicit failure path for a gift retrieval.

`EMPTY_DOC_RECORD` (env `DS2_EMPTY_DOC=1`, off by default) makes the server
materialize a docKey on any write - the registration included - and serve
`[0,[[0,"",policy,time,name]]]` for it. Preview both answers offline, the second
one with the env var set in the same shell:

    python tmp_seed_gift.py empty-str     # '' -> [0,[]] without the switch
    python tmp_seed_gift.py xml-empty     # '<TomeGift></TomeGift>' -> a record
    python tmp_seed_gift.py clear         # back to no document

`tools/test_cloud_exchange.py` -> 37 checks (the switch's both answers, and the
empty record passes `tools/sim_reader.py`).

Two things that are NOT the cause, so they can be ignored in future sessions:

* `>> cb163 SYNTH: ... m_hAuthTicket=0 vs synth 55 [MISMATCH]` +
  `NOT setting state` - the loader's Steam-ticket synthesizer firing 1500 ms
  after `GST` even though the real callback already arrived
  (`OnAuthToken - succeeded` is logged 0.3 s after every request); it happens for
  handles 82/84/85 in every session;
* the `443` socket - the GDS service (`ds2.gds.thqconnect.com:443`, `/gds/rpc`)
  is connected at startup, greeted by our `greeting[doc_realhost_tilde_close]`
  hack and then CLOSED by it (client socket closes 1.5 s later from script
  code). That is the only connection that ends abnormally, so if the switch does
  not fix the message, this is the next suspect: the UI's script API has
  `getNetworkLinkStatus` / `getNetworkConnectionStatus` / `isProfileOnline`
  right beside the gift callbacks (`0xd7e500`-`0xd7e690`).

### 2026-09-28 21:47 - THE VICTORY: exchange opens, first gift captured

`DS2_EMPTY_DOC=1` was the missing piece (see the section above): with a RECORD for the
registered-but-empty `TomeGift`, the exchange menu opens instead of timing out after
four silent seconds, and the player can send. The send was captured verbatim, and it is
the grammar:

    GET /thqconnect/cloud/execute
        ?profileNames=/steam/76561198000000002      <- the RECIPIENT's profile
        &ops=MAP_SET&docKeys=TomeGift
        &keys=5394434090946791192                   <- the gift NUMBER (map slot)
        &values=Qk9E/QQBAQAeAAAAJgAAAJ4DAAB42nVSW0hUURQ9Z5xR...

    document = base64( "BOD" + 16-byte header + zlib(object graph) )
    header   = 04 01 01 00 | 30 | 38 | 926 (inflated size; 926 matches the zlib output)
    inside   = Gift -> Items -> weapon_hammer_a01 (+ Modifiers/`cp1_`, Attributes,
               OverrideRarity), GifterName=playerB,
               Location=$Environment.Z1_MakersVillage.TriStone, Number, IsFromPlayer,
               Version, UniqueId

Three things this settles, all of them predicted from static analysis:

* the value really is **base64 of a THQServer `BOD` frame** - the magic at 0xf8ee09
  (`STL` next door) and the base64 decoder at 0xb4394c were exactly right;
* the doc is a **map of giftId -> gift**, so `keys=` is a number, not the docKey, and
  `doc_content()`'s "only non-empty keyed value" fallback is what makes the record come
  back at all;
* a gift is written **into the recipient's document** (`profileNames=` addresses it),
  which is why the client registers the doc `worldPerms=2` - the mailbox permission.
  Proof on disk: `server/data/cloud/100000001/TomeGift.json` now holds
  `{"TomeGift": "", "5394434090946791192": "Qk9E..."}`.

Tools and records for the next session:

    python tools/gift_doc_decode.py              # newest capture: base64 -> BOD -> zlib
    python tools/gift_doc_decode.py --strings 40  # ... and the object graph's names
    python tools/gift_capture.py --seed 100000001 # replay that gift into any inbox
    VICTORY_RU.md                                 # the whole road, in Russian

`tools/test_gift_capture.py` -> 52 checks (two new: the capture must stay
base64(BOD+zlib), and must keep the `Gift`/`Items`/`GifterName` graph). Still open:
what the record should carry once one document holds SEVERAL gifts (today the single
non-empty value is served), and whether the recipient's UI renders it without a
`TomeGift`-keyed entry once the first gift arrives.


### 2026-10-08 - THE RECEIVE PATH: why the wire blob never reaches the save

Static hunt through the script package and the exe, closing the question left
at the end of the route-split round ("how/when does the client convert a wire
gift blob into a save gift entry?"):

* **Script side** (`media/scripts.obsp`, region 0xfde000-0xfe4000): the
  exchange screen drives everything - `checkForGifts`,
  `onGiftRetrieveAttempt` -> `Game.retrieveGifts` (exe API 0xd7ea70, bound at
  0x4e8f2d to the thunk 0x4e66e0 -> `[global+0x150]`'s vtable+0x20),
  `onGiftRetrieveSuccess/ Failure`, `notifyGiftRetrieveSuccess`, and the
  ingest callback `onGiftReceived` (0xfde6db). The UI fields are filled by
  `base/additemtotome` (0x12d1c1): `gift.GifterName -> this.TomeGifterName`,
  `Location -> TomeLocation`, `Letter -> TomeLetter`, `Items -> Item01..05`,
  then `giftc.addGift(gift)` - i.e. **the name the inbox renders is
  `GifterName` of a gift OBJECT, and the object is expected to come from the
  wire, not from the save's sent copy.**
* **Exe side**: `onRetrieveGiftSuccess` (0xd7e660) is registered on the
  TomeMenu object at 0x5abba9; `onGiftReceived` has its exe xref at 0x722ee3,
  inside the dispatcher that walks a list (stride 0x1c, MSVC strings), builds
  the gift payloads and fires the script event - the only path that ends in
  `addGift`.
* **The decoder**: `0xb4394c` (base64, confirmed round 09-28) has EXACTLY ONE
  call site in .text: `0xb44fcf`, inside the entry walk at `0xb44f96` (case 4
  of the dispatcher switch at 0xb44e65, taken when the parsed document's time
  field is 0). The walk iterates entries - **stride 0x30 = {key {ptr,size},
  value char* at +0x10}**, i.e. the parsed form of a JSON OBJECT whose keys
  are views into the source text and whose values are heap c-strings -
  base64-decodes each VALUE into a vector, and inserts each KEY
  (0xb44dfc -> container +0x13c). **That is: the walk eats
  `{"<giftId>":"Qk9E..."}` and nothing else.**
* **The bug**: the plain GET route served `doc_content()`'s BARE single value
  (`Qk9E...`) since the 10-06 route split. A bare value is not JSON: it
  produces no entries, the walk never runs, `0xb4394c` is never called, the
  gift never reaches `onGiftReceived`/`addGift`, and the save never gains the
  RECEIVED entry. The inbox then renders the save's own SENT entry (written at
  send time, `GifterName` = own name) - the "shows the player's own name"
  symptom. `response good` on that route only proves the ENVELOPE parsed;
  all 54 `Unable to process` verdicts sit on MAP_GET, so a bare GET was
  always silently accepted WITHOUT being ingested.

**The fix - `DS2_GET_MAP`** (`ds2_server.GET_MAP`, off by default): the plain
GET route now serves `doc_map_json()` - the full stored map as
`{"<giftId>":"Qk9E...", ...}` (docKey sentinel and empty values dropped) for
docs that HAVE gift-id keys; GameToWeb and every single-value doc keep
serving raw XML (`doc_map_json` -> None), offline previews and DOC_MAP-off
runs are untouched (`opi == 'GET'` gate). This also answers the old open
question about several stored gifts: they all go out in ONE object.

New checks in `tools/test_cloud_exchange.py` (44 -> **50**, all green):
object served, sentinel absent, GameToWeb still raw, sim_reader passes, no
docshape rotation consumed, and the default-off behaviour restored.
`tools/test_gift_capture.py` -> **92** green, `tools/test_login_flow.py`
exit 0. `tmp_route_verify.py` updated to accept EITHER shape (raw blob or
map object) and its stale `1791211768007` index pin replaced by a generic
"any 15+ digit key" check (the 10-07 store clear broke the pin).

**Still open - the in-game proof**: relaunch with `DS2_GET_MAP=1`, send a
gift both directions, and after a recipient relaunch check that the save
gained the RECEIVED entry with `GifterName` = sender (tmp_savecmp /
tmp_recvsave) and the inbox shows the sender's name. If the object shape is
still not what the walk wants, the next candidates are the pairs layout
`[["id","Qk9E..."]]` (both strings, stride 0x30 = two MSVC strings) - the
live A/B docshape machinery is the tool for that.

Server restarted as round_1014 with
`DS2_EMPTY_DOC=1 DS2_DOC_MAP=1 DS2_TRACE=1 DS2_GET_MAP=1`.


### 2026-10-09 update - obj shape VERDICT: no ingest; default switched to pairs

In-game test of the round_1014 server (obj shape `{"<giftId>":"Qk9E..."}`):
three launches (pids 42212/46076/46704), gifts sent both directions first.
Client log verdicts: `response good` every time, **zero** `Unable to
process`, no inner PARSE of the TomeGift document text - and the save/UI
forensics show NO change: each inbox still renders the save's own SENT
entry under the player's own name. The obj shape therefore did NOT ingest.

Two things that log settles:

* The plain-GET document text **never reaches the hooked JSON parser**
  (af7da1) - only `MAP_GET` inner documents do (PARSE #7 of the index on
  every exchange open). So the client log cannot judge the GET route's
  document shape at all: `response good` only ever means "envelope
  parsed". **The save forensics (tmp_savecmp/tmp_recvsave) is the only
  oracle for this switch.**
* Accepted != ingested - the same acceptance signature as the bare blob
  since the 10-06 route split, which never ingested either.

**Shape switch**: `DS2_GET_MAP` now selects the served map shape instead of
being a boolean - `1`/`pairs` (new default) -> `[["<giftId>","Qk9E..."],...]`,
`obj` -> `{"<giftId>":"Qk9E..."}` (the failed candidate, kept selectable),
unset -> off. Pairs is the default because it is the docshape alias the
PRE-SPLIT rotation actually served on login reads (round logs 09-30..10-05
show `docs=2` bodies rendered from it), and its two-string element maps onto
the walk's entry {key {ptr,size}, value char*} just as cleanly as the
object did.

Suites after the change: `test_cloud_exchange` 44 -> **52** (pairs + obj
both asserted, sentinel-free, GameToWeb raw, sim walk, no rotation consumed,
default-off restored), `test_gift_capture` **92**, `test_login_flow` exit 0,
`tmp_route_verify` 12/12 (now accepts raw / pairs / obj and distinguishes
pairs from the index map by element type).

Server restarted as round_1015 (PID 37036) with
`DS2_EMPTY_DOC=1 DS2_DOC_MAP=1 DS2_TRACE=1 DS2_GET_MAP=1`; live log shows
`shape=getmap:pairs` on the login read:
`[0,["[[\"3326186032734777932\",\"Qk9E/QQBAQAb...\",\"<Stats> ..."]]]`.
Tested by the user next: send both directions, relaunch the RECIPIENT,
open the exchange, then tmp_savecmp must show TWO entries - the own sent
one AND the received one with GifterName = sender.


### 2026-10-09 (late) - BREAKTHROUGH: server gifts ARE ingesting; the ATTACHMENT lands, the MAIL record's gifter is mis-bound

User test (round_1015, pairs shape): restored saves repeatedly, sent nothing
this session, relaunched as `100000002` (steamid `76561198000000001 & 0xffffffff`
= `100000002`, confirmed - all of today's `platformLogin` were this account).
**Before/after**: pristine save `100000002` = 0 TriStone gifts; current save
(launched, nothing sent) = gifts present, and **the mailbox UI now renders
them and behaves correctly** (mail stays, attachments clear on collect - the
intended design; `DS2_COLLECT` full-clear kept as the debug tool). So the
server-served gifts ARE reaching the save on the login read. Two hard facts,
decoded from the decompressed gift payloads (tmp_blobdump / tmp_giftaudit):

* The served gift `12447702100279194958` (doc `100000002`) decompresses to
  `Items=[armor_feet_wanderer_d02]`, `IsFromPlayer`, `GifterName='playerA'`,
  `Location=$Environment.Z1_MakersVillage.TriStone`, `UniqueId`. The live save
  `100000002` now **contains `armor_feet_wanderer_d02`** (x1) - i.e. the
  ATTACHMENT from the served gift is in the save. The receive path works.
* BUT the save's gift RECORD is `@15163: gifterName(playerB) +
  item=weapon_axe_a01 + TriStone` - that is a WEAPON gift with the recipient's
  OWN name, NOT the served armor gift. The served armor blob (gifter
  `playerA`) contributes its item to the save, yet the one serialized
  TriStone record carries the wrong name AND wrong item.

Interpretation (the attribution is a **binding** bug, not a send/ingest bug):
`onGiftReceived`->`addGift` deserializes the served gift (attachment lands -
`IsFromPlayer`/`GifterName='playerA'` are inside the payload), but the entry
the save serializes (`<gifterName>&.$Environment.Z1_MakersVillage.TriStone`)
is built from the SAVE'S OWN existing record - a weapon gift under the player's
own name - not from the ingested gift's fields. In practice the mailbox
`MAP_REMOVE` at 13:38 removes the served gift from the server doc while the
save keeps/rewrites its own SENT entry. So: **ingest = yes (attachment); save
record = own name (attribution wrong).** This matches the symptom "item I sent
to myself" - the inbox shows the player's own TriStone, and the received item
only shows up as an attachment cleared on collect.

Next levers, in order (each is one isolated switch):
1. **`docslot.txt`** sweep - the `lastModifiedProfileName`/status/timestamp
   slots of the served record may be what the record binder reads for the
   gifter; the served doc carries `lastModifiedName='playerA'` but the slot
   currently emits the recipient's own account. Live-sweep on next launch.
2. The `IsFromPlayer`/`GifterName` deserialize path (exe) - if `addGift`
   stores by UniqueId and the save merges by a key the own-sent gift already
   occupies, the two gifts collide into one record. Compare the two UniqueIds.
3. Attribution may require the served record's `data` slot to be the gift's
   **object** (not the base64 blob) so the binder reads GifterName from it -
   `docshape` pairs-vs-obj is now moot; try serving the decoded gift object.

#### DESIGN (confirmed with user 2026-10-09)

* **Mail stays in the inbox; it is per-sender, not per-item.** Each mail row
  carries ONE sender name. **Items in a mail have NO attribution at all** - the
  `Items[]` array is just an attachment bag; only the mail (the gift record)
  has a `GifterName`.
* Consequence: the earlier "record shows weapon under own name" observation is
  NOT a per-item attribution problem. The one `GifterName` on the mail is what
  the inbox renders (`gift.GifterName -> TomeGifterName`, `base/additemtotome`).
  So the fix target is a single field: the mail's `GifterName`. This is far
  simpler than per-item attribution - we only need the served gift's
  `GifterName` to survive into the save's mail record.
* Collecting a mail clears its attachments but keeps the row (intended);
  `DS2_COLLECT` full-clear remains a debug tool.

### 2026-10-09 18:43 - CLEAN-SLATE TEST armed (round_1016)

The GifterName rewrite to `SERVERMAIL` was correct at the blob level (all 4
gifts verified serving `SERVERMAIL` on the wire), but the in-game inbox still
showed **2 gifts (axe + hammer) under the player's own name**. So the inbox
name is NOT the served blob's `GifterName` - it is bound from something else.

To isolate cleanly, the cloud store was wiped of gifts:
* backup: `server/data/cloud_backup_20261009_precleartest/` (plus the older
  `cloud_backup_20261005`).
* `tmp_clear_gifts.py backup` dropped all 4 gifts; both `TomeGift` docs now hold
  only the `TomeGift` sentinel; `_load_doc + doc_map_json` both return `None`
  (empty inbox serves nothing).
* logs rotated (`requests_20261009_184320.log`), `trace.log` removed.
* **round_1016** restarted (PID 37488 / port-owner 40664) with
  `DS2_EMPTY_DOC=1 DS2_DOC_MAP=1 DS2_TRACE=1 DS2_GET_MAP=1` (pairs).
* Save is user-restored (clean); this script confirms the STORE is clean too,
  so any gifts seen in-game this round are 100% client-reconstructed from the
  save/SentBox, not delivered by the server.

NEXT: launch as `playerB`, open exchange WITHOUT sending. If 2 gifts still
appear, they come from the save's own SENT record -> the inbox renders the
SentBox, i.e. the receive-side doc is never consulted for display. Confirm by
checking whether a launch with a truly empty SENT box (fresh profile) shows 0.


#### GifterName rewriter (server-stored mail) — `tmp_setgifter.py` (2026-10-09)

Per the user: don't analyse saves between attempts (they restore them each
time) — instead **stamp a test `GifterName` straight into the server-stored
gift blobs** and let the recipient read it on the next login. The save now
proven-ingests server mail, so the sender name that reaches the inbox is
exactly the name in the blob.

Blob framing (fully cracked, `tmp_bodhdr.py`):
* base64 value -> **BOD container**: `b'BOD' + 17-byte header + zlib stream`.
  The **uncompressed payload length is a u32 at header offset 16**.
* zlib.inflate -> payload. The `GifterName` VALUE string is framed
  `[8-byte SStringHash][u16 LE len][chars]`, located right after the literal
  key `GifterName`. The 8-byte hash is a proprietary engine string hash (no
  standard 64-bit hash matches it — `tmp_crackhash*.py`).

Rewrite (`tmp_setgifter.py --name SERVERMAIL`, default):
* swap the chars to the new name, fix the u16 length prefix,
* **set a fresh RANDOM 8-byte hash** — critical: the engine interns strings by
  that hash, so reusing the old hash would make the reader return the
  already-interned old name and ignore our bytes; a fresh hash forces a new
  intern carrying our bytes,
* if the length changed, bump the BOD payload_len at offset 16,
* every blob is **round-trip verified in memory** (base64->BOD->inflate->name
  present, framing intact) before anything is written.

Verification:
* `tmp_verifyrewrite.py`: diff of old vs new payload shows **only** the name
  line changes; item/location/modifiers/UniqueId framing byte-identical.
* on-disk re-decode: all 4 gifts -> `SERVERMAIL`, len prefix 10, BOD header
  payload_len == actual inflated length (939/925/758/751) — containers valid.
* `tmp_wirecheck.py`: through the server's own `_load_doc` + `doc_map_json`
  (pairs), all 4 served gifts carry `SERVERMAIL` and neither old name survives.
  The server loads docs fresh from disk per request (`_load_doc`, no cache), so
  the rename is live with **no restart**.

All 4 stored gifts (both mailboxes) now read `GifterName='SERVERMAIL'`. Next
in-game test: restore a clean save, launch the recipient, open the exchange —
the mail row should show the sender name `SERVERMAIL` (proving the blob's
`GifterName` is what the inbox renders), which closes the attribution bug.







## 2026-10-10 (late) - GIFT INBOX: slot4 sender-name lever armed; read GIFT_PROTOCOL_FALLBACK.md

AUTHORITATIVE gift-inbox state lives in `GIFT_PROTOCOL_FALLBACK.md` (829 lines) - read its
top "CURRENT STATE" section first after any compaction.

This round:
- RULED OUT (live, round_1022): status slot0=2 REGRESSES ingestion (inbox error); `data` int64
  flag is NOT the render gate (data:1 still empty); blob-string in MAP_GET = "Unable to process".
- ROOT CAUSE of empty row: login GET,GET builds the 5-slot deliverable record; the store doc
  carries no metadata so slot4 lastModifiedProfileName="" -> client skips building the row
  (attachment delivers, row does not).
- ARMED (server/docslot.txt, single line, live, no restart): `0=0 4="playerB"` -> record
  slot4 now 'playerB'. docshape.txt active: `[%E%] => {"key":"%K%","data":1}` (int64 index).
- Verify with `python _verify_armed.py` (parses both files as the server does).
- NEXT on-device: fresh login as playerA (clean save), open exchange once, watch
  server/logs/round_1022_stdout.txt for `slots={0: 0, 4: 'playerB'}`. Row renders => slot4 was
  the gate. Still empty => sweep DS2_GET_MAP (pairs/obj/off) on the login content route (needs
  server RESTART). Full ladder + ruled-out list in GIFT_PROTOCOL_FALLBACK.md.
- Cleaned 25 scratch _*.py scripts; kept _verify_armed.py.

## 2026-10-10 (night) - DECODE hook crashed game -> SPLIT into proxy + ds2_probe.dll

READ `GIFT_PROTOCOL_FALLBACK.md` LAST SECTION (~1074+) for full detail. Summary:

- The in-proxy base64-DECODE capture (patch decoder 0xb4394c) CRASHED the game at startup. Two
  bugs in the stub: (1) decode_log deref'd arg2 as a std::string (AV on early-init base64 where
  the pointer isn't a safe string); (2) the stub clobbered eax/ecx without saving. tlog was NOT
  the cause (READY-gated, patched on worker thread post-open_logs).
- REBUILT per user direction into the split architecture:
  * `binkw32.dll` (proxy): decode hook DELETED; replaced by ONE loader `load_probe_dll()`
    (LoadLibraryA "ds2_probe.dll" -> GetProcAddress "ds2_probe_init" -> call). Absent probe = game
    still runs. Rollback = delete ds2_probe.dll (or env DS2_PROBE_OFF=1 = load but don't patch).
  * `ds2_probe.dll` (NEW crate tools/tls_probe, i686 cdylib, exports ds2_probe_init cdecl):
    patches 0xb4394c, logs every decode to its own ds2_probe.log. Crash-proof: pushad/popad
    register save, content peek fully VirtualQuery-guarded (readable_range), deref-free on fail.
    ret=b44fd4 tagged "<- GIFT WALK".
- BOTH built clean + deployed: proxy->Steam game dir + both server dirs + tools/tls_proxy;
  probe->next to game exe (LoadLibraryA is path-less) + server dir. Marker-verified: crash string
  "DECODE 0xb4394c patched" gone from proxy, present in probe.
- NEXT on-device: launch recipient -> game should BOOT. Check <game>\ds2_probe.log for
  "ds2_probe_init called" + "DECODE 0xb4394c patched". Open exchange; grep DECODE lines for
  ret=b44fd4 (<- GIFT WALK). Walk fires => match the head blob to a server route; never fires =>
  lever #3 (receive path = save-side gift system, not cloud doc). Key files: tools/tls_probe/,
  load_probe_dll() in tools/tls_proxy/rust/src/lib.rs.

## 2026-10-11 - "Startup crash" = MISSING decorate_eat.py step (FIXED, not a code bug)

READ `GIFT_PROTOCOL_FALLBACK.md` LAST SECTION (2026-10-11). TL;DR:
- After the split deploy the game wouldn't start. It was NOT the probe/hook — I had rebuilt the
  proxy with `cargo build` but SKIPPED the mandatory `decorate_eat.py` post-step. The Rust cdylib
  exports UNDECORATED names (BinkOpen); the game imports DECORATED (_BinkOpen@8). Loader failed
  BEFORE DllMain -> no ds2_tls.log line, no WER event, Bink error on direct-exe start.
- FIXED: ran tools/tls_proxy/decorate_eat.py on all 3 binkw32.dll copies (77 EAT names each,
  skipped DllMain). Verified export set now matches binkw32_real.dll exactly (diff empty).
- HARD RULE: any proxy rebuild MUST run decorate_eat.py (or just build_rust.bat = one-shot).
  ds2_probe.dll does NOT need decoration (loaded by name via GetProcAddress, never imported).
- NEXT: launch recipient -> should BOOT now. Verify <game>\ds2_probe.log has "ds2_probe_init
  called" + "DECODE 0xb4394c patched (resume b43953)". Then open exchange, grep DECODE for
  ret=b44fd4 (<- GIFT WALK).


