# server/data/

`cloud/` — the persistent cloud document store (one dir per account) — is **not
committed**. It holds private player data (gift documents, stats, account/Steam
IDs) and is **recreated automatically** on the first client login
(`ds2_server.py` does `os.makedirs(..., exist_ok=True)`). It is listed in
`.gitignore` as `server/data/cloud/`.
