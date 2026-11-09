# Debrief

**Don't watch 300 hours of KubeCon. Watch the 20 minutes that matter to your team.**

Debrief turns a conference's recorded talks into a short, cited briefing for one engineering team. Tell it what your team runs (pick tools or paste a `package.json` / `go.mod` / `requirements.txt` / Helm `Chart.yaml`), and it searches the transcript of every talk, returns a playlist of clips with exact start and end times, and turns that playlist into a one-page memo for your manager.

Built by team **Brogrammers** at the [WeMakeDevs Cloud Native Hackathon](https://www.wemakedevs.org/hackathons/cnh), Salt Lake City, Nov 9, 2026.

## How it works

- **Ingest (offline, once per conference):** download KubeCon auto-captions → clean → cut 60–90 s chunks → tag the tools each chunk mentions → label each talk (deep dive / intro / pitch) → index into Elastic.
- **Search (live):** Elastic hybrid search — keyword on tool names plus semantic on the team's "what hurts" sentence, fused with RRF — then grouped by talk, merged into 1–4 minute clips, reranked by a language model and filled to a ~20 minute budget.
- **Observability:** every request is one OpenTelemetry trace in Dash0 (stack parsing → search → rerank → memo).
- **Runs on Kubernetes:** one image serves the API and the web app; Argo CD deploys every push to `main`; operated through Radar.

Full spec: [`docs/BRIEF.md`](docs/BRIEF.md).

## Setup

Prerequisites: [uv](https://docs.astral.sh/uv/), Node 22+, make. Docker for building the image.

```bash
git clone https://github.com/DillanMena/wemakedevs-brogrammers.git
cd wemakedevs-brogrammers
cp .env.example .env      # fill in values; .env is gitignored
make setup                # uv sync + npm ci
make test                 # lint + unit tests, Python and web
```

Run locally:

```bash
make api                  # API on http://localhost:8000
make web                  # web on http://localhost:5173 (proxies /api)
```

Build the index (needs `ELASTIC_URL` and `ELASTIC_API_KEY`):

```bash
make ingest               # data/talks.csv -> data/chunks.jsonl
make index                # create Elastic indexes, bulk load chunks
```

### Environment variables

| Var | Used by | What |
|---|---|---|
| `ELASTIC_URL`, `ELASTIC_API_KEY` | ingest, API | Elasticsearch endpoint and API key |
| `LLM_PROVIDER` | ingest, API | `anthropic` or `openai` |
| `LLM_API_KEY`, `LLM_RERANK_MODEL`, `LLM_MEMO_MODEL` | ingest (labels), API | Model credentials; a fast model for reranking, a stronger one for the memo |
| `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` | ingest, API | OpenTelemetry export to Dash0 |
| `DEBRIEF_CACHE` | API | `on` serves cached answers for the golden stacks (offline demo) |

## Team

- Dillan Mena
- Brendan Beckstrom

## AI disclosure

Per the hackathon rules: we used AI coding assistants (Claude Code, Anthropic) to help write code, configuration and docs. Commits made with an agent carry a `Co-Authored-By` trailer. The team designed the product, made the technical decisions, and reviewed and tested the code; every pull request is also reviewed by Gitar.

## Data

Talk recordings belong to their speakers and the CNCF. Debrief stores only auto-caption text for search, shows short quotes, and always links back to the original video on YouTube at the clip's timestamp. It never re-hosts video.
