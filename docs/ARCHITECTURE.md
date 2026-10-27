# Polymath: Architecture

## 1. Stack (decided)

| Layer | Choice | Why |
|---|---|---|
| Engine | **Python 3.11+** package `polymath` | Fastest path for the agent pipeline and scholarly APIs |
| Model | **Muse Spark** (`muse-spark-1.1`, or 1.2 if offered) via the **Meta Model API**, which is OpenAI-compatible | Hackathon requirement. Only three values change: base URL `https://api.meta.ai/v1`, `MODEL_API_KEY`, model name |
| HTTP client | `httpx` (calling chat completions directly; no vendor SDK) | Keeps the model swappable and easy to test |
| Server | **FastAPI** + Uvicorn, with **Server-Sent Events** for live runs | Long-running jobs plus streamed progress |
| Storage | **SQLite** (one file) on a persistent disk | Zero ops. The convergence graph lives in it |
| Frontend | **Static HTML/CSS/JS** served by FastAPI (no build step) | Ships fast, deploys alongside the API |
| Deploy | **Render** (Docker web service with a persistent disk). Fly.io is the fallback | Long-running requests and background jobs work there, which isn't true of serverless timeouts (Vercel/Netlify) |
| Tests | `pytest` with a **FakeLLM** and fixture papers | Runs offline with no API spend |

**Secrets never reach the browser.** The Meta API key lives only in server environment variables.

## 2. Repository layout (target)

```
polymath/
├── polymath/
│   ├── config.py          # Settings from env vars
│   ├── models.py          # Paper, Signature, Bridge, Evidence, Claim, Answer
│   ├── llm.py             # OpenAI-compatible client, JSON parsing, retries, usage meter, FakeLLM
│   ├── prompts.py         # Every prompt in one place, each with a JSON output contract
│   ├── store.py           # SQLite: papers, signatures, bridges, edges, runs, events
│   ├── sources/
│   │   ├── base.py        # Source protocol, rate limiting, dedupe
│   │   ├── arxiv.py
│   │   ├── openalex.py
│   │   └── semanticscholar.py
│   ├── similarity.py      # TF-IDF cosine over signature text (pure Python)
│   ├── scan.py            # Field selection, concept translation, retrieval
│   ├── signatures.py      # Paper → structural signature (Muse Spark)
│   ├── converge.py        # Candidates → hypothesis → critic → novelty → score → Bridge
│   ├── meta.py            # Bridges → meta-bridges (recursive)
│   ├── ask.py             # Question → labelled Answer
│   ├── formalise.py       # Bridge → Lean statement (+ optional compile/repair loop)
│   ├── write.py           # Summary / paper renderers
│   ├── publish.py         # Zenodo upload behind the approve gate
│   ├── runs.py            # Background run manager + event bus
│   ├── server.py          # FastAPI app
│   ├── cli.py             # `polymath explore|ask|serve|ingest`
│   ├── demo.py            # Offline demo responders for FakeLLM
│   └── web/               # index.html, app.js, styles.css
├── tests/                 # fixtures/, test_*.py
├── docs/
├── Dockerfile
├── render.yaml
├── .env.example
├── pyproject.toml
├── LICENSE                # Apache-2.0
└── .github/workflows/bitcoin-timestamp.yml   # existing; do not break
```

## 3. The Explore pipeline (F1)

```
concept
  │
  ├─1. choose_fields ......... Muse Spark picks N fields (default 6) where the same
  │                             structure might live under other names, and gives
  │                             2–3 field-native search queries for each
  │
  ├─2. retrieve .............. arXiv + OpenAlex (+ Semantic Scholar) per field,
  │                             k papers per field (default 8), dedupe by DOI/title
  │
  ├─3. signatures ............ Muse Spark rewrites each paper as a vocabulary-free
  │                             structural signature (batched, cached in the DB)
  │
  ├─4. candidates ............ TF-IDF cosine over signature text, CROSS-FIELD pairs
  │                             only, top M (default 12), at most 2 per field pair
  │
  ├─5. hypothesise ........... Muse Spark: is this the same problem? → title, argument,
  │                             A↔B mapping table, open question, evidence quotes
  │                             (must be verbatim from title/abstract), math_content flag
  │
  ├─6. critique .............. a separate "critic" call tries to break it:
  │                             verdict = survives | weakened | broken, plus reasons
  │
  ├─7. novelty ............... search sources for papers mentioning both sides;
  │                             Muse Spark judges novel | partially_known | known
  │
  ├─8. ground ................ code check: every evidence quote must appear in the
  │                             cited paper's text (normalised substring), and
  │                             unverifiable quotes are dropped
  │
  └─9. score ................. confidence = f(hypothesis score, critic verdict,
                                novelty, grounded-evidence count) → rank → persist
```

**Why the signature step matters.** Plain similarity search fails across fields precisely *because* the vocabularies differ. Rewriting every paper as abstract objects, relations, dynamics and mathematical form lets Muse Spark normalise the vocabulary. After that, cheap similarity search finds same-shaped problems.

**Confidence formula (v1, tune later):**
`confidence = 0.45·hypothesis_score + 0.25·critic_factor + 0.15·novelty_factor + 0.15·evidence_factor`
where critic_factor is survives=1, weakened=0.5, broken=0 (a broken bridge is dropped); novelty_factor is novel=1, partial=0.6, known=0.2; and evidence_factor = min(grounded_quotes/4, 1).

## 4. Prompt contracts (`prompts.py`)

Every model step returns JSON in a fixed shape, parsed defensively (tolerating code fences and surrounding prose, with one retry asking for JSON only).

| Task | Output |
|---|---|
| `choose_fields` | `{"fields":[{"name","why","queries":[..]}]}` |
| `signature` | `{"objects":[..],"relations":[..],"dynamics","math_form","open_problem","keywords":[..]}` |
| `hypothesise` | `{"is_bridge":bool,"score":0-1,"title","argument","mapping":[{"a","b","role"}],"open_question","evidence":[{"paper_id","quote","supports"}],"math_content":bool}` |
| `critique` | `{"verdict":"survives|weakened|broken","reasons":[..],"strongest_objection"}` |
| `novelty` | `{"novelty":"novel|partially_known|known","note","prior_work_ids":[..]}` |
| `ask_plan` | `{"concepts":[..],"sub_questions":[..]}` |
| `ask_answer` | `{"summary","claims":[{"text","label","confidence","sources":[..],"bridges":[..]}],"caveats"}` |
| `meta_signature` / `meta_hypothesise` | the same shapes, applied to bridges |
| `formalise` | `{"applicable":bool,"lean":"...","informal_statement","proof_sketch"}` |
| `write_summary` / `write_paper` | Markdown |

The system prompts tell the model to stay **honest** (say "no bridge" when there isn't one), to **quote verbatim**, and never to invent papers or IDs. Paper IDs are passed in and checked on the way out.

## 5. Data model (SQLite)

```
papers(id PK, source, title, abstract, url, year, authors_json, field, doi, created_at)
signatures(paper_id PK→papers, json, model, created_at)
bridges(id PK, run_id, level, concept, field_a, field_b, json, confidence, created_at)
bridge_papers(bridge_id, paper_id, side)            -- 'a' | 'b'
bridge_children(parent_id, child_id)                -- meta-bridges
runs(id PK, kind, input, status, result_json, usage_json, created_at, finished_at)
events(run_id, seq, ts, type, data_json)            -- replay for the live view
publications(bridge_id, kind, doi, url, approved_by, created_at)
```

## 6. Runs and live streaming

- `POST /api/runs {kind: "explore"|"ask"|"meta", input}` → `{run_id}`. The run executes in a background worker thread.
- Each step emits an event, written to `events` and pushed to subscribers:
  `run.started, fields.chosen, source.query, papers.found, signature.done, candidates.ready, hypothesis.done, critique.done, novelty.done, bridge.saved, answer.ready, run.finished, run.failed, usage`.
- `GET /api/runs/{id}/events` uses **SSE**. It replays from `events` and then tails live, so the browser can reconnect mid-run.
- Runs are resumable: signatures and papers are cached, so rerunning a concept costs much less.

## 7. HTTP API

```
GET  /                              web app
POST /api/runs                      start explore | ask | meta
GET  /api/runs/{id}                 status + result
GET  /api/runs/{id}/events          SSE stream
GET  /api/bridges?limit=&level=     list
GET  /api/bridges/{id}              detail (with papers)
POST /api/bridges/{id}/formalise    → Lean statement + status
POST /api/bridges/{id}/write        {kind: "summary"|"paper"} → markdown
POST /api/bridges/{id}/publish      {approve: true, kind} → Zenodo DOI (403 without approve)
GET  /api/graph                     nodes + edges for the graph view
GET  /api/health                    model reachable? sources reachable? usage so far
```

## 8. Meta AI tooling (25% of judging: go beyond a basic integration)

| Capability | How Polymath uses it |
|---|---|
| **Muse Spark reasoning** | Every judgement step: fields, signatures, hypotheses, critique, novelty, answers, formalisation |
| **Multi-agent roles** | Separate reader, hypothesiser, critic, novelty-judge and writer personas, with the critic adversarial to the hypothesiser |
| **Tool use / function calling** | v2 "agentic explore": Muse Spark drives the loop itself with tools such as `search_arxiv`, `search_openalex`, `get_paper`, `save_bridge`, deciding where to look next. *Check in Phase 0 that the Model API supports `tools`.* |
| **Long-context + context compaction** | Long runs keep a compacted research memory across dozens of papers, which is the "long-running agent" story |
| **Multimodal** | Read figures and diagrams from PDFs (page images → Muse Spark), because figures often show structural similarity that the text hides. Stretch goal |
| **Muse Code** | Optional: use it for part of the build and say so in the submission |
| **SAM 3 / Glimmer** | Check what they offer in Phase 0. Possible use: segmenting figures out of PDFs before reading them |

## 9. Budget ($150 Meta API credits)

Rough estimate per Explore run at default settings (6 fields × 8 papers, 12 candidates): about 250k input and 60k output tokens. At the reported Muse Spark price ($1.25 / $4.25 per M tokens) that's **about $0.60 per run**, so $150 covers **roughly 200+ runs**. Signature caching makes repeat runs cheaper. Develop against FakeLLM, and keep live runs for integration checks and the demo. Set the **$150 spend limit** on the Meta billing page.

## 10. Configuration (`.env.example`)

```
MODEL_API_KEY=                 # from dev.meta.ai (server-side only)
POLYMATH_API_BASE=https://api.meta.ai/v1
POLYMATH_MODEL=muse-spark-1.1
POLYMATH_FAST_MODEL=           # optional cheaper model for signatures
POLYMATH_DB=polymath.db
POLYMATH_CONTACT_EMAIL=        # polite-pool header for OpenAlex / arXiv
SEMANTIC_SCHOLAR_API_KEY=      # optional, raises rate limits
ZENODO_TOKEN=                  # optional; publishing disabled without it
ZENODO_SANDBOX=true            # keep true until the demo is final
LEAN_BIN=                      # optional path to `lake`; otherwise status = unchecked
POLYMATH_MAX_FIELDS=6
POLYMATH_PAPERS_PER_FIELD=8
POLYMATH_MAX_CANDIDATES=12
POLYMATH_MAX_BRIDGES=5
```

## 11. Deployment (Render)

- A `Dockerfile` (python:3.12-slim, `pip install .`, `uvicorn polymath.server:app --host 0.0.0.0 --port $PORT`).
- A `render.yaml` describing a web service plus a **1 GB persistent disk** mounted at `/data`, with `POLYMATH_DB=/data/polymath.db`.
- Env vars set in the Render dashboard. **Never commit `.env`.**
- Basic abuse protection on the public URL: a per-IP run rate limit and a global daily run cap, so judges can't drain the credits. A `DEMO_MODE` flag can serve cached showcase runs instantly.

## 12. Source etiquette
- arXiv API: at most 1 request every 3 s, and send a contact email in the User-Agent.
- OpenAlex: `mailto=` param for the polite pool.
- Semantic Scholar: an API key is optional. Back off on 429.
- Cache everything. Never refetch a paper we already have.
