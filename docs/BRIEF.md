# Debrief — build brief

## Overrides (these win over everything below)

- Deadline: submissions due 5:30 PM MT on Mon Nov 9, 2026; presentations 5:30–6:30. The original 9:00–19:00 plan is compressed into 11:00–17:30.
- Pre-work: the event has started; every task is allowed. Ignore the "Before the start" table.
- Deploy: follows the Radar starter (skyhook-io/cnh-starter), not `deploy/k8s/`. Manifests live in `deploy/` (Kustomize), Argo CD syncs them from `main`, a GitHub Action builds one image per push to `main`. The image serves the API and the built web app together on port 8080 behind Service `app` port 80 at `https://<team>.cnh.radarhq.io`. The ingest Job uses the same image. Secrets come from a Kubernetes Secret created by a person, never committed.
- Elastic: a serverless Elasticsearch project at `ELASTIC_URL`.
- Language model: Claude or OpenAI, selected by `LLM_PROVIDER` (`anthropic` | `openai`); still only `api/llm.py` talks to the provider.
- Code host: GitHub (BrendanBeckstrom/brogrammers2026); one PR per task titled `Task N: ...`, reviewed by Gitar. Nothing is pushed until Gitar is installed on the repo.
- Search grouping: group hits by `talk_id` in Python (at most three per talk), as the Contracts section says. Where the Product/Architecture/Data pipeline sections (from the overview) say "collapse" or list a bare "Argo" alias, the Contracts and Tasks sections win.

**How to use this brief:** build version one exactly as described: stack in, clip playlist out, memo on request. Work through the tasks in order where they depend on each other; each task ends with a check. Run it and paste its output into the PR. If a check still fails after two attempts at a fix, stop and report what you tried.

## Product

**Debrief has one input, your team's stack, and two outputs: a clip playlist and a memo.** Everything else is optional.

### Flow 1: tell it your stack

1. Pick tools from a list of chips (Argo CD, Istio, Prometheus, Postgres, and so on), or paste a dependency file: `package.json`, `go.mod`, `requirements.txt` or a Helm `Chart.yaml`.
2. Debrief maps package names to tool names. For example, `github.com/argoproj/argo-cd` becomes Argo CD.
3. Optionally, add one sentence about what hurts right now, such as "our Argo CD syncs are slow." That sentence boosts matching clips.
4. Optionally, tick the talks someone on the team attended. That unlocks a "what we missed" filter.

### Flow 2: get the playlist

Debrief searches every talk at the conference and returns a ranked list of clips, with the total watch time at the top ("22 minutes across 9 talks"). Each clip card shows:

- the talk title and speaker
- the start and end time of the clip, usually one to four minutes
- one line on why it matters to this team, naming the matched tool
- two or three lines quoted from the transcript
- a label: deep dive, introduction or product pitch, judged from the transcript rather than the abstract
- a Watch button that opens the video at the clip's first second

Filters: all talks, talks we attended, talks we missed.

An illustrative card (not a real talk):

> **Scaling GitOps to 10,000 apps** · 14:32 to 18:10 · Deep dive Why for you: you run Argo CD; this is how they cut sync time. "We stopped syncing everything on every commit and started sharding by..."

### Flow 3: make the memo

One click turns the playlist into a one-page memo:

- the top three takeaways for this stack, each linked to its clip
- two or three action items the team could try
- a short "watch together" list for a team meeting

The memo copies out as Markdown, ready for Slack, email or a wiki.

### Not in version one

Live features during the conference, note-taking, slides, more than one conference at a time, and user accounts.

## Architecture

**Debrief has three moving parts: an offline ingest job, a small API, and one Elastic index they share.** Keeping it this small is what makes a one-day build possible.

One Elastic index of transcript clips powers every request. Ingest fills the index once, offline; every team request reads it live.

- **Offline ingest:** YouTube, CNCF channel (recorded talks with automatic captions) → captions → **Ingest job** in the Kubernetes cluster (download, clean, chunk, tag tools, label talks) → index → **Elastic**.
- **Live request path:** **Browser** (stack in; playlist, clips and memo out) ↔ requests ↔ **Web app and API** in the Kubernetes cluster (parse stack, search, build clips) ↔ query ↔ **Elastic**. The API sends rerank and memo calls to the **Language model** (reranks clips, writes the why line and memo) and traces to **Dash0** (traces every request and every ingest run). The browser goes to YouTube to watch a clip.
- **Elastic:** clips index: one-minute chunks, tool tags, timestamps; talks index: metadata. Hybrid search: keyword plus semantic with RRF, collapsed by talk.

The ingest job runs once per conference. A team's request touches only the API, Elastic and the language model, and the Watch button goes straight to YouTube at the clip's first second.

### Stack

| Layer | Choice | Why |
| --- | --- | --- |
| Ingest | Python with `yt-dlp` or `youtube-transcript-api`, run as a Kubernetes Job | The transcript tools are Python; a Job makes adding a conference one command |
| API | Python with FastAPI | Same language as ingest, so the tool dictionary and chunking code are shared |
| Search | An Elastic Cloud trial, or Elasticsearch on the cluster through Elastic's ECK operator | Hosted is fastest to stand up; use ECK only if the Kubernetes story needs it |
| Semantic search | Elastic's `semantic_text` field with a built-in model such as ELSER | No separate embedding service to run |
| Language model | A fast model for reranking, a stronger one for the memo | Reranking runs on about 30 clips per request; the memo runs once |
| Frontend | React with Vite and Tailwind | Quick to build; one page with three views |
| Tracing | OpenTelemetry SDK exporting to Dash0 | Standard instrumentation; spans carry the matched tools and scores |
| Deploy | Containers on Kubernetes, through Radar | Covers the Kubernetes track |
| Review | Pull requests reviewed by Gitar | Covers the Gitar track |
| Demo safety | Cached results for the scripted stacks, and the hero clip saved to disk | Survives bad venue Wi-Fi |

## Data pipeline

**The whole product rests on one index of one-minute transcript chunks, each tagged with the tools it mentions.** Getting that index right is the longest task of the day, so it starts first.

### Ingest (offline, run once)

1. **Pick one conference.** Use the latest KubeCon whose talks are already on the CNCF YouTube channel. Take 40 to 60 talks, weighted toward common tools, rather than all of them. The design scales; the demo does not need to.
2. **Collect talk metadata.** Title, speakers, track and YouTube video ID. Video titles on the channel carry title and speakers; the schedule page carries track and abstract.
3. **Download transcripts.** Use YouTube's automatic captions, for example `yt-dlp --skip-download --write-auto-subs --sub-langs en --sub-format vtt <url>` or the `youtube-transcript-api` Python package. Keep only short quotes in the product and always link back to the video.
4. **Clean the captions.** Automatic captions repeat each line as it scrolls. Remove the repeats and rebuild sentences, keeping each sentence's start time.
5. **Chunk.** Cut windows of about 60 to 90 seconds, snapped to sentence boundaries, with about 15 seconds of overlap.
6. **Tag tools.** Match each chunk against a tool dictionary: names, spellings and aliases ("Argo CD", "ArgoCD", "Argo"). Add the ways captions mangle names, which you will spot while cleaning.
7. **Label each talk once.** Ask the language model to classify a sample of each transcript as deep dive, introduction or product pitch. Copy the label onto every chunk of that talk.
8. **Index into Elastic.** One `clips` index for chunks and one small `talks` index for metadata.

| Field on each clip | Type | Used for |
| --- | --- | --- |
| `talk_id`, `title`, `speakers` | keyword and text | Grouping clips by talk; showing the card |
| `start_sec`, `end_sec` | integer | The timestamp link and the watch-time total |
| `text` | text with a semantic field (Elastic's `semantic_text`, or a `dense_vector` with your own embeddings) | Keyword and meaning search |
| `tools` | keyword array | Matching and boosting by stack |
| `talk_label` | keyword | The deep dive, introduction or pitch label and filter |
| `youtube_id` | keyword | Building `youtube.com/watch?v=ID&t=SECONDS` |

### Query (live, per request)

1. **Turn the stack into tools.** Map package names to tool names with a small hand-written table for the demo tools; ask the model only for names the table does not know.
2. **Search two ways at once.** A keyword query on `text`, boosted where `tools` matches the stack, plus a semantic query built from the team's pain sentence. Combine them with Elastic's reciprocal rank fusion (RRF) retriever.
3. **Spread the results.** Collapse on `talk_id` so one talk cannot fill the playlist, keeping its best two or three chunks.
4. **Build clips.** Merge neighbouring chunks from the same talk into one clip of one to four minutes.
5. **Rerank and explain.** Send the top 30 or so clips to the model with the team's stack. It scores relevance and writes the one-line "why for you." Drop anything under a threshold.
6. **Fill to a budget.** Add clips in score order until the playlist reaches about 20 to 25 minutes.
7. **Write the memo on request.** The model sees only the selected clips and their links, and must cite a clip for every takeaway. That keeps it from inventing advice.

## Decisions

**Every choice the agent would otherwise guess is made here.** Change a default by editing its row, then set it to Decided. A row marked You choose blocks the tasks that need it.

| Decision | Choice | Status |
| --- | --- | --- |
| Language for ingest and API | Python 3.12, managed with `uv`; lint with `ruff`, test with `pytest` | Default |
| API framework | FastAPI | Default |
| Frontend | React, Vite, TypeScript and Tailwind, with npm | Default |
| Elastic deployment | Elastic Cloud **serverless** project (Elasticsearch 9.7), reached through `ELASTIC_URL` and `ELASTIC_API_KEY`. Its Elastic Inference Service offers ELSER (`.elser-2-elastic`), Jina embeddings and Jina rerankers | Decided |
| Semantic search | A `semantic_text` field using the deployment's default inference endpoint | Default |
| Language model provider and models | One fast model for reranking, one stronger model for the memo, both behind a single `llm.complete()` function; provider switchable by `LLM_PROVIDER` (Claude now; OpenAI credits may come later). Exact models not yet picked | You choose (models) |
| Conference and talk list | The latest KubeCon fully posted on the CNCF YouTube channel; 40 to 60 talks listed in `data/talks.csv` | Decided (list in progress) |
| Demo dependency file | A hand-made `package.json` / `go.mod` aligned with the chosen talks' tools, saved in `tests/fixtures/` | Decided |
| Tracing | OpenTelemetry Python SDK with the OTLP exporter, pointed at Dash0; endpoint and token from env vars | Default |
| Deployment format | Radar starter layout: Kustomize in `deploy/`, synced by Argo CD from `main`; one image for API + web | Decided |
| Cluster access through Radar | The hackathon's vcluster via join.cnh.radarhq.io, operated through the `radar-cloud` MCP | Decided (team name pending) |
| Code host and review | GitHub, one pull request per task, reviewed by Gitar | Decided |
| Demo cache | JSON files in `api/cache/`, keyed by a hash of the request | Default |

## Repo layout and conventions

**One repo, three parts (ingest, API, web), and every command behind `make`.**

```text
debrief/
  README.md            how to run, every env var explained
  Makefile             the commands below
  .env.example         every env var, no values
  data/
    talks.csv          youtube_id,title,speakers,track
    tools.yaml         the tool dictionary
    raw/               downloaded caption files (gitignored)
    chunks.jsonl       cleaned chunks, one JSON object per line (gitignored)
  ingest/
    download.py        talks.csv -> data/raw/*.vtt
    clean.py           vtt -> sentences with start times
    chunk.py           sentences -> chunks
    tag.py             chunks -> tool tags, from tools.yaml
    label.py           one label per talk, from the model
    index.py           create indexes, bulk load chunks
  api/
    main.py            FastAPI app and routes
    stack.py           dependency file -> tool ids
    search.py          Elastic query, grouping, clip building
    rerank.py          model scoring and why lines
    memo.py            memo from selected clips
    llm.py             the only file that talks to the model provider
    telemetry.py       OpenTelemetry setup
    cache/             demo cache (task 13)
  web/                 Vite, React, TypeScript, Tailwind
  deploy/              Kustomize: Deployment + Service `app` (API and web in one image), the ingest Job
  tests/
    fixtures/          sample dependency files and caption files
    golden_stacks.yaml the acceptance stacks
```

| Command | Does |
| --- | --- |
| `make ingest` | Download, clean, chunk and tag; writes `data/chunks.jsonl` |
| `make index` | Creates the Elastic indexes and loads the chunks |
| `make api` | Runs the API locally on port 8000 |
| `make web` | Runs the web app locally on port 5173 |
| `make test` | Lint and unit tests for Python and web |
| `make golden` | Runs every golden stack against the running API and prints pass or fail per check |

| Env var | Used by |
| --- | --- |
| `ELASTIC_URL`, `ELASTIC_API_KEY` | ingest, API |
| `LLM_PROVIDER`, `LLM_API_KEY`, `LLM_RERANK_MODEL`, `LLM_MEMO_MODEL` | ingest (labels), API |
| `OTEL_EXPORTER_OTLP_ENDPOINT`, `OTEL_EXPORTER_OTLP_HEADERS` | ingest, API |
| `DEBRIEF_CACHE` | API: `on` serves cached answers for the golden stacks |

Conventions:

- Config comes only from env vars. Nothing secret is committed; `.env` and `data/raw/` are gitignored.
- Type hints on all Python. Pin every dependency version.
- One pull request per task, titled with the task number, so each review stays small.
- Every module that calls Elastic or the model can run against a fake, so unit tests need no network.

## Contracts

**These shapes are fixed; the agent builds to them and does not redesign them.** If one turns out to be wrong, change it here first, then in code.

### Tool dictionary: `data/tools.yaml`

```yaml
- id: argo-cd                 # stable, lowercase, kebab-case
  name: Argo CD
  aliases: ["argo cd", "argocd"]   # whole words, case-insensitive
  caption_spellings: []       # misheard forms found while cleaning
  packages:
    go: ["github.com/argoproj/argo-cd"]
    npm: []
    pypi: []
    helm: ["argo-cd"]
- id: kubernetes
  name: Kubernetes
  aliases: ["kubernetes", "k8s"]
  exclude_from_matching: true  # said in nearly every talk, so it matches nothing useful
```

Rules: avoid aliases that also name another tool (a bare "argo" also means Argo Workflows). Start with about 30 tools common at KubeCon.

### Chunk record: one line of `data/chunks.jsonl`, and one document in the `clips` index

```json
{
  "chunk_id": "abc123-872",
  "talk_id": "abc123",
  "youtube_id": "abc123",
  "title": "Talk title",
  "speakers": ["Speaker Name"],
  "track": "Track name",
  "start_sec": 872,
  "end_sec": 951,
  "text": "Cleaned transcript text for this window.",
  "tools": ["argo-cd"],
  "talk_label": "deep_dive"
}
```

`talk_label` is one of `deep_dive`, `intro` or `pitch`. `chunk_id` is the talk id plus the start second.

### Elastic mapping for the `clips` index

```json
{
  "mappings": {
    "properties": {
      "chunk_id":      { "type": "keyword" },
      "talk_id":       { "type": "keyword" },
      "youtube_id":    { "type": "keyword" },
      "title":         { "type": "text" },
      "speakers":      { "type": "keyword" },
      "track":         { "type": "keyword" },
      "start_sec":     { "type": "integer" },
      "end_sec":       { "type": "integer" },
      "text":          { "type": "text", "copy_to": "text_semantic" },
      "text_semantic": { "type": "semantic_text" },
      "tools":         { "type": "keyword" },
      "talk_label":    { "type": "keyword" }
    }
  }
}
```

If the deployed Elastic version refuses `copy_to` into a `semantic_text` field, write the text into both fields at index time instead.

The `talks` index holds one document per talk, with `talk_id`, `youtube_id`, `title`, `speakers`, `track` and `talk_label`, all as in the chunk record.

### Search: a starting point

```json
{
  "size": 100,
  "retriever": {
    "rrf": {
      "rank_window_size": 100,
      "retrievers": [
        { "standard": { "query": { "bool": { "should": [
          { "terms": { "tools": ["argo-cd", "prometheus"], "boost": 2 } },
          { "match": { "text": "argo cd argocd prometheus" } }
        ] } } } },
        { "standard": { "query": { "semantic": {
          "field": "text_semantic",
          "query": "our Argo CD syncs are slow"
        } } } }
      ]
    }
  }
}
```

Check this against the docs for the deployed version. Group the 100 hits by `talk_id` in Python, keeping at most three per talk, rather than relying on Elastic's collapse alongside RRF. When there is no pain sentence, send the tool names to the semantic half instead.

### Clip building

- Merge chunks from the same talk that overlap or sit within 20 seconds of each other.
- A clip runs 60 to 240 seconds and starts at a sentence start, two seconds early.
- `quote` is two or three sentences from the best chunk, at most 300 characters.
- `watch_url` is `https://www.youtube.com/watch?v={youtube_id}&t={start_sec}s`.

### API

| Route | Request | Response |
| --- | --- | --- |
| `GET /api/tools` | none | `[{"id", "name", "chunk_count"}]`, only tools with 5 or more chunks |
| `POST /api/stack` | `{"tools": [ids], "dependency_file": {"name", "content"}}`, either field optional | `{"tools": [{"id", "name", "source"}], "unknown_packages": [strings]}` |
| `POST /api/playlist` | `{"tools", "pain", "attended_talk_ids", "filter": "all" or "attended" or "missed", "budget_minutes": 25, "rerank": true}` | `{"total_seconds", "clips": [clip], "trace_id"}` |
| `POST /api/memo` | `{"tools", "clip_ids"}` | `{"markdown", "cited_clip_ids"}` |
| `GET /healthz` | none | `{"ok", "elastic"}` |

A `clip` is `{"clip_id", "talk_id", "title", "speakers", "start_sec", "end_sec", "watch_url", "quote", "why", "matched_tools", "talk_label", "score"}`. The memo route loads clip text from the index by id and never trusts text sent by the browser.

One more route feeds the attended-talk picker: `GET /api/talks` returns `[{"talk_id", "title", "speakers"}]` from the `talks` index.

## Tasks

**Fourteen tasks in order; each is one pull request, and each is done only when its check passes.** "Needs" names the decision that must be settled first.

| # | Task | Needs | Check | Status |
| --- | --- | --- | --- | --- |
| 0 | Repo skeleton: the layout, `Makefile`, `.env.example`, README, and lint and test setup for Python and web | Nothing | `make test` passes on the empty project | To do |
| 1 | Placeholder deploy: a hello-world API and web page on the cluster, behind the Ingress | Cluster access through Radar | `/healthz` through the Ingress URL returns `{"ok": true}` | To do |
| 2 | Talk list and download: fill `talks.csv`; `download.py` fetches English automatic captions for each row | Conference and talk list | One caption file per row; the script lists any talk it could not fetch | To do |
| 3 | Clean and chunk: remove rolling-caption repeats, keep sentence start times, cut 60 to 90 second chunks with about 15 seconds of overlap | Nothing | Unit tests on a fixture caption file: no repeated lines, every chunk 45 to 100 seconds, start times rising within a talk | To do |
| 4 | Tool dictionary and tagging: about 30 tools in `tools.yaml`; `tag.py` fills `tools` on every chunk | Nothing | Prints chunks per tool; at least 15 tools have 5 or more chunks | To do |
| 5 | Index: create `clips` and `talks` with the mapping, then bulk load | Elastic deployment | Elastic's document count equals the line count of `chunks.jsonl`; a semantic query for "slow sync" returns hits | To do |
| 6 | Search and clips: `/api/playlist` with `rerank: false`, grouping by talk, clip building, the time budget and watch links | Nothing | The structural checks under Acceptance pass for every golden stack | To do |
| 7 | Stack parsing: `/api/stack` for `go.mod`, `package.json`, `requirements.txt` and `Chart.yaml` | Demo dependency file | Unit tests on fixture files; unknown packages are listed, never dropped silently | To do |
| 8 | Rerank and why line: the fast model scores the top 30 clips and writes a why line of at most 20 words that names a matched tool | Language model | Every why line names a tool in `matched_tools`; if the model call fails, the API returns Elastic's order, not an error | To do |
| 9 | Memo: `/api/memo` writes takeaways, action items and a watch-together list, each citing a clip | Language model | With a fake model that cites an unknown clip id, that line is dropped; every takeaway links to a requested clip | To do |
| 10 | Web UI: tool chips and file paste, the playlist with cards and filter, the memo view with copy | Nothing | `npm run build` passes; the whole flow works by keyboard alone, and every control has a label | To do |
| 11 | Telemetry: spans for stack parsing, search, rerank and memo, carrying tool ids, clip counts, scores and token counts | Nothing | One request shows as one trace with at least four spans in Dash0 | To do |
| 12 | Talk labels: `label.py` sets one label per talk | Language model | Every talk has a label; the script prints the list for a person to check | To do |
| 13 | Demo cache: with `DEBRIEF_CACHE=on`, golden stacks are answered from `api/cache/` | Nothing | With the network off, `make golden` still passes | To do |

## Acceptance

**`make golden` runs these stacks against the live API; the build is acceptable when every structural check passes and a person agrees with the top clips.** Swap tool ids for ones the index covers well, using the output of task 4.

```yaml
# tests/golden_stacks.yaml
- name: gitops
  tools: [argo-cd, prometheus]
  pain: "our Argo CD syncs are slow"
- name: observability
  tools: [opentelemetry, prometheus]
- name: mesh
  tools: [istio, envoy]
- name: from-file
  dependency_file: tests/fixtures/go.mod
- name: missed-only
  tools: [argo-cd]
  attended_talk_ids: [FILL_IN_ONE_TALK_ID]
  filter: missed
- name: no-coverage
  tools: [a-tool-with-no-chunks]
  expect_empty: true
```

### Structural checks, run by `make golden`

- 6 to 15 clips, from at least 4 different talks, with no talk contributing more than 3.
- `total_seconds` between 900 and 1,800 (15 to 30 minutes).
- Every clip: `start_sec` below `end_sec`, a length of 60 to 240 seconds, and a `watch_url` ending in `&t={start_sec}s`.
- At least 80% of clips have a non-empty `matched_tools`.
- With `filter: missed`, no clip comes from a talk in `attended_talk_ids`.
- A stack whose tools appear in no chunk returns zero clips and a message, never weak matches.
- With `DEBRIEF_CACHE=on` and the network off, every stack still passes.
- Targets, not hard limits: under 2 seconds per playlist without rerank, under 10 seconds with it.

### Relevance check, done by a person

For two golden stacks, a teammate watches the top five clips and marks each one relevant or not. Aim for four out of five. This is the only check that tells you the product works; the structural checks only tell you it runs.

## Do not do

**These limits hold even when they look like they would help.**

- Do not start work before the limit set in "Before the start".
- Do not build reach ideas, a chat box, user accounts, or support for more than one conference.
- Do not choose anything marked You choose. Ask.
- Do not download video files. Captions only; people decide how the demo clip is kept offline.
- Do not let the model write anything that is not grounded in clip text from the index. Why lines and memo lines must cite a clip, and uncited lines are dropped.
- Do not commit secrets, `.env` or downloaded data.
- Do not put full transcript text or prompts on spans or in logs. Ids, counts, scores and token totals are enough.
- Do not add a dependency without pinning its version and running `pip-audit` or `npm audit`.
- Do not move on from a failing check. Two attempts at a fix, then stop and report.
