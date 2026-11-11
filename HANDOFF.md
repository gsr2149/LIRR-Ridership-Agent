# Handoff: LIRR Fare Change Research Agent

This file is for whoever finishes the project. It covers what is done, how to get
running, and exactly what is left.

## The assignment, and where things stand

Question the agent answers: **How did the January 2026 LIRR fare and ticketing changes
change what riders buy and what they pay?**

| Requirement | Status | Where |
|---|---|---|
| Uses data the model doesn't know deeply | ✅ Done | 4 data.ny.gov datasets + the Jan 2026 fare chart (`data/`) |
| Gets at the data through tools | ✅ Done | `list_tables`, `describe_table`, `run_sql`, `lookup_fare` (`lirr_agent/tools/`) |
| Combines evidence into a supported claim | ✅ Done | Writer agent (`lirr_agent/agents/writer.py`) |
| States when the data can't answer | ✅ Done | Lead `out_of_scope`, Writer "What the data can't tell us", tool errors |
| Shows its work; `/chat` keeps the Project 1 shape; sub-agent calls in `tool_calls` with an `agent` field | ✅ Done | `lirr_agent/main.py`, `lirr_agent/llm.py` |
| Deep Research: Lead, Researchers, Writer (3 agent shapes) | ✅ Done | `lirr_agent/agents/lead.py`, `researcher.py`, `writer.py` |
| Lead fans out to several Researchers in one turn, in parallel, fresh contexts | ✅ Done | `lead.dispatch()` (thread pool) |
| Lead goes back for more when there's a gap, up to a turn cap | ✅ Done | `lead.review()`, `MAX_RESEARCH_ROUNDS` |
| Vague questions get clarification | ✅ Done | Lead `clarify` action |
| Output is accurately cited | ✅ Done | Citations checked in code; Sources list generated from real queries |
| Speed (answers took 1-2 minutes) | ✅ Done | speed patch applied (`feat/deploy`) |
| Front end: says what it explores, who it's for, how to use it | ❌ To do | **Step 2** below (current page is a bare test page) |
| Deployed to Google Cloud Run | ❌ To do | **Step 3** below |
| README | ❌ To do | **Step 4** below |

All 32 tests pass on `main` (`uv run pytest -q`).

## How it works (one turn of `/chat`)

```
question -> Lead.plan: clarify? out of scope? or 2-4 sub-questions (different angles)
              | parallel, fresh contexts
        researcher-1   researcher-2   researcher-3      (each writes its own SQL)
              |
        Lead.review: a gap or surprise? -> follow-up researchers (max 2 rounds)
              |
        Writer -> answer with [q_xxxxxxxx] citations -> code checks every id
               -> Sources list built from the cited queries
```

`/chat` request: `{"message": "...", "session_id": "optional"}`

`/chat` response: `{"response": "...", "session_id": "...", "tool_calls": [{"agent", "name", "args", "result"}, ...]}`

`agent` is `lead`, `researcher-N` or `writer`. Besides real tool calls (`run_sql` etc.),
the trace has entries for the agents' decisions: `plan`, `dispatch_researchers`,
`report_finding`, `review`, `verify_citations`.

| File | What it does |
|---|---|
| `data/fetch.py`, `data/build_db.py` | Download the datasets, build `data/lirr.duckdb`, write `data/SCHEMA.md` |
| `data/fares/` | Fare chart PDF and the 576-row CSV made from it |
| `lirr_agent/tools/` | The four data tools + registry (`python -m lirr_agent.tools ...` to try them) |
| `lirr_agent/llm.py` | Gemini tool loop, retries, JSON helper |
| `lirr_agent/agents/` | Lead, Researcher, Writer, shared prompt text (`context.py`) |
| `lirr_agent/main.py` | FastAPI: `GET /`, `GET /health`, `POST /chat` |
| `lirr_agent/static/index.html` | Front end (temporary) |
| `tests/` | 32 tests with a fake Gemini; no API key needed |

---

## Step 0: Get set up

### Access

You can clone the repo without any access because it's public. To **push**, the owner
adds you on GitHub (Settings -> Collaborators -> Add people). Accept the email invite.

```bash
git clone https://github.com/gsr2149/LIRR-Ridership-Agent.git
cd LIRR-Ridership-Agent
```

### Python environment

Use Python 3.11 or 3.12. If you use Anaconda, run `conda deactivate` first so its
packages don't get mixed in.

```bash
# install uv: https://docs.astral.sh/uv/

uv sync
uv run pytest -q          # expect 32 passed
```

### Gemini key

Get a key at aistudio.google.com, then:

```bash
cp .env.example .env
```

Put the key in `.env` as `GEMINI_API_KEY=...`. **Never commit `.env`**; it's already in `.gitignore`.

### The database (not in git; it's large)

**Option A, fastest:** ask the owner to send you their `data/lirr.duckdb`, e.g. via
Google Drive. Put it at `data/lirr.duckdb`.

**Option B:** build it yourself. This downloads about 22M rows and takes a while.

```bash
python data/fetch.py --since 2025-01-01
python data/build_db.py
```

### Run it

```bash
uv run uvicorn app:app --reload
```

Open http://127.0.0.1:8000.

---

## Step 1: Apply the speed fix (`feat/speed`)

The file `patches/speed-up.patch` (committed next to this file) makes answers much
faster without changing what they find. It was tested against `main`: all 32 tests pass.

What it changes:
- **Gemini "thinking" off** (`GEMINI_THINKING_BUDGET=0` by default in `lirr_agent/llm.py`).
  Thinking added several seconds to every one of the 15-25 calls per answer.
- **Schema cached** (`lirr_agent/tools/schema.py`). `describe_table` on the 17M-row ticket
  table took seconds and every Researcher called it.
- **Researchers get the schema up front** (`context.schema_brief()`), so they go straight
  to SQL instead of spending two Gemini calls discovering tables. `list_tables` was
  removed from their tools.
- **Round cap 2** by default (`MAX_RESEARCH_ROUNDS`): the plan, plus one follow-up round.
- **Warm-up at startup** (`lirr_agent/main.py` lifespan), so the first question isn't slow.
- **Timing logs** in the uvicorn terminal, e.g. `research round 1: 3 researchers 21.4s`,
  plus a warning whenever Gemini rate-limits and the code waits.

```bash
git checkout main && git pull origin main
git checkout -b feat/speed
git apply patches/speed-up.patch
git rm patches/speed-up.patch
uv run pytest -q          # expect 32 passed
uv run uvicorn app:app --reload
```

Ask a question and watch the timing lines in the terminal. If you see
`Gemini rate limit ... waiting`, the free tier's per-minute limit is the bottleneck.
Add these lines to `.env` and restart:

```
RESEARCHER_MODEL=gemini-2.5-flash-lite
MAX_PARALLEL_RESEARCHERS=3
```

Commit, push, then **open a pull request and merge it** (see "Git rules" below):

```bash
git add -A app
git commit -m "Speed up research: no thinking, cached schema, schema in researcher prompt, round cap 2"
git push -u origin feat/speed
```

---

## Step 2: Front end (`feat/frontend`)

Replace `lirr_agent/static/index.html` (and add CSS/JS files in `lirr_agent/static/` if you like;
they're served at `/static/...`). Keep calling `POST /chat` exactly as the current page does.

**Required by the assignment.** The page must say:
- **What the agent explores:** the January 4, 2026 LIRR fare and ticketing changes
  (fares up ~4.5-8%, ten-trip discontinued, Day Pass replacing round-trip, higher
  onboard prices), using these 5 sources: MTA Railroad Ticket Sales, MTA Daily
  Ridership, MTA Monthly Ridership, MTA LIRR Stop-Level Occupancy, and the LIRR fare
  chart effective Jan 4, 2026.
- **Who it's for:** LIRR riders and commuters, transit advocates, journalists, students.
- **How to use it:** type a question or click an example. Answers take about 30-60
  seconds because several researchers query the data. A vague question gets a
  clarifying question back; answer it in the box. Every number has a citation like
  `[q_1a2b3c4d]` that matches a query in the Sources list and the trace.

**Should have (these make the "shows its work" part obvious):**
- 3-5 clickable example questions, e.g.:
  - "How did the 10 trip change in January 26?"
  - "What did riders buy instead of ten-trip and round-trip tickets?"
  - "Did more people buy tickets in the app after January 2026?"
  - "What does a monthly from Penn Station to Hicksville cost now?"
  - "Did things change?" (shows the clarification behaviour)
- **Render the answer as Markdown.** Answers use `**bold**`, bullets and a `---` Sources
  section. Use a pinned library from cdnjs, e.g. marked plus DOMPurify to sanitize.
- **Readable trace, grouped by agent** (collapsed by default):
  - Lead: the plan's sub-questions as a list; each review's decision and reason.
  - Each Researcher: its sub-question, each `run_sql` with the SQL in a code block and
    `columns`/`rows` as a small HTML table, and its `report_finding` summary.
  - Writer: the `verify_citations` result (cited ids, any removed).
- A loading indicator with an elapsed-seconds counter, since answers take a while.
- Show errors (e.g. 429 "usage limit") as a message, not a blank page.
- A "New conversation" button (reset `sessionId = null`).
- A short "Data limitations" note: ticket counts not revenue; only 2026 prices;
  data through 2026-09-30.
- Works on a phone (no horizontal scrolling; wrap long SQL).

Checks:
- `uv run pytest -q` still passes (`tests/test_chat.py` checks the page contains "LIRR").
- Try each example question in the browser.

---

## Step 3: Deploy to Cloud Run (`feat/deploy`)

The app needs `data/lirr.duckdb` inside the container. Put it in the image: it's
read-only at runtime.

### 3a. Files to add

`Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY data/lirr.duckdb ./data/lirr.duckdb
# Cloud Run sets $PORT (default 8080)
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
```

`.dockerignore`:

```
.venv/
.env
.git/
__pycache__/
.pytest_cache/
data/raw/
tests/
```

`.gcloudignore`. **This one is important.** Without it, `gcloud` uses `.gitignore`,
which excludes `*.duckdb`, so the database would never be uploaded:

```
.venv/
.env
.git/
__pycache__/
.pytest_cache/
data/raw/
```

Check the database size first:

```bash
ls -lh data/lirr.duckdb
```

Up to a few hundred MB is fine. If it's well over 1 GB, consider rebuilding with a
later `--since` date for `stop_occupancy`, or pre-aggregating `ticket_sales` by day.

Test the image locally if you have Docker:

```bash
docker build -t lirr-agent .
docker run --rm -p 8080:8080 --env-file .env lirr-agent
```

### 3b. Deploy

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

Store the Gemini key in Secret Manager, not in the image or the command line history:

```bash
printf "%s" "PASTE_KEY_HERE" | gcloud secrets create gemini-api-key --data-file=-
PROJECT_NUMBER=$(gcloud projects describe $(gcloud config get-value project) --format='value(projectNumber)')
gcloud secrets add-iam-policy-binding gemini-api-key \
  --member="serviceAccount:${PROJECT_NUMBER}-compute@developer.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

Then deploy:

```bash
gcloud run deploy lirr-agent \
  --source . \
  --region us-east1 \
  --allow-unauthenticated \
  --memory 2Gi --cpu 2 \
  --timeout 300 \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest \
  --set-env-vars GEMINI_MODEL=gemini-2.5-flash
```

Notes:
- `--timeout 300`: a research answer can take a minute or more; the default can cut it off.
- `--memory 2Gi`: DuckDB queries over 17M rows need room.
- Session memory is in-process, so a follow-up can land on a different instance. For a
  class demo that's fine; add `--max-instances 1` if clarifications get "forgotten".

Smoke test with the printed URL:

```bash
curl https://YOUR-URL/health
curl -s -X POST https://YOUR-URL/chat -H "Content-Type: application/json" \
  -d '{"message": "What does a zone 1 to zone 7 monthly cost?"}'
```

Logs if something breaks:

```bash
gcloud run services logs read lirr-agent --region us-east1 --limit 50
```

### 3c. Submission file

Project 1 had a `submission.json`. Add the same here:

```json
{"deploy_url": "https://YOUR-URL", "authors": ["gsr2149", "FRIEND_GITHUB_USERNAME"]}
```

Commit `Dockerfile`, `.dockerignore`, `.gcloudignore` and `submission.json`
(**not** the `.duckdb` file), push, open a pull request and merge it.

---

## Step 4: README (`feat/readme`)

Replace the one-line `README.md` with:
- What it is, who it's for, the live URL.
- The question and the 5 data sources (with data.ny.gov links: `5ypk-fceg`,
  `sayj-mze2`, `xfre-bxip`, `hb2b-cimm`, plus the fare chart in `data/fares/`).
- The architecture diagram above and the `/chat` request/response shape.
- How to run locally and run the tests (Step 0 above).
- **Data limitations**, which are required to be stated:
  - Fare table covers **January 4, 2026 prices only** (pre-2026 charts weren't
    available), so per-ticket price increases can't be computed from the data.
  - Ticket sales are **counts, not revenue**; "what riders pay" is estimated by
    matching ticket types and zones to 2026 prices.
  - Per the fare chart, peak and off-peak fares are the same within Zones 4-14.
  - Non-price changes (ten-trip ended, Day Pass, 11th mobile trip free, onboard
    surcharge) come from MTA announcements, labelled `[MTA announcement]`, not the datasets.
  - Ticket sales and occupancy in this build cover 2025-01-01 to 2026-09-30.
  - `device_class_type` codes OBTIM/TIM/STIM aren't documented in the dataset.
  - Stop-level occupancy covers M7/M9 electric trains only.
- 2-3 example questions with what the agent does (one clarify, one out-of-scope).

## Step 5: Final checks before submitting

- [ ] `main` has everything: on GitHub, every branch's pull request shows **Merged**.
- [ ] `uv run pytest -q` passes on `main`.
- [ ] The live URL loads the front end and answers a question with citations and a trace.
- [ ] A vague question ("Did things change?") gets a clarifying question.
- [ ] An out-of-scope question ("How crowded is the subway?") is declined with what it *can* answer.
- [ ] `submission.json` has the live URL.
- [ ] Tag the submitted version:

```bash
git tag -a v1.0 -m "Submitted version" && git push origin v1.0
```

---

## Git rules (learned the hard way)

- One branch per step: `git checkout main && git pull` first, then `git checkout -b feat/...`.
- **Pushing is not merging.** After `git push -u origin feat/...`, go to GitHub and do
  **Compare & pull request -> Create pull request -> Merge pull request -> Confirm merge**.
  Then `git checkout main && git pull origin main`. Earlier milestones got stuck
  because a pull request was created but never merged.
- Before every commit, run `git status` and make sure `.env`, `.venv/` and
  `data/lirr.duckdb` are **not** listed.
- Never run `git clone` inside the project folder; that creates a nested copy.

## Known gotchas

- **`No module named 'fastapi'` with `/Applications/anaconda3/...` in the error:**
  conda's Python is being used. Run `conda deactivate`, `source .venv/bin/activate`,
  then `python -m uvicorn ...`.
- **`Function calling is not enabled for api version v1`:** already fixed. `lirr_agent/llm.py`
  forces the `v1beta` API. Don't remove that.
- **`429 RESOURCE_EXHAUSTED`:** a Gemini rate limit. The code retries automatically;
  a daily limit needs a new day, another key, or billing on the Google Cloud project.
- **`cannot import name ... (unknown location)`:** a folder is missing its `__init__.py`
  (often leftover `__pycache__` after switching branches). Check you're on the right
  branch and that `main` is up to date.
- **Settings in `.env`** (all optional except the key): `GEMINI_API_KEY`, `GEMINI_MODEL`,
  `RESEARCHER_MODEL`, `GEMINI_THINKING_BUDGET`, `MAX_RESEARCH_ROUNDS`,
  `MAX_PARALLEL_RESEARCHERS`, `LIRR_DB_PATH`.
