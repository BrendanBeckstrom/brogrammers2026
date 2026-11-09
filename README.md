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
git clone https://github.com/BrendanBeckstrom/brogrammers2026.git
cd brogrammers2026
cp .env.example .env      # fill in values; .env is gitignored
make setup                # uv sync + npm ci
make test                 # lint + unit tests, Python and web
```

Run locally:

```bash
make api                  # API on http://localhost:8000
make web                  # web on http://localhost:5173 (proxies /api); add ?mock=1 to use sample data without the API
```

Build the index (needs `ELASTIC_URL` and `ELASTIC_API_KEY`):

```bash
make ingest               # data/talks.csv -> data/chunks.jsonl
make index                # create Elastic indexes, bulk load chunks
make label                # label new talks (data/talk_labels.csv), set talk_label in place
```

### Environment variables

| Var | Used by | What |
|---|---|---|
| `ELASTIC_URL`, `ELASTIC_API_KEY` | ingest, API | Elasticsearch endpoint and API key |
| `LLM_PROVIDER` | ingest, API | `anthropic` or `openai` |
| `LLM_API_KEY`, `LLM_RERANK_MODEL`, `LLM_MEMO_MODEL` | ingest (labels), API | Model credentials; a fast model for reranking, a stronger one for the memo |
| `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` | ingest, API | OpenTelemetry export to Dash0 |
| `DEBRIEF_CACHE` | API | `on` serves cached answers for the golden stacks (offline demo) |

## Deploy

GitOps through Argo CD, operated through [Radar](https://radarhq.io), on the [Radar hackathon starter](https://github.com/skyhook-io/cnh-starter) layout:

- Every push to `main` runs `.github/workflows/deploy.yaml`: it builds one image from the root `Dockerfile` (the API plus the built web app, on port 8080), pushes it to GHCR, and commits the new tag into `deploy/kustomization.yaml`.
- Argo CD in our vcluster syncs `deploy/` (Kustomize) into namespace `team-<team>`. The platform routes `https://<team>.cnh.radarhq.io` to Service `app`, port 80. The page footer shows the deployed commit (from `/api/version`).
- We watch syncs, rollouts, logs and Checks in Radar Cloud; agents use Radar's MCP server (see `AGENTS.md` and `.agents/skills/`).

Once the team name is known:

```bash
./scripts/setup.sh <team>   # fills in the team name and GitHub repo in deploy/, argocd/, examples/, AGENTS.md
```

Then apply `argocd/application.yaml` to the cluster once (through Radar) to start the sync.

### Secrets

The repo and the image are public, so the app reads its keys from the Kubernetes Secret `app-credentials` in `team-<team>`. **A person creates it by hand: never an agent, and never committed.** It has five keys: `elastic-url`, `elastic-api-key`, `llm-api-key`, `otel-endpoint` and `otel-headers`. Create it in Radar Cloud (**Resources** > **Secrets** > **+**), or with kubectl:

```bash
# Paste each value and press Enter; nothing is echoed.
read -rs ELASTIC_URL; read -rs ELASTIC_API_KEY; read -rs LLM_API_KEY; read -rs OTEL_ENDPOINT; read -rs OTEL_HEADERS
kubectl -n team-<team> create secret generic app-credentials \
  --from-literal=elastic-url="$ELASTIC_URL" \
  --from-literal=elastic-api-key="$ELASTIC_API_KEY" \
  --from-literal=llm-api-key="$LLM_API_KEY" \
  --from-literal=otel-endpoint="$OTEL_ENDPOINT" \
  --from-literal=otel-headers="$OTEL_HEADERS"
```

Every key is optional in `deploy/deployment.yaml`, so pods start before the Secret exists. Restart the app after creating or changing it.

## Team

- Dillan Mena
- Brendan Beckstrom

## AI disclosure

Per the hackathon rules: we used AI coding assistants (Claude Code, Anthropic) to help write code, configuration and docs. Commits made with an agent carry a `Co-Authored-By` trailer. The team designed the product, made the technical decisions, and reviewed and tested the code; every pull request is also reviewed by Gitar.

## Data

Talk recordings belong to their speakers and the CNCF. Debrief stores only auto-caption text for search, shows short quotes, and always links back to the original video on YouTube at the clip's timestamp. It never re-hosts video.
