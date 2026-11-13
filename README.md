# ConsultFlow

A workbench for consultants who automate document and email work for their clients with LLM API calls. In one web UI you create a client, configure an agent (a fixed sequence of prompt steps, a provider/model, and the kind of input it accepts), feed it an uploaded document or an email (pasted, or uploaded as `.eml`), run it, and inspect every LLM call the run made: rendered prompt, output, token counts, cost in USD, latency, and the exact sources the prompt consumed. All of it is stored in PostgreSQL.

Stack: TypeScript + React (Vite) frontend, FastAPI + SQLAlchemy backend, PostgreSQL 16, pytest and Vitest, GitHub Actions.

## Architecture

```
React SPA (frontend/src)  --fetch /api-->  FastAPI (backend/app/main.py)
                                              |-- ingest.py   .eml / pasted email / PDF / HTML / text -> text + metadata + sha256
                                              |-- runner.py   template rendering, step execution, per-step logging
                                              |-- llm.py      provider adapter: offline | anthropic | openai, pricing table
                                              '-- models.py   SQLAlchemy models -> PostgreSQL
```

Tables (`backend/app/models.py`):

| table | contents |
|---|---|
| `clients` | client name, contact, notes |
| `agents` | owning client, provider, model, system prompt, `input_kind` (`document`/`email`), ordered `steps` (JSONB) |
| `input_sources` | uploaded document or email: kind, origin (`upload`/`paste`), filename, sha256, extracted text, metadata (subject, from, message-id, attachments, page count) |
| `runs` | agent config snapshot taken at run time, status, error, final output, total cost/latency/tokens |
| `run_steps` | one row per LLM call: position, provider, model, `sources` (JSONB), rendered prompt, output, status, error, input/output tokens, `cost_usd`, `latency_ms` |

Step prompts are templates. Variables: `{{input}}` (input text), `{{previous}}` (output of the previous step), `{{step.<name>}}` (output of an earlier named step), `{{subject}}`, `{{sender}}`, `{{filename}}` (input metadata), `{{client}}`. Agent configs are validated on save: unknown variables, `{{previous}}` in step 1, forward references and duplicate step names are rejected with a 422.

Every variable that pulls data into a prompt is recorded in that step's `sources`, e.g. `email:#1 invoice-dispute.eml sha256:03a984550919`, `email-meta:#1`, `step:1 extract`. Latency is wall-clock time around the provider call. Cost is computed from returned token usage and the per-model price table in `backend/app/llm.py` (USD per 1M tokens; edit it when list prices change). A failing step is logged with its error and latency, and the run stops there with status `failed`.

LLM providers sit behind one `complete(model, system, prompt)` interface:

- `offline` / `offline-extractive-v1`: deterministic, no network, priced at $0. Ranks sentences by word frequency and lists e-mail addresses, money amounts and dates it finds. It exists so the whole workflow, the tests and CI run without an API key; its output is extractive, not generative.
- `anthropic` (Messages API) and `openai` (Chat Completions) over HTTPS, using `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` from the environment. Providers without a key are shown as "(no API key)" in the UI and a run against them is recorded as failed with the reason.

The production container serves the built SPA and the API from one uvicorn process.

## Quickstart

```bash
docker compose up --build        # UI + API on http://127.0.0.1:18410, Postgres on 127.0.0.1:55441
```

Open http://127.0.0.1:18410, create a client, click "New agent", configure steps, upload `samples/invoice-dispute.eml` (or paste an email), pick it as input, and click "Run agent". The run panel shows the per-step table and the prompts/outputs.

To use a real provider, export `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` before `docker compose up`; compose passes them through.

Scripted end-to-end check of the same flow over HTTP (also checks the UI bundle is served):

```bash
uv run --no-project --with httpx python scripts/e2e_flow.py --base-url http://127.0.0.1:18410
# optional: --provider anthropic --model claude-haiku-4-5
```

## Development and tests

Backend tests run against a real PostgreSQL (database `consultflow_test`, created by `db/init.sql`):

```bash
docker compose up -d --wait db
cd backend
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -v
```

Frontend:

```bash
cd frontend
bun install
bun run typecheck
bun run test         # Vitest + Testing Library, jsdom
bun run build
bun run dev          # Vite on :5173, proxies /api to 127.0.0.1:18410
```

CI (`.github/workflows/ci.yml`) runs three jobs: backend (ruff + pytest against a Postgres service container), frontend (tsc + Vitest + build), and compose (builds the image, `docker compose up`, runs `scripts/e2e_flow.py`).

## Measured results

All numbers below come from runs on 2026-11-13 on the development machine (Linux, 8 cores), with the offline provider.

- `uv run pytest -q`: 30 passed (ingest parsing incl. `.eml`/HTML/PDF, provider adapters with mocked HTTP, cost math, API flow, validation, failure logging, and a direct SQL check that `run_steps` rows are persisted).
- `bun run test`: 8 passed in 2 files.
- `scripts/e2e_flow.py` against `docker compose up`: 3 runs, all `succeeded`, 8 logged steps. Example run (3-step email agent on `samples/invoice-dispute.eml`): 476 input / 345 output tokens, total latency 1 ms, cost $0.000000 (the offline model is free). Token counts are the offline provider's word/punctuation count, not a vendor tokenizer.
- The same flow was driven through the UI in headless Chromium (create client, new two-step agent, upload `.eml`, run, view run panel); the run succeeded and its two `run_steps` rows were confirmed with `psql`.

No real-provider cost or latency figures are reported, because no working API key was available (see Limitations).

## Limitations

- Real LLM cost/latency not measured: the `ANTHROPIC_API_KEY` present in the build environment was rejected (`401 invalid x-api-key`) and no OpenAI key was available. The Anthropic and OpenAI adapters are covered by tests with mocked HTTP responses only. The failed real-provider attempt is itself logged as a failed run, which is the intended behaviour.
- Runs execute synchronously inside the request; a long multi-step agent on a slow model blocks that HTTP request until it finishes. There is no queue or streaming.
- Prices in `llm.PRICING` are a static table and must be kept current by hand.
- No authentication or multi-tenant access control; it is a single-user workbench intended to run locally or behind your own auth.
- Schema is created with `create_all` on startup; there are no migrations.
- Document ingestion supports text, Markdown, CSV, JSON, HTML, PDF with a text layer, and `.eml`. Scanned PDFs (no OCR), Office files, and email attachments' contents are not extracted (attachment filenames are recorded).

## License

MIT
