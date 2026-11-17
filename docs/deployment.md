# Deployment — options and considerations

Working notes for the Phase 5 public demo. `design.md` §11 has the original
one-paragraph sketch (Vercel + Fly/Render + Supabase); this expands it with
what the code actually needs and where the real decisions are.

**Shipped 2026-11-16 as bundle B with Neon:** Vercel + one Fly machine + Neon
Free. §8 records what was done and how to redeploy. Everything above §7 is the
original option analysis, kept for the reasoning.

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
   ~19k chunks locally), but it exercises the real path and proves the prod
   DB and schema work. Raw HTML is disk-cached so a second run is fast.

Either way the corpus must be at the current schema and chunking: migrations
through `005_text_only_fts.sql`, table cells present (`python -m pipeline
table-report` shows ~280k cells), chunks built by the current chunker. A dump of
a local DB that predates table column binding needs `migrate`, `retable` and
`rechunk` run locally first; a fresh `ingest` + `embed` produces all of it.

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

## 7. Pre-deploy checklist

Completed on 2026-11-16, except the one item left open.

- [x] Anthropic console: hard monthly spend limit + email alert
- [x] `backend/Dockerfile`: `python:3.13-slim`, pinned `requirements.lock`, model baked in, `uvicorn ... --host 0.0.0.0` (PR #28)
- [x] Rate limit on `/ask`: 20 per visitor per hour, 200 per day (`api/limits.py`, PR #28)
- [x] `frontend/.env.local.example` exists; `NEXT_PUBLIC_API_URL` documented
- [x] Provision DB: Neon Free, Postgres 16, pgvector 0.8.7, `us-east-1`
- [x] Load corpus (`pg_dump` → `pg_restore`, then `VACUUM ANALYZE`). Row counts and the index list match local exactly; one `/ask` answered and verified
- [x] `python -m evals examples --runs 10 --verbose` against prod: 10/10, 40/40 citations verified first pass, answers read by hand
- [x] Retrieval on prod matches local: `--retrieval-only` gives identical recall@10, unfiltered and targeted (once local had a fresh `ANALYZE`)
- [x] API env: `DATABASE_URL`, `ANTHROPIC_API_KEY` as Fly secrets; `FRONTEND_ORIGIN` etc. in `fly.toml`. `CREDITS_EXHAUSTED_MESSAGE` left at its default
- [x] Anthropic usage limit and credit balance checked before posting the link
- [x] Frontend env: `NEXT_PUBLIC_API_URL=https://edgar-answers-api.fly.dev`, rebuilt
- [x] Health check → `/healthz` (in `fly.toml`)
- [x] `conversation_turns` retention: `save_turn` deletes conversations idle for 7 days (PR #28), so no cron is needed
- [ ] Log drain / Sentry DSN. Not done; `fly logs` is the only trace for now
- [x] End to end on the live URL: the full tutorial, with both turns' citations clicked and highlighted, in Chromium (desktop and phone size), Firefox and WebKit (phone size). No console or CORS errors
- [x] Forged `Fly-Client-IP` can't buy fresh quota: with the cap at 2, the third request (each with a different forged IP) got a 429, and the UI showed the limit message
- [x] `README.md` "Demo" section + `design.md` §11 updated

## 8. Running it

| What | Where |
| --- | --- |
| Site | https://edgar-answers.vercel.app (Vercel project `edgar-answers`, root directory `frontend`) |
| API | https://edgar-answers-api.fly.dev (Fly app `edgar-answers-api`, one machine in `iad`) |
| Database | Neon project in AWS `us-east-1`. The URL is in `backend/.env.production` as `PROD_DATABASE_URL` (gitignored) |

- **Redeploy the API:** run `fly deploy --ha=false` from `backend/`. A plain
  `fly deploy` may create a second machine, which splits the `/ask` counters.
- **Change a secret:** `fly secrets set NAME=value -a edgar-answers-api`. This
  restarts the machine, and that resets the in-process `/ask` counters.
- **Frontend changes:** Vercel rebuilds on every push to `main`. A change to
  `NEXT_PUBLIC_API_URL` needs a **Redeploy**, because Next bakes it in at build
  time. The variable must be a plain (non-sensitive) variable with the
  `NEXT_PUBLIC_` prefix.
- **Don't let Vercel host the backend.** Importing at the repo root makes Vercel
  offer a multi-service `vercel.json` with the FastAPI backend in it. Set the
  root directory to `frontend` instead.
- **Reload the corpus** after any local `rechunk`, `reprocess` or `ingest`. Run
  the dump and restore from the primary checkout with `MSYS_NO_PATHCONV=1`
  (see the deploy plan, Task 5). Read `PROD_DATABASE_URL` with `grep | cut`,
  not `. .env.production`: Neon's URL contains `&`, which shell sourcing treats
  as "run in background", leaving the variable empty.
- **After a restore, always `VACUUM ANALYZE`** before measuring anything.
  Without fresh stats the planner's choice between HNSW and a seq scan is
  arbitrary.
- **Re-run the release check** (`python -m evals examples --runs 10 --verbose`
  with `DATABASE_URL` set to the prod URL) after any prompt, retrieval or
  corpus change.
- **Revert the page layout without a code change.** `/ask` serves the evidence
  layout (spec 2026-11-17). To put the original split-pane page back, add the
  Vercel environment variable `NEXT_PUBLIC_LAYOUT=classic` (Production) and
  **Redeploy**. Remove it and redeploy to switch back. The original page is
  also always live at `/classic`, for comparison.
