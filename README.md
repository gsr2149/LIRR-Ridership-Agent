# LIRR Fare Desk: what the January 2026 fare change did to riders

**Live:** https://lirr-agent-343981342907.us-east1.run.app · **Path:** Deep Research · **Team:** hgn2108, gsr2149

On January 4, 2026 the Long Island Rail Road raised fares, retired the ten-trip ticket, replaced
round trips with a Day Pass and made buying onboard more expensive. This agent answers one question
people actually have about that change:

> **How did the January 2026 LIRR fare and ticketing changes change what riders buy and what they pay?**

Ask it in plain English. A **Lead** agent plans the research, two to four **Researchers** query
17 million rows of MTA ticket sales, ridership and train-crowding data in parallel, and a **Writer**
reports. Every number in the answer is cited to the SQL query it came from, and code checks that the
number actually appears in that query's rows.

## Who it's for

- **LIRR riders** working out what to buy now that the ten-trip is gone, and what their trip costs now.
- **Transit advocates and reporters** checking the MTA's claims against the MTA's own open data.
- **Students of transit policy** curious how one fare change ripples through a commuter railroad.

## What it does that a chatbot can't

| | |
|---|---|
| **Uses data the model doesn't have** | 2025–2026 ticket sales and ridership, the January 2026 fare chart, and a reconstruction of the fares in force before it. Asked the same questions with no tools, the same model refuses or gets the price wrong (see the sample queries below). |
| **Shows the research live** | The page streams the Lead's plan, each Researcher's SQL and findings, the Lead's gap review and the Writer's checks as they happen (`POST /chat/stream`). |
| **Checks its own numbers** | Code, not the model, checks that every `[q_…]` citation is a query that ran, and that every number next to it appears in that query's rows (or is a simple difference, ratio, percent change or share of them). If one doesn't, the Writer has to fix it, and anything still unmatched is flagged under the answer. |
| **Before and after prices** | The MTA only publishes the current fare chart. We rebuilt the August 2023 – January 2026 fares from the MTA's adopted 2023 fare table, so the agent can compute real price changes for 84% of 2025 tickets. |
| **Spending estimates with stated coverage** | An offline build step prices every one of 89 million ticket sales (`ticket_spend_est`), so "what do riders pay" questions get a consistent estimate plus the share of tickets that could be priced. |
| **Found a quirk in the MTA data** | Every `ten_trip` count in the ticket-sales data is a multiple of 10: the dataset counts **rides**, ten per ten-trip ticket, and round trips mostly per leg. The data dictionary doesn't say so. The agents are told, and the spending table divides ten-trip counts by 10. |
| **Instant commute check** | Pick two stations to see every ticket's price before and after, with the change, retired tickets and new tickets. This comes straight from the fare charts, with no model call (`GET /api/fare`). |
| **Says what the data can't answer** | Vague questions get a clarifying question back. Out-of-scope questions (the subway, other years, individual riders) are declined with what *can* be answered. Every answer ends with "What the data can't tell us". |

## How it works

```
question ─► Lead.plan ── vague? ──► one clarifying question (with options)
               │ ── out of scope? ─► what the data can and can't answer
               ▼
        2-4 sub-questions, each a different angle
        (direct effect · substitution · how riders buy · ridership context · price)
               │  run in parallel, each in a fresh context
   ┌───────────┼───────────┬───────────┐
researcher-1 researcher-2 researcher-3 researcher-4     tools: run_sql, describe_table, lookup_fare
   └───────────┴─────┬─────┴───────────┘
                     ▼
              Lead.review ── gap or surprise? ──► follow-up Researchers (round cap: 2)
                     ▼
                  Writer ──► answer with [q_xxxxxxxx] / [fare chart …] citations
                     ▼
     code: verify_citations · verify_numbers (one rewrite if a number isn't in its rows)
           · charts from the cited query results · Sources list built from the real queries
```

| Deep Research requirement | Where |
|---|---|
| At least three agent shapes with different jobs | Lead (`lirr_agent/agents/lead.py`), Researcher (`researcher.py`), Writer (`writer.py`) |
| Lead fans out to several Researchers in one turn, in parallel, in fresh contexts | `lead.dispatch()`: a thread pool; each Researcher sees only its sub-question |
| Lead goes back for more when there's a gap, up to a turn cap | `lead.review()`, `MAX_RESEARCH_ROUNDS` (default 2) |
| Vague questions get clarification | Lead `clarify` action (try "Did things change?") |
| Output is accurately cited | `writer.check_citations`, `agents/grounding.py`; Sources are built by code from queries that ran |
| Sub-agent tool calls in `tool_calls` with an `agent` field | every entry is `{agent, name, args, result}`; agents are `lead`, `researcher-N`, `writer` |

### API

`POST /chat` keeps the Project 1 shape:

```json
// request
{"message": "What replaced the ten-trip?", "session_id": "optional"}
// response
{"response": "...markdown with citations...", "session_id": "…",
 "tool_calls": [{"agent": "researcher-2", "name": "run_sql", "args": {"sql": "…", "purpose": "…"},
                 "result": {"query_id": "q_1a2b3c4d", "columns": […], "rows": […]}}, …]}
```

Besides real tool calls (`run_sql`, `describe_table`, `lookup_fare`), the trace records each agent's
decisions: `plan`, `dispatch_researchers`, `report_finding`, `review`, `verify_citations`,
`verify_numbers` and `chart`.

| Endpoint | |
|---|---|
| `POST /chat/stream` | Same answer, streamed as NDJSON events (`phase`, `researcher_start`, `step`), then a final `done` line with the `/chat` body |
| `GET /api/fare?origin=…&destination=…` | Before/after fares between two stations or zones (no model call) |
| `GET /api/stations` | LIRR stations and their fare zones |
| `GET /health` | Liveness |

## Data

One source per team member, and then some: five public sources, plus one table we built from them.

| Table | Source | Rows | What it adds |
|---|---|---|---|
| `ticket_sales` | [MTA Railroad Ticket Sales: Beginning 2023](https://data.ny.gov/d/5ypk-fceg) (`5ypk-fceg`), LIRR + Metro-North, from 2025-01-01 | 17.4M | What riders bought, by day, ticket kind, fare type, zone pair and sales channel (app, machine, office, onboard) |
| `daily_ridership` | [MTA Daily Ridership and Traffic](https://data.ny.gov/d/sayj-mze2) (`sayj-mze2`) | 18K | Whether people rode less, or just bought differently |
| `monthly_ridership` | [MTA Monthly Ridership / Traffic](https://data.ny.gov/d/xfre-bxip) (`xfre-bxip`) | 1K | Longer-run context back to 2008 |
| `stop_occupancy` | [MTA LIRR Stop-Level Occupancy](https://data.ny.gov/d/hb2b-cimm) (`hb2b-cimm`), from 2025-01-01 | 4.9M | How full trains were (M7/M9 electric trains) |
| `lirr_fares` | LIRR fare charts effective **2023-08-20** and **2026-01-04**, hand-entered from MTA PDFs in [`data/fares/`](data/fares) | 772 | Prices before and after the change |
| `ticket_spend_est` | Built by `data/build_db.py`: `ticket_sales` × `lirr_fares` | 618K | Estimated spending per day, ticket kind and zone pair |

The pre-2026 fares come from the "Proposed" column of the MTA's 2023 LIRR fare table, which the MTA
board adopted in July 2023 (effective August 20, 2023). They match the 2024–2025 station fare chart,
e.g. a Zone 1→7 monthly at $287.00 and a ten-trip peak at $145.00. That table covers trips to and from
Zone 1 (84% of 2025 tickets) and a sample of other zone pairs. Full column notes are in
[`data/SCHEMA.md`](data/SCHEMA.md).

**Load it yourself.** The DuckDB file is about 620 MB, too big for GitHub. In production it is
downloaded from a GCS bucket at startup (`LIRR_DB_GCS_URI`). To rebuild it from scratch:

```bash
uv sync
uv run python data/fetch.py --since 2025-01-01   # data.ny.gov Socrata API -> data/raw/ (about 22M rows)
uv run python data/build_db.py                   # -> data/lirr.duckdb and data/SCHEMA.md
```

After editing `data/fares/lirr_fares.csv`, run `uv run python data/build_db.py --fares-only` to reload
the fares and rebuild the spending table.

### What the data can't answer

- **Revenue.** Ticket sales are counts. Spending is an estimate (tickets × chart prices): no discounts,
  refunds or unused tickets, and family and intermediate fares are unpriced.
- **Prices before August 2023**, and pre-2026 prices for zone pairs missing from the 2023 table.
- **Individual riders.** Nobody is tracked across purchases, so "ten-trip buyers switched to X" is
  inferred from aggregate shares.
- **Ticket counts are closer to rides than purchases**: a ten-trip counts as 10, and round trips mostly per leg.
- **Onboard and device codes.** `OBTIM`/`TIM`/`STIM` aren't documented, and crowding data covers M7/M9 trains only.
- **Dates.** Ticket sales and crowding run from 2025-01-01 to 2026-09-30.
- **Background facts.** The non-price changes (the ten-trip ended, the Day Pass was introduced) come from
  MTA announcements and are cited `[MTA announcement]`, not to a dataset.

## Sample queries

Each query below was run through this agent and through the same model (`gemini-3.5-flash`) with no
tools and no system prompt. The expected answers come from SQL run directly on the database.

### 1. A price before vs after

**Query:** *How much did a monthly ticket between Penn Station and Hicksville cost before and after the January 2026 LIRR fare change?*

**Expected answer:** **$287.00 before → $299.75 after (+$12.75, +4.4%).** Zone 1 (Penn Station) to Zone 7 (Hicksville).

**Where it comes from:** `lirr_fares` table, rows `pre_2026 / 1 / 7 / monthly` and `post_2026 / 1 / 7 / monthly`; from `data/fares/lirr_fare_table_2023-08-20.pdf` and `data/fares/lirr_fare_chart_2026-01-04.pdf` (also `GET /api/fare?origin=Penn Station&destination=Hicksville`).

**Same model, no tools, no system prompt** (`gemini-3.5-flash`):

> Because my knowledge cutoff is in January 2025, I do not have information regarding any LIRR fare changes implemented in January 2026, nor do I have access to the ticket prices after that date. 
>
> However, prior to that period (under the fare structure established in August 2023), a monthly ticket between Penn Station (Zone 1) and Hicksville (Zone 7) cost **$308.00**.

**This agent** (9 s; code check: 6 of 6 cited numbers match the query rows):

<details><summary>Full answer with sources</summary>

> Before the January 4, 2026 LIRR fare change, a monthly ticket between Penn Station and Hicksville cost $287.00, and it increased to $299.75 after the change [q_5a0786d0]. This represents an absolute increase of $12.75 [q_5a0786d0], or approximately 4.4% [fare chart 2023-08-20].
>
> **Supporting Evidence:**
> * **Zone Designations:** Penn Station is designated as Zone 1 and Hicksville is designated as Zone 7 [MTA announcement]. 
> * **Pre-Change Price:** The monthly ticket price between Zone 1 and Zone 7 was $287.00 [q_5a0786d0], which was effective from August 20, 2023, through January 3, 2026 [fare chart 2023-08-20].
> * **Post-Change Price:** Following the fare hike on January 4, 2026, the monthly ticket price between Zone 1 and Zone 7 rose to $299.75 [q_5a0786d0] [fare chart 2026-01-04]. This aligns with the MTA's announcement that monthly fares would rise by up to about 4.5% [MTA announcement].
>
> **What the data can't tell us:**
> While the database provides the exact fare schedules, the data cannot tell us how many riders actually purchased this specific monthly ticket before or after the rate increase, or if any riders switched to alternative ticket types (such as the new Day Pass) as a result of the fare change.
>
> ---
> **Sources**
> - [q_5a0786d0] Verify the exact pre_2026 and post_2026 prices for monthly tickets between Zone 1 and Zone 7 in the lirr_fares table. — lirr_fares (LIRR fare tables (hand-entered from MTA PDFs))
> - [fare chart 2023-08-20] MTA LIRR fares effective August 20, 2023, from the 2023 Table of Proposed Fares as adopted (data/fares)
> - [fare chart 2026-01-04] MTA/LIRR Station Fares effective January 4, 2026 (data/fares)
> - [MTA announcement] MTA fare and ticketing changes effective January 4, 2026 (background, not from the datasets)

</details>

**Verdict:** The baseline admits it doesn't know the 2026 price and gets the old price **wrong** ($308 instead of $287). The agent gets both right and cites the fare charts.

### 2. What replaced the ten-trip

**Query:** *What did LIRR riders buy instead of ten-trip and round-trip tickets after January 2026?*

**Expected answer:** Comparing Feb–Sep 2025 with Feb–Sep 2026 (LIRR ticket counts): one-way share **77.2% → 92.2%**; the new **Day Pass 2,000,652 tickets (5.9%)**; round trip **4,902,309 → 3,946**; ten-trip **2,443,200 → 0**; monthly 511,234 → 548,175. LIRR ridership rose **3.6%** (54,278,048 → 56,250,175), so riders switched tickets rather than leaving.

**Where it comes from:** `ticket_sales` (data.ny.gov `5ypk-fceg`), `sold_as` by period with `railroad = 'LIRR'`; `daily_ridership` (`sayj-mze2`) with `mode = 'LIRR'`.

**Same model, no tools, no system prompt** (`gemini-3.5-flash`):

> Because my knowledge cutoff is in January 2025, I cannot provide information about events or decisions occurring after January 2026, as this date is in the future. I do not have access to real-time updates or future data regarding LIRR ticket changes.

**This agent** (13 s; code check: 14 of 14 cited numbers match the query rows):

<details><summary>Full answer with sources</summary>

> After LIRR discontinued ten-trip and round-trip tickets in January 2026, riders overwhelmingly substituted them with one-way tickets, while a smaller portion adopted the newly introduced Day Pass [q_fc22892a]. Overall LIRR ridership actually increased during this period [q_7e3574e4, q_0adc3412], confirming that the shift in sales represents a change in buyer behavior rather than a loss of riders.
>
> Supporting evidence shows:
> * **The One-Way Surge:** Prior to the changes (October–December 2025), one-way tickets averaged a 77.9% share of sales, round-trips averaged 13.8%, and ten-trips made up 6.4% to 6.9% [q_fc22892a]. By February 2026, ten-trips fell to 0.00% and round-trips dropped to 0.06%, while one-way ticket shares surged to over 92% of all sales through May 2026 [q_fc22892a]. 
> * **Day Pass Adoption:** The newly introduced Day Pass [MTA announcement] captured a stable 5.4% to 5.9% share of monthly sales between February and May 2026 [q_fc22892a]. Monthly and weekly ticket shares remained relatively steady [q_fc22892a].
> * **Channel Shifts:** The elimination of these tickets coincided with an accelerated migration to mobile purchases, which rose from 72.13% to 75.29% of sales, while physical station purchases (TVMs and TOMs) declined [q_2c54d1e8]. 
> * **Financial Impact:** Driven by both the fare hikes [MTA announcement] and these purchasing shifts, the estimated average spend per ticket sold rose from $13.15 to $14.64 (an 11.3% nominal increase) when comparing February–September 2025 to February–September 2026 [q_629291b6].
>
> **What the data can't tell us:**
> Because the database tracks ticket units sold (where a ten-trip counts as 10 tickets and round-trips are counted by individual legs) rather than individual transactions [q_fc22892a, q_629291b6], we cannot isolate the exact number of individual transaction purchases. Additionally, the data cannot determine if the mobile channel shift was caused by the ticket changes or simply reflected ongoing multi-year mobile adoption trends [q_2c54d1e8].
>
> ---
> **Sources**
> - [q_0adc3412] Verify with daily ridership counts for Feb-Sep 2025 vs Feb-Sep 2026. — daily_ridership (MTA Daily Ridership and Traffic: Beginning 2020, https://data.ny.gov/d/sayj-mze2)
> - [q_2c54d1e8] Analyze LIRR channel mix (device_class_type) before and after January 4, 2026. — ticket_sales (MTA Railroad Ticket Sales: Beginning 2023, https://data.ny.gov/d/5ypk-fceg)
> - [q_629291b6] Calculate average spend per ticket comparing identical calendar months (Feb-Sep 2025 vs Feb-Sep 2026) to ensure a seasonally balanced comparison. — ticket_spend_est (Estimated spending: LIRR ticket_sales x lirr_fares (built by data/build_db.py))
> - [q_7e3574e4] Compare LIRR monthly ridership in Feb-Sep 2025 vs Feb-Sep 2026 to see the overall change in ridership. — monthly_ridership (MTA Monthly Ridership / Traffic Data: Beginning January 2008, https://data.ny.gov/d/xfre-bxip)
> - [q_fc22892a] Calculate the monthly ticket count and share for each sold_as type (one_way, round_trip, day_pass, ten_trip, monthly, weekly) from October 2025 through May 2026. — ticket_sales (MTA Railroad Ticket Sales: Beginning 2023, https://data.ny.gov/d/5ypk-fceg)
> - [MTA announcement] MTA fare and ticketing changes effective January 4, 2026 (background, not from the datasets)

</details>

**Verdict:** The baseline can't answer at all. The agent finds the substitution (one-way plus Day Pass), checks it against ridership, and notes the counting quirk (a ten-trip is counted as 10 tickets).

### 3. What riders spend

**Query:** *Roughly how much more did LIRR riders spend on tickets in February-September 2026 than in February-September 2025?*

**Expected answer:** Estimated spending **$458.4M (Feb–Sep 2025) → $496.4M (Feb–Sep 2026), about +$38.0M (+8.3%)**, with 94.1% and 96.9% of tickets priced. Because more 2025 tickets are unpriced, the true increase is somewhat smaller.

**Where it comes from:** `ticket_spend_est` (built offline by `data/build_db.py` from `ticket_sales` × `lirr_fares`), `sum(est_spend_usd)` by year for months 2–9.

**Same model, no tools, no system prompt** (`gemini-3.5-flash`):

> I cannot answer this question because the dates you are asking about—February–September 2025 and February–September 2026—occur after my knowledge cutoff date of January 2025. I do not have access to real-time data or future financial reports for the Long Island Rail Road (LIRR). 
>
> To find this information once those periods have passed, you can consult the MTA’s official financial statements, budget reports, or rider statistics, which are regularly published on the Metropolitan Transportation Authority (MTA) website.

**This agent** (13 s; code check: 13 of 13 cited numbers match the query rows):

<details><summary>Full answer with sources</summary>

> LIRR riders spent approximately $37.99 million more on tickets during February–September 2026 than in the same period in 2025 [q_6250b402]. This increase was driven by a combination of a 3.63% rise in ridership [q_a872ee0d] and fare increases implemented on January 4, 2026 [MTA announcement].
>
> **Supporting Evidence:**
> *   **Total Estimated Spending:** Spending rose from $458,380,931.50 in February–September 2025 to $496,373,534.50 in February–September 2026 [q_6250b402]. 
> *   **Ridership Increase:** Total ridership grew from 54,278,048 (average 224,289.45 daily riders) in the 2025 period to 56,250,175 (average 232,438.74 daily riders) in the 2026 period [q_a872ee0d].
> *   **Ticket Purchase Shifts:** Following the elimination of ten-trip tickets and round-trip tickets [MTA announcement], sales shifted primarily to one-way tickets (which grew from 26,918,482 to 31,258,499 [q_bb67553c]) and the newly introduced day passes, which recorded 2,000,652 ticket sales in 2026 [q_bb67553c].
>
> **What the data can't tell us:**
> Because the database was unable to find matching prices for 6.31% of the ticket units in 2025 (representing unpriced stations or ticket types), the actual 2025 spending was higher than the estimated $458.38 million [q_6250b402]. Consequently, the true spending gap between 2025 and 2026 is slightly smaller than the estimated $37.99 million difference. Additionally, because the ticket sales table counts individual ticket legs rather than unique transactions, we cannot determine the exact amount spent per individual customer [q_bb67553c].
>
> ---
> **Sources**
> - [q_6250b402] Calculate total estimated spending and ticket units sold/priced for Feb-Sep 2025 vs Feb-Sep 2026 using ticket_spend_est — ticket_spend_est (Estimated spending: LIRR ticket_sales x lirr_fares (built by data/build_db.py))
> - [q_a872ee0d] Compare overall daily ridership average and total on the LIRR for Feb-Sep 2025 vs Feb-Sep 2026. — daily_ridership (MTA Daily Ridership and Traffic: Beginning 2020, https://data.ny.gov/d/sayj-mze2)
> - [q_bb67553c] Compare LIRR ticket sales volume by sold_as category between Feb-Sep 2025 and Feb-Sep 2026 — ticket_sales (MTA Railroad Ticket Sales: Beginning 2023, https://data.ny.gov/d/5ypk-fceg)
> - [MTA announcement] MTA fare and ticketing changes effective January 4, 2026 (background, not from the datasets)

</details>

**Verdict:** The baseline can't answer. The agent gives the estimate, says what share of tickets it could price, and explains which way that bias runs.

More to try: "Did more riders buy in the TrainTime app after the change?", "Did trains get less
crowded after the fare increase?", "Did things change?" (to see a clarifying question) and
"How crowded is the subway?" (to see a decline).

## Run it locally

```bash
uv sync
uv run pytest -q                 # 50 tests, with a fake Gemini; no API key needed
```

Put the database at `data/lirr.duckdb` (build it as above, or copy it from the bucket with
`gcloud storage cp gs://lirr-agent-data-510817/lirr.duckdb data/`). Then pick a Gemini backend in `.env`
(see `.env.example`): either `GEMINI_API_KEY=…`, or Vertex AI with `GOOGLE_GENAI_USE_VERTEXAI=true`,
`GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION=global`. Then:

```bash
uv run uvicorn app:app --reload  # http://127.0.0.1:8000
```

## Deployment

Cloud Run (`us-east1`), built from the `Dockerfile`. The database is pulled from GCS when the
container starts, and Gemini runs through Vertex AI as the service's own service account, so there are
no API keys. Settings: 4 GiB / 2 CPU, request timeout 600 s, one instance (chat history lives in
memory), and each Gemini request times out after 90 s with retries. Answers usually take 15–40 s.

## Repository layout

| Path | |
|---|---|
| `app.py` | Entry point (`uvicorn app:app`) |
| `lirr_agent/agents/` | `lead.py`, `researcher.py`, `writer.py`, `grounding.py` (number check), `charts.py`, `context.py` (shared prompt text) |
| `lirr_agent/tools/` | `run_sql` (read-only, row cap, timeout, query ids), `describe_table`, `lookup_fare`, schema notes |
| `lirr_agent/llm.py` | Gemini tool loop with per-agent tagging, retries and timeouts |
| `lirr_agent/events.py`, `main.py` | Live event stream; FastAPI routes |
| `lirr_agent/static/` | The front end (no build step) |
| `data/` | `fetch.py`, `build_db.py`, `SCHEMA.md`, the fare CSV and the source PDFs |
| `tests/` | Tools, agent loop, Lead/Writer behaviour, citation and number checks, charts, streaming |

Not affiliated with the MTA. Data © MTA, via data.ny.gov.
