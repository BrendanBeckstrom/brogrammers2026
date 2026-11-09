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
DEBRIEF_CACHE=on make api # answer the golden stacks, their memos and the demo go.mod from api/cache/, no network
```

**Demo safety:** with `DEBRIEF_CACHE=on`, cached requests never touch Elastic or the model (they carry `X-Debrief-Cache: hit`), and anything else falls through to the live path, or answers 503 when offline. `make cache` rebuilds the cache from a running API (`DEBRIEF_API`, default `http://localhost:8000`). `make golden ARGS=--cache` checks that every golden stack is served from it.

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

- `main` is protected (ruleset `protect-main`): changes land only through pull requests with green CI (`python`, `web`), and every PR is reviewed by Gitar.
- Every merge to `main` runs `.github/workflows/deploy.yaml`: it builds one image from the root `Dockerfile` (the API plus the built web app, on port 8080) and pushes it to GHCR. It then force-pushes the **`deploy` branch**, which is that commit plus one `deploy: <sha> [skip ci]` commit setting the image tag. Nothing is ever committed to `main` by the bot.
- Argo CD in our vcluster (Application `app`, `targetRevision: deploy`) syncs `deploy/` (Kustomize) into namespace `team-<team>`. The platform routes `https://<team>.cnh.radarhq.io` to Service `app`, port 80. The page footer shows the deployed commit (from `/api/version`).
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

Every key is optional in `deploy/deployment.yaml`, so pods start before the Secret exists. Restart the app after creating or changing it. A sixth key, `dash0-web-token`, is the ingest-only Dash0 website token for browser RUM; the app hands it to the page at runtime through `GET /api/config`, so it is never in git or in the image.

## Operating Debrief

How we ran Debrief on the hackathon's Kubernetes cluster today, through [Radar](https://radarhq.io): the Radar Cloud UI, plus Radar's MCP server from our coding agent.

**Cluster:** `brogrammers` in the Cloud Native Hackathon org; namespace `team-brogrammers`; live at https://brogrammers.cnh.radarhq.io. The app runs as 2 pods (`replicas: 2`, a PodDisruptionBudget, soft topology spread) with a read-only root filesystem and no service-account token.

### MCP question log (coding agent → Radar)

| What we asked | Radar MCP tool | What it told us / what we did |
|---|---|---|
| Which cluster is ours, and what's running in it? | `list_clusters`, `list_resources` | The starter app was running 2/2, with no Argo CD Application and no Secrets yet |
| Start GitOps | `apply_resource` (Argo CD `Application` → `targetRevision: deploy`) | Argo CD synced our image; the platform's placeholder pod was replaced |
| Is the Secret valid before a person applies it? | `apply_resource` with `dry_run: true` (dummy values) | The Secret structure was accepted, so a UI rejection was down to the pasted value, not our YAML |
| Did the rollout finish? | `manage_gitops` refresh, `wait_for`, `diagnose` | Rollouts confirmed after each merge; `diagnose` showed the new pod pulling the 300 MB image |
| Why are reranked playlists slow? | `get_workload_logs` (grep `rerank\|llm\|error`) | `llm call failed … model=claude-haiku-5-5 … error=APITimeoutError duration_ms=10858.4`, which started the incident below |
| Did secret changes take effect? | `list_resources` (key names only, never values), `manage_workload` restart | Restarted pods picked up the model, Dash0 and web-token keys |
| Is the cluster well configured? | `get_cluster_audit` (namespace `team-brogrammers`) | 3 findings → fixed in [#18](https://github.com/BrendanBeckstrom/brogrammers2026/pull/18); 2 multi-replica findings → fixed in [#19](https://github.com/BrendanBeckstrom/brogrammers2026/pull/19); now **0** |
| Will the PDB be accepted? | `apply_resource` with `dry_run: true` | Accepted by the vcluster before we merged #19 |

Secrets were always created by a person in Radar's UI. The agent only ever read key names.

### AI investigation of a real issue

We ran a Radar AI investigation on `Deployment team-brogrammers/app`.
- **Finding:** the app was healthy, but every rollout logged readiness and liveness probe failures, because Python took about 16 s to start listening. We added a `startupProbe` in [#14](https://github.com/BrendanBeckstrom/brogrammers2026/pull/14).
- **Follow-up** ("could anything in the cluster explain 12–14 s playlist requests?"): Radar checked Prometheus. CPU was about 0.3% of its limit with zero CFS throttling, memory about 22%, and no dropped packets. It concluded the time was spent outside Kubernetes, on the external LLM call. That ruled out the cluster.

### One incident through three tools

1. **Radar logs** showed the model call timing out (`APITimeoutError` after 10.9 s).
2. **Dash0 trace** `a9172d99794ae9239dd69ed65a29af54` showed `POST /api/playlist` at 12.6 s: `rerank.model` 11.5 s, of which the Anthropic `POST /v1/messages` call took 8.2 s, after a 3.3 s gap. **Dash0 Agent0** found the call sent 29 clips and got back 1,970 output tokens, 341 of them reasoning.
3. **Radar's investigation** ruled out the cluster.
4. **The fix** in [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15), reviewed by Gitar: at most 18 clips, in 3 parallel batches, with thinking off, a tight `max_tokens`, and the client built once at startup. Gitar also caught and fixed a thread-pool starvation bug in the same PR.
5. **After:** reranked playlists take about 3–4 s, and the model writes real why lines instead of falling back to templates.

## Team

- Dillan Mena
- Brendan Beckstrom

## AI disclosure

Per the hackathon rules: we used AI coding assistants (Claude Code, Anthropic) to help write code, configuration and docs. Commits made with an agent carry a `Co-Authored-By` trailer. The team designed the product, made the technical decisions, and reviewed and tested the code; every pull request is also reviewed by Gitar.

## Data

Talk recordings belong to their speakers and the CNCF. Debrief stores only auto-caption text for search, shows short quotes, and always links back to the original video on YouTube at the clip's timestamp. It never re-hosts video.
