# Deployment — options and considerations

Working notes for the Phase 5 public demo. `design.md` §11 has the original
one-paragraph sketch (Vercel + Fly/Render + Supabase); this expands it with
what the code actually needs and where the real decisions are. Nothing here
is committed to yet.

---

## 1. What is actually being deployed

Three units (`design.md` §3) plus a pile of data:

| Unit | Shape | Needs |
| --- | --- | --- |
| **Frontend** (`frontend/`) | Next.js App Router, **entirely client-rendered** — `/` redirects to `/ask`, `/ask` is `"use client"`, no server components fetch data, no API routes | the API's URL at **build** time (`NEXT_PUBLIC_API_URL`); nothing else |
| **API** (`backend/src/api/`) | one stateless FastAPI process, `uvicorn api.app:app` | `DATABASE_URL`, `ANTHROPIC_API_KEY`, `FRONTEND_ORIGIN`; ~500 MB RAM; holds a 65 MB ONNX model in memory; makes up to ~5 sequential Haiku calls per answer; streams SSE |
| **Pipeline** (`backend/src/pipeline/`) | batch CLI, run by hand | not deployed — runs from your machine against the prod `DATABASE_URL` |
| **Data** | Postgres + pgvector | **320 MB today** (chunks 151 MB, sentences 104 MB, filings 57 MB, HNSW index 46 MB, FTS index 16 MB); 120 filings / 15,432 chunks / 296,316 sentences; `vector` extension + pgvector ≥ 0.5 for the HNSW index; **read-write** — the API inserts a `conversation_turns` row per answered turn |

The query path is DB-heavy: each answer runs the hybrid retrieval (a vector
search + an FTS search + RRF), loads chunks, and verifies quotes — dozens of
round trips — then 3–5 Haiku calls. **Co-locate the API and DB in the same
region** or the round trips compound into seconds.

---

## 2. Pre-deploy gap analysis

### 2.1 Blockers — the demo will not come up without these

1. **No Dockerfile for the API.** Need one: `python:3.13-slim`, `pip install .`
   from `backend/`, `CMD ["uvicorn", "api.app:app", "--host", "0.0.0.0",
   "--port", "8000"]`. `--host 0.0.0.0` matters — uvicorn binds `127.0.0.1`
   by default and the container would refuse external traffic.
2. **Bake or pin the embedding model.** `fastembed` downloads
   `bge-small-en-v1.5` (~65 MB) from a CDN on first use and caches it to a
   **temp directory** by default (`Embedder.__init__` passes no `cache_dir`).
   On an ephemeral container FS that means a 65 MB download — and a multi-
   second stall — on the first `/ask` after every cold start, or a silent
   failure if the CDN is slow. Fix: `RUN python -c "from fastembed import
   TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5')"` in the image with
   `FASTEMBED_CACHE_PATH` set to a path inside the image, so the weights ship
   in the layer.
3. **`NEXT_PUBLIC_API_URL`** must be set to the deployed API URL at frontend
   **build** time (Next inlines `NEXT_PUBLIC_*` at build, not runtime).
   Default is `http://localhost:8000`.
4. **`FRONTEND_ORIGIN`** must be set on the API to the exact deployed
   frontend origin or the browser's CORS preflight fails and every request
   is blocked. One origin, no wildcard (`app.py`).
5. **Get the corpus into the prod DB** (see §4). Decide `pg_dump`/restore vs
   re-ingest.
6. **Confirm pgvector on the chosen DB.** `CREATE EXTENSION vector` must be
   available and the version must support `hnsw` (≥ 0.5.0). Supabase and Neon
   ship it; a bare Postgres does not.

(`frontend/.env.local.example` — once a blocker here — now exists and is
tracked; it documents `NEXT_PUBLIC_API_URL`, currently the only frontend env
var.)

### 2.2 Strongly recommended before a *public* URL

8. **`/ask` is unauthenticated and spends real money.** Every call is 3–5
   Haiku requests. `MAX_OUTPUT_TOKENS = 1500` caps *output* per call
   (~$0.005–0.015/answer), but nothing caps *request volume*. A public URL
   with no gate is an open invitation to run up a bill. Pick at least one:
   - a hard **monthly spend limit + alert in the Anthropic console** (do this
     regardless of anything else);
   - an **IP rate limiter** in front of `/ask` (`slowapi` is ~15 lines, or a
     reverse-proxy / Cloudflare rule — e.g. 10 requests/min/IP);
   - a **shared password / basic-auth** on the whole demo (a query-param
     token the frontend holds is enough to stop drive-by abuse);
   - **Cloudflare** in front with Bot Fight Mode + a rate-limit rule.
9. **`conversation_turns` grows unbounded.** No retention policy (noted as
   deferred in the conversation-memory spec). On a public demo it will
   accumulate fast. Add a `DELETE FROM conversation_turns WHERE created_at <
   now() - interval '7 days'` cron, or a row cap, or just monitor it.
10. **No observability.** Nothing logs structured events or reports errors.
    At minimum wire up the platform's log drain; a free Sentry DSN for the
    API is cheap insurance for a live demo.

### 2.3 Nice-to-have

11. **Connection handling.** The API opens a fresh `psycopg.connect()` per
    request (`app.py` says pooling is "a Phase 5 concern"). Fine at demo
    traffic. If you point it at a **transaction-mode pooler** (Supabase port
    6543, Supavisor), psycopg3's prepared statements will collide —
    `set prepare_threshold=None` on the connection or use the **session**
    pooler / direct connection instead.
12. **`/healthz` exists** — point the platform's health check at it.
13. **Pin the model id.** `generate.py` uses `claude-haiku-4-5` (an alias).
    Pin the dated id if you want answers reproducible across a model refresh.
14. **Static export is possible.** The frontend has no server needs, so
    `output: "export"` + any static host works (see §3.3). The only snag is
    `app/page.tsx`'s server-side `redirect("/ask")`, which `output: export`
    rejects — make it a client redirect or set `/ask` as the index.

### 2.4 Before you deploy anything — verify the build

- Backend: `ruff check .` + `pytest -v` green with `TEST_DATABASE_URL` set
  (218 pass today).
- Frontend: `npm run lint`, `npm test`, `npm run test:e2e` green; **plus the
  branch `ask-input-dock` / PR #23 merged** (the bottom-docked input).
- `python -m evals run` on a clean tree — verified-citation rate ≥ 0.90
  (currently 1.0). Record the row.
- The spec §4 manual multi-turn script against a real key (done once during
  the conversation-memory work; re-run after any prompt change).
- Click-to-highlight by hand in at least Chrome and Firefox — the viewer
  does raw DOM offset math and it has never been checked cross-browser.

---

## 3. The options

Free-tier terms drift constantly — treat the specifics below as "the shape
of the choice and the gotcha for *this* app", and check current limits
before committing.

### 3.1 Database (Postgres + pgvector)

| Option | Rough free allowance | pgvector | The catch for this app |
| --- | --- | --- | --- |
| **Neon** | 0.5 GiB/branch, ~190 compute-hrs, autosuspend @5 min, wakes <1 s | yes, HNSW | 320 MB fits with headroom; sub-second wake makes scale-to-zero painless; branching is handy for eval runs. **Best default.** |
| **Supabase** | 500 MB DB, project **pauses after 7 days idle** | built-in, HNSW | fits, barely; the 7-day auto-pause bites a demo that sits between showings — you'd need a weekly ping. Great SQL editor / dashboard. Use the **session** pooler, not transaction (see §2.3-11). |
| **Render Postgres** | 1 GB, **free instance deleted after 30 days** | via `CREATE EXTENSION`, pgvector present | the 30-day expiry rules it out for anything meant to persist. |
| **Fly Postgres / Managed Postgres (MPG)** | pay-as-you-go, a tiny instance is a few $/mo | pgvector available | worth it only if the API is also on Fly — co-location is the point. MPG is still beta. |
| **Railway Postgres** | $5 trial credit, then usage | pgvector template | tidy if the API is also on Railway; otherwise nothing special. |
| **Postgres in a container on a VPS** | — (VPS cost only) | you install `pgvector/pgvector:pg16` — the same image as dev | most control, you own backups and upgrades. Natural if you take the single-box route (§3.4). |

**Pick:** Neon, unless you specifically want the Supabase dashboard and will
keep it warm.

### 3.2 API (the FastAPI container)

Requirements it imposes: **≥ 512 MB RAM** (Python + onnxruntime + the 65 MB
model resident → ~400–600 MB RSS under load; a 256 MB instance OOMs on model
load), a **long-lived process** (SSE streams for 10–30 s; ~5 sequential LLM
calls), and a request timeout **> 60 s**.

| Option | Always-on on free? | Cold start | Notes |
| --- | --- | --- | --- |
| **Fly.io** | effectively — a `shared-cpu-1x` 512 MB machine kept at `min_machines_running = 1` is ~$2–4/mo | none when warm | best fit: cheap, regioned, SSE-clean, put the DB alongside. Not free, but the smallest real bill here. |
| **Render Web Service (free)** | **no — sleeps after 15 min idle**, ~50 s wake **+** model load | brutal for a live demo unless you hit it right before | dead-simple Docker deploy from GitHub; the sleep is the whole problem. A cron pinging `/healthz` every 10 min keeps it up but burns the free hours. |
| **Google Cloud Run** | scales to zero; 2M req/mo free; 60-min timeout; streaming supported | cold start + model load unless `min-instances = 1` (then ~$5–10/mo for a warm small instance) | generous, pay-per-use, good if traffic is spiky. Set `--min-instances=1` for demo snappiness. |
| **Railway** | $5/mo hobby includes $5 usage; no sleep | fast | one dashboard for API + DB + cron; usage-priced so a runaway loop shows up as cost, not an outage. |
| **Koyeb / Northflank / etc.** | small free instance | varies | fine; less battle-tested for this exact SSE + model pattern. |
| **VPS + Docker (Hetzner / DO / Lightsail)** | yes, ~$4–6/mo | none | you run `uvicorn` under a process manager or compose; you own TLS and restarts. See §3.4. |

**Pick:** Fly.io for a small always-on machine (predictable, no cold starts,
co-locate DB). Cloud Run if you'd rather pay strictly per use and can accept
`min-instances=1` cost or occasional cold starts.

### 3.3 Frontend

The app is client-only, so this is the easy part.

| Option | Notes |
| --- | --- |
| **Vercel (Hobby)** | canonical Next.js host, free, GitHub auto-deploy, set `NEXT_PUBLIC_API_URL` in project env. Zero friction. **Default pick.** |
| **Cloudflare Pages / Netlify** | equally fine; Pages doubles as a rate-limiting / WAF layer if you also proxy the API through Cloudflare. |
| **Static export to any host** | `output: "export"` → drop `out/` on Pages, GitHub Pages, S3+CloudFront, or let the API's reverse proxy serve it. Needs the `app/page.tsx` redirect fix (§2.3-14). |
| **Served from the API origin** | build static, have Caddy/nginx in front of the API serve it. **Eliminates CORS entirely** (`FRONTEND_ORIGIN` becomes same-origin). Best paired with §3.4. |

**Pick:** Vercel, unless you go single-box, then serve the static build from
the same origin as the API and skip CORS.

### 3.4 Or: one box, one compose file

A single small VPS (Hetzner CX22 ~€4/mo, DO/Lightsail ~$6) running
`docker compose`: `pgvector/pgvector:pg16` + the API image + **Caddy**
(automatic HTTPS) serving the static frontend and reverse-proxying `/api` →
uvicorn.

- **Pros:** no cold starts, no CORS, one place to look, one bill, and you
  learn the deploy/TLS/logging flow end to end. The dev `docker-compose.yml`
  is 80% of the config already. Backups are `pg_dump` on a cron.
- **Cons:** you own uptime, security patches, and disk. No platform to page
  you. A reboot needs `restart: unless-stopped` set on every service.

Good fit given this is a learning project and the demo is meant to sit up
persistently rather than scale.

---

## 4. Getting the corpus into production

Two paths:

1. **`pg_dump` → restore** (recommended). `pg_dump --no-owner
   --no-privileges --format=custom` locally, `pg_restore` into the prod DB
   after `python -m pipeline migrate` has created the schema. ~320 MB,
   a few minutes, bit-exact, no EDGAR traffic, no model needed on the prod
   side. The HNSW index rebuilds on restore (a minute or two).
2. **Re-ingest against prod.** Point `DATABASE_URL` at the prod DB and run
   `python -m pipeline ingest --all && python -m pipeline embed` from your
   machine. Slower (re-fetches filing lists from EDGAR live, re-embeds all
   15k chunks locally), but it exercises the real path and proves the prod
   DB and schema work. Raw HTML is disk-cached so a second run is fast.

Ongoing ingestion stays manual from your machine against prod `DATABASE_URL`
— there is no ingestion service to deploy (`design.md` §11).

`companies` is seeded as a side effect of `ingest` (`store.py`), so either
path populates it; a `pg_dump` carries it directly.

---

## 5. Cross-cutting

- **Cost.** Infra: $0 (all-managed-free, with cold starts) to ~$6/mo
  (Fly small always-on + Neon, or one VPS). Anthropic: ~$0.005–0.015 per
  answer at Haiku, output-capped. The variable you cannot bound structurally
  is *how many* answers — hence §2.2-8. Set the Anthropic monthly limit
  first, everything else second.
- **Secrets.** `ANTHROPIC_API_KEY` and `DATABASE_URL` as platform env vars,
  never in the image, never `NEXT_PUBLIC_*`. `load_env(override=False)`
  already prefers injected env over any file, so no `.env` needs to exist in
  prod.
- **SSE through proxies.** The API already sends `X-Accel-Buffering: no` and
  `Cache-Control: no-cache`. Fly / Render / Cloud Run / Caddy stream fine.
  **Cloudflare's proxy buffers some SSE** and Rocket Loader can interfere —
  if you put CF in front of the API, test streaming end to end. Never route
  SSE through Vercel's edge.
- **Region.** API and DB in the same provider region. The query path is
  chatty with Postgres.
- **Custom domain / HTTPS.** Vercel and Fly do TLS automatically; a VPS
  needs Caddy or certbot. A demo on `*.vercel.app` + `*.fly.dev` is fine to
  start.
- **CI.** `.github/workflows/ci.yml` runs lint + tests on push to `main`. No
  deploy step. Add one (Fly `flyctl deploy`, Render/Vercel auto-deploy hooks,
  or a compose `docker compose pull && up -d` over SSH) once the target is
  chosen — or deploy by hand for a demo.

---

## 6. Three coherent bundles

| | Frontend | API | DB | Monthly | Trade-off |
| --- | --- | --- | --- | --- | --- |
| **A. All-free** | Vercel | Render (free) | Neon | **$0** | Render cold-sleeps; ping `/healthz` before every demo, or the first question takes ~90 s. |
| **B. Cheap & snappy** | Vercel | Fly.io, 1× `shared-cpu-1x` 512 MB always-on | Neon (or Fly MPG) | **~$3–5** | no cold starts, regioned, clean SSE. The recommended demo setup. |
| **C. One box** | static build, served by Caddy | same box, uvicorn under compose | pgvector container on the same box | **~$4–6** (VPS only) | no CORS, no cold starts, one bill, most to learn/own; you're the ops team. |

**Recommendation:** **B** if the demo needs to feel instant when you show it
and you're fine with a ~$5 bill. **C** if you want the demo to just sit up
indefinitely and you'd rather learn the full deploy flow than juggle three
dashboards. **A** only as a throwaway / first smoke test.

---

## 7. Pre-deploy checklist (once a target is chosen)

- [ ] Anthropic console: hard monthly spend limit + email alert
- [ ] `backend/Dockerfile` — `python:3.13-slim`, `pip install .`, pre-download the model into the image, `uvicorn ... --host 0.0.0.0`
- [ ] Rate limit or password gate on `/ask`
- [x] `frontend/.env.local.example` exists; `NEXT_PUBLIC_API_URL` documented
- [ ] Provision DB, `CREATE EXTENSION vector`, `python -m pipeline migrate`
- [ ] Load corpus (`pg_dump` → `pg_restore`), spot-check row counts + one `/ask`
- [ ] API env: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `FRONTEND_ORIGIN`
- [ ] Frontend env: `NEXT_PUBLIC_API_URL` → deployed API URL; rebuild
- [ ] Health check → `/healthz`
- [ ] `conversation_turns` prune cron (or a monitoring note)
- [ ] Log drain / Sentry DSN
- [ ] End-to-end from a fresh browser: ask → stream → citation → highlight, in Chrome and Firefox
- [ ] `README.md` "Demo" section + `design.md` §11 updated with what was actually done
