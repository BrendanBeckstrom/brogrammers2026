# How Debrief uses each sponsor track

Debrief turns 54 recorded KubeCon EU 2026 talks into a short, cited briefing for one engineering team: stack in, clip playlist out, memo on request. **Elastic is the only data store and does the ranking. Gitar reviewed every pull request and pushed 20 commits to `main`. Dash0 and Agent0 found a real 12.6 s latency problem, and the fix took it to 3.2 s. A Radar AI investigation led to a startup-probe fix, a follow-up question ruled out the cluster, and Radar Checks for our namespace are now at 0 findings after two rounds of fixes.** The app is live at <https://brogrammers.cnh.radarhq.io>, with the deployed commit in the page footer (`GET /api/version`).

This doc is for three readers: the team (are we using each tool fully?), the presenter (what to say and show), and the judges (where the evidence is). Every claim links to a file, PR, commit, trace id or screenshot. "Per team notes" marks claims we could not re-check from the repo, the live app or Radar's API while writing this.

Links: repo files are relative. PRs are `#N` → `https://github.com/BrendanBeckstrom/brogrammers2026/pull/N`. Screenshots are in the team's Google Doc "Hackathon plans" (names in [Screenshot key](#screenshot-key)).

## At a glance

| Track | What the judges look for (short) | Our strongest evidence | Links |
|---|---|---|---|
| **Elastic**: Find the Right Talk | Elastic as the core data layer; smart keyword, semantic or hybrid search | RRF hybrid search plus Jina rerank *inside* Elastic. Every clip says which half found it. Coverage aggregations give an honest "no talk mentions X" | [`api/search.py`](../api/search.py), [#9](https://github.com/BrendanBeckstrom/brogrammers2026/pull/9), [#12](https://github.com/BrendanBeckstrom/brogrammers2026/pull/12) |
| **Sonar**: Never Merge Broken (Gitar) | Gitar part of how we built; project-aware rules; CI diagnosis and auto-apply on real problems | Gitar reviewed every PR from #1 to #20 and pushed fixes to 15 of them: 20 Gitar-authored commits on `main`. A real CI break on PR #1 was fixed by Gitar. Rules are tied to product promises | [`.gitar/`](../.gitar/review/conventions.md), [#1](https://github.com/BrendanBeckstrom/brogrammers2026/pull/1), [`f22fcc9`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/f22fcc9) |
| **Dash0**: Stop Flying Blind | Traces, logs, metrics, RUM; Agent0 or MCP fixing something real; observability in the product; dashboard or SLO bonus | Trace `a9172d99…` (12.6 s) → Agent0 → [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) → trace `901fc63e…` (3.2 s). Metrics that measure the product's promise | [`api/telemetry.py`](../api/telemetry.py), [#13](https://github.com/BrendanBeckstrom/brogrammers2026/pull/13), [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) |
| **Radar**: AI SRE | Runs on Kubernetes with Radar; UI and MCP throughout; AI investigation of a real issue; what we did with it | Investigation of Deployment `app` → [#14](https://github.com/BrendanBeckstrom/brogrammers2026/pull/14). Checks 3 + 2 findings → [#18](https://github.com/BrendanBeckstrom/brogrammers2026/pull/18), [#19](https://github.com/BrendanBeckstrom/brogrammers2026/pull/19) → 0 | [`deploy/`](../deploy/deployment.yaml), [`argocd/application.yaml`](../argocd/application.yaml) |
| **Merge Forward** | "makes conferences more inclusive and accessible" | Read view (captions) for people who can't play audio; WCAG 2.2 AA checks in CI and in Gitar; vendor pitches labelled from the transcript | [`ClipCard.tsx`](../web/src/components/ClipCard.tsx), [`accessible-ui.md`](../.gitar/rules/accessible-ui.md) |
| **Best overall** | No separate criteria ("Every project is in the running for best overall") | Real data, live URL, a cited memo, an offline demo cache, and one incident traced through three sponsor tools | [Cross-track story](#one-real-incident-through-three-tools) |

## One real incident through three tools

This is the strongest single story for the video. Nothing in it was staged.

| Step | Tool | What happened | Evidence |
|---|---|---|---|
| 1 | Radar MCP | `get_workload_logs` on Deployment `app` showed `APITimeoutError duration_ms=10858.4` from the model call, on a playlist that took 12.9 s | [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) body; the tool name is per team notes |
| 2 | Dash0 | Trace `a9172d99794ae9239dd69ed65a29af54`: `POST /api/playlist` with rerank took **12.6 s**. `rerank.model` 11.5 s → `llm.complete` → Anthropic `POST /v1/messages` 8.2 s, after a 3.3 s gap before the HTTP call | Screenshot S1, [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) |
| 3 | Dash0 Agent0 | We sent 29 clips (3,573 input tokens) and got back 1,970 output tokens, 341 of them reasoning tokens | Screenshot S2, [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) |
| 4 | Radar AI investigation | Follow-up question ruled out the cluster: CPU 0.3% of limit, 0 throttling, memory 22%, 0 packet drops. It pointed at the external LLM call, which matched what Dash0 showed | Screenshot S4 (follow-up answer), per team notes |
| 5 | Gitar | Fix PR [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) (rated "High risk"): at most 18 clips, 3 parallel batches of 6, thinking off, tight `max_tokens`, client built once at startup. Gitar's review found that a shared 12-worker pool let slow calls starve other requests' batches, and it pushed [`b017b70`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/b017b70) (48 workers) | [`api/rerank.py#L44-L67`](../api/rerank.py#L44-L67), [`api/llm.py#L206-L208`](../api/llm.py#L206-L208) |
| 6 | Dash0 | After: trace `901fc63e54fe701cc493276cf6c4eedb`, **3.2 s**. PR #15 measured 3.2–4.0 s typical, and model-written "why" lines went from 0/15 to 9/9 | After trace: screenshot S1, per team notes. Numbers: [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) body |
| 7 | Live re-check | While writing this doc: a new stack with rerank took **3.0 s** end to end (Elastic rerank 1.07 s, 3 model batches in parallel at about 1.8 s). Both rerank steps returned `ok`, and 11 of 12 clips got a model "why" line | Trace `72a68cc2540adf619b3a49b2975096f7` (`X-Trace-Id` header) |

**Numbers to say:** 12.6 s → 3.2 s (about 4×), 0/15 → 9/9 model "why" lines, and the cluster was cleared in one Radar follow-up question.

### Suggested video order (about 3 minutes)

The plan says: product demo first, then one quick proof per track. The hackathon pages we read don't state a video length, so scale this to the submission form's limit.

| Time | Segment | Screen |
|---|---|---|
| 0:00–0:20 | The problem: hundreds of hours of talks, a team that stayed home | Landing page or README headline |
| 0:20–1:00 | Paste a real `go.mod`, get the playlist, click the hero clip, generate the memo | Live app (cache on as fallback) |
| 1:00–1:25 | Elastic: keyword / semantic / both tags, then "Under the hood" | Live app |
| 1:25–2:00 | The incident: S1 slow trace → S2 Agent0 → S4 Radar follow-up → #15 → S1 after trace | Screenshots |
| 2:00–2:20 | Gitar: PR list, S6, `risk.md` | GitHub |
| 2:20–2:40 | Radar: S3 investigation → #14; Checks before (S5) and after | Radar Cloud |
| 2:40–3:00 | Merge Forward: keyboard-only tab-through, Read view, a "pitch" label | Live app |

## Elastic: Find the Right Talk

**What the judges look for** (from the [hackathon page](https://www.wemakedevs.org/hackathons/cnh)):
- "Elasticsearch is the core data layer of your app, not a side feature."
- "Smart use of search, whether that's keyword, semantic, vector or hybrid."
- "How well your project makes conferences easier."

**How we use it**

| Feature | Criterion | Why it's not the obvious use | Evidence |
|---|---|---|---|
| Two indexes: `clips` (1,270 one-minute chunks) and `talks` (54 docs, also checked live via `/api/talks`). Serverless 9.7, per team notes | Core data layer | Elastic is the only database. Every API read hits it: tools, talks, playlist, clip text, memo | [`ingest/index.py`](../ingest/index.py), [#7](https://github.com/BrendanBeckstrom/brogrammers2026/pull/7), counts in [#16](https://github.com/BrendanBeckstrom/brogrammers2026/pull/16) |
| `semantic_text` filled by `copy_to` from `text`, using the project's default embedding endpoint (`.jina-embeddings-v5-text-small`, per team notes) | Smart search | Embeddings are made at index time inside Elastic. We have no embedding code. A fallback writes the field directly if `copy_to` is refused | [`ingest/index.py#L37-L56`](../ingest/index.py#L37-L56) |
| **Hybrid RRF retriever.** Keyword half: a `terms` query on `tools` with boost 2, plus `match_phrase` on tool names, aliases and known caption misspellings (`"argo city"`, `"carpenter node pool"`). Semantic half: the team's "what hurts" sentence | Smart search | Each half does a different job. Keyword catches exact tools even when the captions mangle them; semantic catches the pain ("our deploys drift") when no tool is named | [`api/search.py#L167-L223`](../api/search.py#L167-L223), [`data/tools.yaml`](../data/tools.yaml), [#9](https://github.com/BrendanBeckstrom/brogrammers2026/pull/9) |
| **`matched_by` provenance** (keyword / semantic / both) on every clip card | Smart search | The two halves also run on their own as `_id`-only queries, in parallel with the RRF request, so the UI can show which half found each clip | [`api/search.py#L225-L249`](../api/search.py#L225-L249), [`#L424-L435`](../api/search.py#L424-L435) |
| The highlighter picks the quote on each card | Smart search | Elastic chooses the sentence and the matched words, not Python string matching | [`api/search.py#L192-L223`](../api/search.py#L192-L223) |
| **Per-tool aggregation for an honest "no coverage"** | Conferences easier | If no chunk mentions any of the requested tools, the API says "No talk mentions X" and returns no clips, instead of showing weak matches | [`api/search.py#L409-L414`](../api/search.py#L409-L414) |
| `terms` aggregation powers `/api/tools` | Core data layer | The tool chips only offer tools with real coverage: 30 of the 40 tools in the dictionary today (checked live) | [`api/search.py#L547`](../api/search.py#L547) |
| **Rerank inside Elastic:** `text_similarity_reranker` with `.jina-reranker-v3` over the top 50 RRF hits | Smart search | Ranking stays in the search engine. The LLM only writes the "why" line. Top 50 takes about 1.1 s (100 took about 2.8 s, so we chose 50); it measured 1.07 s today. After a 3 s timeout, we keep the RRF order | [`api/search.py#L251-L266`](../api/search.py#L251-L266), [#12](https://github.com/BrendanBeckstrom/brogrammers2026/pull/12) |
| **Labels by bulk partial updates** | Core data layer | `talk_label` (deep dive / intro / pitch) is set in place on all 1,270 chunks. No reindex, so the stored embeddings are kept | [`ingest/label.py#L423-L440`](../ingest/label.py#L423-L440), [#16](https://github.com/BrendanBeckstrom/brogrammers2026/pull/16) |
| `mget` serves the memo's clip text | Core data layer | The memo route loads clip text from the index by id and never trusts text sent by the browser (a Gitar convention) | [`api/search.py#L590-L620`](../api/search.py#L590-L620), [`api/routes_memo.py`](../api/routes_memo.py) |

**Depth check: Strong.** Elastic stores and ranks everything, and both hybrid halves are visible in the UI. The Elastic-hosted LLM completion endpoints returned "403 Forbidden" on this project ([#12](https://github.com/BrendanBeckstrom/brogrammers2026/pull/12)), so generation uses Anthropic. Be ready to say that in one line.

**Remaining quick wins**

| Item | Effort | Value to Elastic judges | Status |
|---|---|---|---|
| `significant_text` "what this conference said about your stack" chips | 20–30 min | High: uses search for discovery, not only retrieval | ⏳ in progress (another agent) |
| `playlists` index for shareable links | 20 min | Medium: a second write path in Elastic | ⏳ in progress |
| `queries` index plus `/api/insights`: tools teams searched for that the conference didn't cover | 25 min | High: an organizer insight built on aggregations | ⏳ in progress |
| Caption-corrected transcripts in the Read view (from `caption_spellings`) | 15 min | Medium (also Merge Forward) | ⏳ in progress |
| Rehearse opening "Under the hood" to show the real RRF + reranker JSON | 2 min | High: the hybrid design is visible in 5 seconds | Ready now |

**Video talking points** (20–30 s). *Show: a playlist for Argo CD + KEDA with a "drift" sentence; the keyword / semantic / both tags; then open "Under the hood".*
> "Everything Debrief knows lives in Elasticsearch. One RRF query runs two halves: keyword on the tools you run, including how auto-captions misspell them, and semantic on what hurts. Each card says which half found it. Then Elastic's own Jina reranker reorders the top 50 inside the same request, and if a tool was never covered, Elastic's aggregations let us say so honestly."

## Sonar: Never Merge Broken, with Gitar

**What the judges look for:**
- "Gitar is part of how you built, with features landing through PRs it reviewed, not one PR at the end."
- "Project-aware setup, with review instructions and repository rules that reflect how your team actually works."
- "Using CI failure diagnosis and auto-apply to fix real problems."
- "How well your project makes conferences easier."

**How we use it**

| Feature | Criterion | Why it's not the obvious use | Evidence |
|---|---|---|---|
| 21 small PRs, one per task, from the first commit ([#1](https://github.com/BrendanBeckstrom/brogrammers2026/pull/1)) on. Gitar posted a review summary on every one of #1–#20, and every one has an `gitar auto-apply:on` comment | Part of how we built | `main` is protected by ruleset `protect-main` (active): PR required, no force-push or delete, required checks `python` and `web`, no bypass actors. "Never merge broken" is enforced literally | [Ruleset](https://github.com/BrendanBeckstrom/brogrammers2026/rules/26878699), [`ci.yaml`](../.github/workflows/ci.yaml), [PR list](https://github.com/BrendanBeckstrom/brogrammers2026/pulls?q=is%3Apr) |
| Review conventions tied to `docs/BRIEF.md` contracts | Project-aware | Gitar flags code that drifts from our API and index contracts unless the spec changes first | [`.gitar/review/conventions.md`](../.gitar/review/conventions.md) |
| **Rules tied to product promises:** `search-golden-stacks`, `route-span-and-test`, `accessible-ui`, `task-pr-title` | Project-aware | Each rule protects something the product promises: relevant clips (golden stacks), explainable requests (span + test per route), accessibility, one task per PR. The citation rule ("every why line and memo line cites a requested clip") is in the conventions | [`.gitar/rules/`](../.gitar/rules/search-golden-stacks.md), [#4](https://github.com/BrendanBeckstrom/brogrammers2026/pull/4) |
| **`risk.md` drives "High risk" labels** | Project-aware | Deploy, workflows, `api/llm.py` and the citation check are marked high risk. From PR #8 on, Gitar's risk line quotes "Repository policy…". 13 of 20 PRs were rated 🔴 High, including the deploy PRs ([#10](https://github.com/BrendanBeckstrom/brogrammers2026/pull/10), [#14](https://github.com/BrendanBeckstrom/brogrammers2026/pull/14), [#18](https://github.com/BrendanBeckstrom/brogrammers2026/pull/18), [#19](https://github.com/BrendanBeckstrom/brogrammers2026/pull/19)) and the LLM PRs ([#12](https://github.com/BrendanBeckstrom/brogrammers2026/pull/12), [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15)) | [`.gitar/config/risk.md`](../.gitar/config/risk.md) |
| **`apply.md` limits auto-apply** | Project-aware | Gitar may auto-fix lint, types, tests, pins and leaked secrets, but only comments on contracts, tool aliases and `deploy/` manifests, so a person decides | [`.gitar/config/apply.md`](../.gitar/config/apply.md) |
| **Real CI failure, auto-fixed:** the `python` job failed with "Unable to resolve action `astral-sh/setup-uv@v10`". Gitar pushed the fix, and it fixed the same break on its own in #2 and #3, pinning `v10.3.3` (what `main` uses) | CI diagnosis | It came up on its own; we didn't stage it. The diagnosis text is in screenshot S6 (per team notes). The PR thread shows only the fix commit | [#1](https://github.com/BrendanBeckstrom/brogrammers2026/pull/1), [`f22fcc9`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/f22fcc9), [`4e4b516`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/4e4b516), [`75c91b3`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/75c91b3) |
| `gitar auto-apply:on` on every PR → **20 Gitar-authored commits on `main`** (`git log origin/main --author=gitar --no-merges`) | Auto-apply | They are real bugs, not formatting (table below) | [Commits by Gitar](https://github.com/BrendanBeckstrom/brogrammers2026/commits/main/?author=gitar) |
| Late fixes not lost: PR #20 cherry-picks two Gitar fixes that landed on #16's and #17's branches after those PRs merged. Gitar then requested changes on #20 itself (Elastic with the network off returned 502, not a cache-miss 503), and we fixed that in [#21](https://github.com/BrendanBeckstrom/brogrammers2026/pull/21) | Auto-apply | We act on Gitar's findings even after merge | [#20](https://github.com/BrendanBeckstrom/brogrammers2026/pull/20), [`c5d25a4`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/c5d25a4), [`857e407`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/857e407), [#21](https://github.com/BrendanBeckstrom/brogrammers2026/pull/21) |

**Notable Gitar fixes on `main`**

| Commit | PR | What Gitar caught |
|---|---|---|
| [`f22fcc9`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/f22fcc9) | [#1](https://github.com/BrendanBeckstrom/brogrammers2026/pull/1) | CI used a `setup-uv` tag that doesn't exist; an OTLP headers example broke when the Makefile sourced `.env`; secret ignore patterns had been dropped from `.gitignore` |
| [`3139e33`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/3139e33) | [#2](https://github.com/BrendanBeckstrom/brogrammers2026/pull/2) | Short post-gap caption segments became tiny chunks |
| [`2e4aa81`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/2e4aa81) | [#3](https://github.com/BrendanBeckstrom/brogrammers2026/pull/3) | Caption spellings "carpenter" and "spiffy" are ordinary English words (false tool matches) |
| [`c178ceb`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/c178ceb) | [#5](https://github.com/BrendanBeckstrom/brogrammers2026/pull/5) | A PR that changed only `kustomization.yaml` would never reach the `deploy` branch |
| [`a3b40c4`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/a3b40c4) | [#7](https://github.com/BrendanBeckstrom/brogrammers2026/pull/7) | Re-running the index without `--recreate` left stale clips and failed silently |
| [`ba0ab74`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/ba0ab74) | [#8](https://github.com/BrendanBeckstrom/brogrammers2026/pull/8) | A 422 echoed the whole uploaded file; deeply nested JSON/YAML returned 500 |
| [`1b82731`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/1b82731) | [#10](https://github.com/BrendanBeckstrom/brogrammers2026/pull/10) | Deployment set `LLM_PROVIDER=elastic`, which `main` didn't support yet |
| [`7ed2034`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/7ed2034) | [#12](https://github.com/BrendanBeckstrom/brogrammers2026/pull/12) | Model-written Markdown links and HTML could reach the rendered memo |
| [`343bd90`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/343bd90) | [#14](https://github.com/BrendanBeckstrom/brogrammers2026/pull/14) | The new startup probe still logged "connection refused" warnings, so Gitar added `initialDelaySeconds: 3` |
| [`b017b70`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/b017b70) | [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) | Rerank batches could starve a shared thread pool |
| [`2c17bab`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/2c17bab) | [#4](https://github.com/BrendanBeckstrom/brogrammers2026/pull/4) | The `accessible-ui` rule must cover `.css`, not only `.tsx` |

**Depth check: Strong.** Review, rules and auto-apply are all used on real problems, and the rules are clearly about Debrief, not generic. The weak spot: Gitar's CI *diagnosis* text is only in a screenshot, not in the PR thread. The fix commit is the public evidence.

**Remaining quick wins**

| Item | Effort | Value to Gitar judges | Status |
|---|---|---|---|
| Make sure screenshot S6 shows Gitar's diagnosis text *and* links the fix commit `f22fcc9`; the PR thread alone doesn't show the diagnosis | 2 min | High: it's a named criterion | ✅ saved, check framing |
| Screenshot one "Repository policy… High risk" line (for example #15) next to `risk.md` | 3 min | Medium-high: proves the config drives behaviour | ❌ |
| Reply in-thread to the "Task N:" title-convention comments on #14, #15, #18, #19 (fix PRs use `Fix:`; say so, or add `Fix:` to the rule) | 5–10 min | Medium: shows we read every comment | ❌ |
| Open this docs PR and let Gitar review it | 5 min | Low-medium | ⏳ |

**Video talking points.** *Show: the PR list, then S6 (Gitar's CI diagnosis) with `f22fcc9`, then [`risk.md`](../.gitar/config/risk.md) next to a "High risk" label.*
> "Main is protected, so every PR went through Gitar and green CI. Our rules are about Debrief's promises: search changes must update the golden stacks, every route needs a span and a test, and the UI must stay accessible. Our risk file makes Gitar treat deploy and LLM changes as high risk. In PR #1 our CI broke for real on a GitHub Action tag that doesn't exist, and Gitar pushed the fix. That's one of 20 Gitar commits on main, including a thread-pool starvation bug in our rerank."

## Dash0: Stop Flying Blind

**What the judges look for:**
- "Depth of instrumentation across your app, including traces, logs, metrics and real-user monitoring if you have a frontend."
- "Using Agent0 or the MCP server to find and fix something real during the day, and showing it in your demo."
- "Observability that's part of the product experience, not bolted on at the end."
- "Bonus point if you can create a dashboard or an SLO that shows how healthy your app is."
- "How well your project makes conferences easier."

**How we use it**

| Feature | Criterion | Why it's not the obvious use | Evidence |
|---|---|---|---|
| Traces: one span per step (`stack.parse`, `search.hybrid`, `search.keyword`, `search.semantic`, `search.elastic_rerank`, `search.group`, `search.clips`, `rerank.model`, `rerank.batch`, `llm.complete`, `search.budget`, `api.memo`) on top of FastAPI auto-instrumentation | Depth | Each request reads as a timeline of the pipeline. Spans carry ids, counts, scores and token totals only (a Gitar rule) | [`api/search.py`](../api/search.py), [`api/rerank.py`](../api/rerank.py), [#13](https://github.com/BrendanBeckstrom/brogrammers2026/pull/13) |
| Ingest traces: `ingest.download`, `ingest.index` with a span per bulk batch, `ingest.label` per talk | Depth | The offline pipeline is observable too | [`ingest/index.py`](../ingest/index.py), [`ingest/label.py`](../ingest/label.py) |
| Logs over OTLP, with trace ids: rerank batch fallbacks, Elastic rerank fallbacks, dropped memo lines (ids and reasons, never text) | Depth | Logs link straight to the trace that wrote them | [`api/telemetry.py#L130-L170`](../api/telemetry.py#L130-L170) |
| **Metrics that measure the product's promise:** `debrief.playlist.duration`, `debrief.playlist.clips`, `debrief.playlist.empty` by tool (coverage gaps), `debrief.llm.tokens`, `debrief.memo.citations_dropped` | Part of the product | Empty playlists by tool show which tools the conference never covered. Dropped citations measure whether the memo can be trusted | [`api/telemetry.py#L263-L290`](../api/telemetry.py#L263-L290) |
| **`X-Trace-Id` on every `/api` response**, and `trace_id` in the UI's "Under the hood" panel | Part of the product | Any user-visible result can be found in Dash0 in one paste (checked live today) | [`api/telemetry.py#L235-L257`](../api/telemetry.py#L235-L257), [`Playlist.tsx#L105-L120`](../web/src/components/Playlist.tsx#L105-L120) |
| **Agent0 found and we fixed a real problem** (12.6 s → 3.2 s) | Agent0 / MCP | Agent0 read the token counts off the trace and showed the time went to output and reasoning tokens from an oversized prompt, not to Elastic | [Cross-track story](#one-real-incident-through-three-tools), screenshots S1, S2 |
| Browser RUM via the Dash0 web SDK, configured at runtime from `/api/config` (token in a k8s Secret, never in git). Watch clicks by rank show whether ranking works | RUM, part of the product | Watch-by-rank is a live relevance measure, not a page-load chart | ✅ SDK and event delegation are on main; ⏳ token activation and UI event attributes remain |
| "Debrief health" dashboard and 2 SLOs: playlist p95 latency, and memo citation validity ≥ 99% | Bonus | The citation SLO is about trust, not speed | ✅ Dashboard/SLO definitions are on main; ⏳ import/apply and live verification remain |

**Depth check: Good now, Strong once RUM is enabled and the dashboard/SLOs are imported and verified.** Traces, logs, metrics and a real Agent0 fix are done. The SDK and assets are on main; the remaining gaps are enabling RUM with the runtime token, adding `data-rum-event` markup for product actions, and importing/verifying the dashboard and SLOs. The SDK propagates `traceparent` to `/api/*` once active; the browser token is not enabled and watch events are not wired to UI elements yet.

**Remaining quick wins**

| Item | Effort | Value to Dash0 judges | Status |
|---|---|---|---|
| Import/apply the dashboard and 2 SLOs; verify and screenshot them | 15–20 min | High: the explicit bonus point | ✅ definitions on main; ⏳ import/apply and screenshot remain |
| Enable RUM with the Dash0 web SDK and add "Watch clicked" event attributes by rank | 20–30 min | High: RUM is named in the first criterion | ✅ SDK and event delegation on main; ⏳ token activation and UI event attributes remain |
| Enable RUM and verify one browser trace through playlist search, rerank and memo | 5–10 min | High: it's on the plan's proof checklist | ⏳ token activation and live verification remain |
| Screenshot the `debrief.playlist.empty` by-tool chart (coverage gaps) | 5 min | Medium: observability as product insight | ❌ |

**Video talking points.** *Show: the slow trace (S1), the Agent0 answer (S2), then the after trace.*
> "Every Debrief request is one trace in Dash0, and the trace id is shown in the app. Early on, one playlist took 12.6 seconds; the trace put 11.5 of them in our model rerank. Agent0 read the token counts off the trace: we were sending 29 clips and getting almost 2,000 output tokens back, including reasoning. We cut it to 18 clips in three parallel batches with thinking off, and the same request now takes 3.2 seconds. We also measure the product's promise: coverage gaps by tool, and memo lines dropped for missing citations."

## Radar: AI SRE

**What the judges look for:**
- "Run your project on Kubernetes with Radar connected", "in the cluster and namespace we provide your team, or on your own cluster."
- "Use Radar's UI and MCP throughout building and operating your project."
- "Use Radar's AI investigations to investigate a real issue or question about your running app."
- "Show what you learned and what you did with the findings."
- "Build something that makes conferences easier."

**How we use it**

| Feature | Criterion | Why it's not the obvious use | Evidence |
|---|---|---|---|
| Runs on Radar cluster `brogrammers` (id `1g0G9WZ2sHY`), namespace `team-brogrammers`, at <https://brogrammers.cnh.radarhq.io>. 2 replicas, startup/readiness/liveness probes, non-root, read-only root fs | Runs on Kubernetes | `/api/version` returns the deployed commit, so "what's running" is checkable from the page footer | [`deploy/deployment.yaml`](../deploy/deployment.yaml), [#5](https://github.com/BrendanBeckstrom/brogrammers2026/pull/5), [#10](https://github.com/BrendanBeckstrom/brogrammers2026/pull/10) |
| **GitOps through a separate `deploy` branch.** Merge → Action builds the image → force-pushes `deploy` → Argo CD Application `app` (`targetRevision: deploy`) syncs | Building and operating | The starter commits image tags to `main`. We moved them to `deploy`, so `main` stays protected and the Action never writes to it | [`deploy.yaml`](../.github/workflows/deploy.yaml), [`argocd/application.yaml`](../argocd/application.yaml), [`ab266e3`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/ab266e3) |
| **MCP from the coding agent:** `list_clusters`, `list_resources`, `apply_resource` (the Argo CD Application; dry-runs for the Secret and PDB), `manage_gitops` refresh, `wait_for`, `diagnose`, `get_workload_logs` (surfaced the `APITimeoutError` lines that started the incident), `get_events`, `get_cluster_audit`, `manage_workload` restart | MCP throughout | The agent operated the cluster through Radar, not kubectl ([`AGENTS.md`](../AGENTS.md) tells it to) | In PRs: `get_cluster_audit` ([#18](https://github.com/BrendanBeckstrom/brogrammers2026/pull/18)), `apply_resource` dry-run of the PDB ([#19](https://github.com/BrendanBeckstrom/brogrammers2026/pull/19)), `APITimeoutError` logs ([#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15)). The full list is per team notes. While writing this doc we also used `list_investigations`, `get_investigation` and `get_cluster_audit` |
| Secrets created by a person in Radar's UI, never by the agent | UI | The repo and image are public. The agent only ever sees the Secret's name | [`README.md` Secrets](../README.md#secrets), [`AGENTS.md`](../AGENTS.md) |
| **AI investigation on Deployment `app`:** "healthy now… past probe failures were tied to startup during recent rollouts". Readiness "connection refused" 16 s after a restart → we added a startup probe (2 s × 30) | Investigation of a real issue; what we did | It turned noisy warnings into a concrete manifest fix | Run `chat_Ls_kRKpAfQvsbFpu` ([Radar link](https://app.radarhq.io/c/1g0G9WZ2sHY/investigations/chat_Ls_kRKpAfQvsbFpu?org=org_NCJbssDGEBsU), team-only), [#14](https://github.com/BrendanBeckstrom/brogrammers2026/pull/14), [`deployment.yaml#L96-L105`](../deploy/deployment.yaml#L96-L105), screenshot S3 |
| **Follow-up question to the same investigation**, about slow playlists → CPU 0.3% of limit, 0 throttling, memory 22%, 0 packet drops; points at the external LLM call | Show what you learned | Used to *rule out* infrastructure, which sent us to Dash0 for the real cause | Screenshot S4, per team notes |
| **Radar Checks for `team-brogrammers`:** 3 findings (SA token auto-mount, read-only root fs, single replica) → [#18](https://github.com/BrendanBeckstrom/brogrammers2026/pull/18); 2 multi-replica findings (PDB, topology spread) → [#19](https://github.com/BrendanBeckstrom/brogrammers2026/pull/19) | What we did | We followed the posture checks to zero, not just the incident | Before: screenshot S5. After: `get_cluster_audit` returned **0 findings** for `team-brogrammers` at 13:20 MT. [`deploy/pdb.yaml`](../deploy/pdb.yaml) |

**Depth check: Strong.** A real investigation led to a merged fix, a follow-up ruled out the cluster, and Checks are at 0 findings after two rounds (3 findings, then 2 that appeared once we ran 2 replicas). The gap: the plan asked for an "Operating Debrief" section with an MCP question log in the README, and it isn't there yet.

**Remaining quick wins**

| Item | Effort | Value to Radar judges | Status |
|---|---|---|---|
| Screenshot Radar Checks "after" (0 findings) to pair with S5 | 2 min | High: before/after in one slide | ❌ (state verified, screenshot missing) |
| "Operating Debrief" README section: the deploy flow, the MCP question log (the list above), the investigation link and its outcome | 10 min | High: README is required and judges read it | ❌ |
| Screenshot the topology view of `team-brogrammers` | 2 min | Medium: proof checklist item | ❌ |
| Ask the investigation one more follow-up after PRs #15, #18 and #19 ("is `app` healthy with 2 replicas and the PDB?") for an after state | 5 min | Medium | ❌ |

**Video talking points.** *Show: the investigation answer (S3), the follow-up answer (S4), then Checks before (S5) and after.*
> "Debrief runs on our Radar cluster, deployed by Argo CD from a separate deploy branch so main stays protected. Our coding agent operates it through Radar's MCP. Radar's AI investigation tied our probe failures to startup, with a readiness failure 16 seconds after a restart, so we added a startup probe. When playlists were slow, one follow-up question ruled the cluster out: CPU at 0.3%, no throttling. That sent us to the model call. Then we fixed every Radar Check on our namespace, and it now shows zero findings."

## Merge Forward: Conferences for Everyone

**What the judges look for:** "For the project that makes conferences more inclusive and accessible." No other criteria are listed.

**How we use it**

| Feature | Why it matters | Evidence |
|---|---|---|
| **The conference for people who couldn't be in the room.** Travel cost, childcare and visas keep people home; Debrief gives them the 20 minutes that matter | The product itself is the inclusion story | [`README.md`](../README.md), plan research (framing only) |
| **Read view:** every clip card has a Read button that shows the clip's automatic captions, clearly labelled as machine-generated | For Deaf and hard-of-hearing viewers, slow connections, and anyone who can't play audio | [`ClipCard.tsx#L98-L125`](../web/src/components/ClipCard.tsx#L98-L125) |
| Screen-reader announcements: `aria-live` for playlist results, memo status and caption loading | People using a screen reader know when results arrive | [`App.tsx#L182`](../web/src/App.tsx#L182), [`Memo.tsx#L76`](../web/src/components/Memo.tsx#L76), [`ClipCard.tsx#L115`](../web/src/components/ClipCard.tsx#L115) |
| Labels on every control; selected tool chips use `aria-pressed`, so state isn't shown by colour alone; reduced motion respected | WCAG 2.2 AA basics | [`StackInput.tsx#L176`](../web/src/components/StackInput.tsx#L176), [`index.css#L16`](../web/src/index.css#L16) |
| **Accessibility enforced twice:** oxlint `jsx-a11y` in the required `web` CI check, plus the Gitar `accessible-ui` rule (Gitar extended it to `.css` in [`2c17bab`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/2c17bab)) | It can't silently regress | [`web/.oxlintrc.json`](../web/.oxlintrc.json), [`accessible-ui.md`](../.gitar/rules/accessible-ui.md) |
| Keyboard-only run in headless Chrome at 390 px (phone width), keys only | The whole flow works without a mouse, on a small screen | [#11](https://github.com/BrendanBeckstrom/brogrammers2026/pull/11) body |
| **Vendor pitches are labelled.** All 5 sponsored keynotes came out `pitch`, and they are the only 5 pitches out of 54 talks. The model saw transcript excerpts only, never the title | Viewers with limited time (or bandwidth) can skip marketing | [`data/talk_labels.csv`](../data/talk_labels.csv), [`ingest/label.py#L70-L96`](../ingest/label.py#L70-L96), [#16](https://github.com/BrendanBeckstrom/brogrammers2026/pull/16) |

**Depth check: Good.** The framing is strong and the basics are enforced in CI and in review. It's thin on proof that a person tested it with a real screen reader, and on features beyond the Read view (plain-language notes, translation, low-bandwidth mode).

**Remaining quick wins**

| Item | Effort | Value to Merge Forward judges | Status |
|---|---|---|---|
| A 10-minute VoiceOver pass; write 3–5 findings in the README and fix one | 15 min | High: "we tested it with VoiceOver" beats a list of claims | ❌ |
| Caption-corrected tool names in the Read view | 15 min | Medium | ⏳ in progress |
| Public landing page that leads with the "couldn't be in the room" story | 20 min | Medium | ⏳ in progress |
| A "Hide pitches" filter on talk labels | 10 min | Medium: makes the labels useful to viewers | ❌ |

**Video talking points.** *Show: Tab through the app with focus rings visible, open Read on a clip, then point at a "pitch" label.*
> "Debrief is the conference for people who couldn't be in the room. Every clip has a Read view with the captions, for Deaf and hard-of-hearing viewers or anyone who can't play audio. The whole flow works by keyboard, results are announced to screen readers, and accessibility is checked twice: a lint in CI and a Gitar rule. And we label vendor pitches from the transcript alone: all five sponsored keynotes came out as pitches."

## Best overall

**What the judges look for:** no separate criteria. The FAQ says "Every project is in the running for best overall." The rules require a public repo with a README, AI use disclosed, and a team that can explain the architecture.

| What makes it strong | Evidence |
|---|---|
| A clear problem: hundreds of hours of talks, a team that stayed home | [`README.md`](../README.md) |
| Real data only: 54 real KubeCon EU 2026 talks, real dependency files (`go.mod`, `package.json`, `requirements.txt`, `Chart.yaml`) | `/api/talks` (live), [`api/stack.py`](../api/stack.py), [#8](https://github.com/BrendanBeckstrom/brogrammers2026/pull/8) |
| Every model output is grounded. Claude Haiku 5.5 writes the "why" lines in batches, each checked to name a matched tool. Claude Sonnet 5.5 writes the memo, and every line must cite a requested clip or it is dropped (and counted in Dash0) | [`api/rerank.py`](../api/rerank.py), [`api/memo.py`](../api/memo.py), [`api/llm.py#L46`](../api/llm.py#L46), [#12](https://github.com/BrendanBeckstrom/brogrammers2026/pull/12) |
| Demo safety: `DEBRIEF_CACHE=on` serves the golden stacks offline, and tests check that | [`api/demo_cache.py`](../api/demo_cache.py), [#17](https://github.com/BrendanBeckstrom/brogrammers2026/pull/17) |
| Quality bar: golden stacks, unit tests with fakes (no network), required CI | [`tests/golden_stacks.yaml`](../tests/golden_stacks.yaml), [`ci.yaml`](../.github/workflows/ci.yaml) |
| One incident traced through Radar, Dash0 and Gitar | [Cross-track story](#one-real-incident-through-three-tools) |
| AI use disclosed; agent commits carry `Co-Authored-By` | [`README.md` AI disclosure](../README.md#ai-disclosure) |

**Depth check: Strong** on the product and its evidence. The risk is the demo itself: rehearse the hero clip and keep the cache on as a fallback.

**Video talking points.** *Show: paste a real `go.mod` → playlist → click the hero clip → generate the memo.*
> "Paste your team's go.mod and Debrief finds the 20 minutes of KubeCon that matter to you, with exact timestamps. Click a clip and the speaker says exactly what the card promised. Then one click writes a memo for your manager where every line cites a clip, and any line without a citation is dropped."

## Claims to avoid

| Don't say | Say instead | Why |
|---|---|---|
| "Elastic hosts our LLM" | "Elastic does search and reranking; Anthropic writes the text" | The Elastic-hosted completion endpoints returned 403. `api/llm.py` still defaults to `elastic`, but the deployment sets `LLM_PROVIDER=anthropic` ([`deployment.yaml`](../deploy/deployment.yaml)) |
| "Playlists and queries are stored in Elastic", "shareable links", "/api/insights", "term chips" | Only once those PRs are merged and live | ⏳ in progress, not on `main` |
| "We have RUM / a dashboard / SLOs" | Say they're implemented only after they're visible in Dash0 and screenshotted | SDK and dashboard/SLO definitions are on main; live setup and evidence remain pending |
| "A browser click traces through to the backend" | Say so after an end-to-end browser trace is verified | The Dash0 SDK propagates `traceparent` to `/api/*` once RUM is active; the runtime token is not enabled yet |
| "Gitar auto-fixed 20 bugs" | "20 Gitar-authored commits on main, across 15 PRs" | One is docs (`14b37c4`) and one is a perf change |
| "You can see Gitar's CI diagnosis in PR #1" | Show S6, then the fix commit `f22fcc9` | The PR thread has the fix commit, not the diagnosis text |
| "10/10 why lines after the fix" | "0/15 → 9/9 model why lines" | That's what PR #15's table says. "10/10" appears only in #17's golden output |
| "risk.md made every deploy PR High risk" | "13 of 20 PRs were rated High, and from #8 on Gitar cites our repository policy" | #5 (deploy workflow) was rated Medium, likely reviewed before `risk.md` merged |
| "Radar found the latency bug" | "Radar ruled out the cluster; Dash0 and Agent0 found the cause" | That's what happened |
| "Argo CD deploys from `main`" | "Argo CD syncs the `deploy` branch the Action publishes" | The README's Deploy section still describes the starter's flow |
| "Fully WCAG 2.2 AA compliant" / "tested with VoiceOver" | "Built against WCAG 2.2 AA checks in CI and review; keyboard-tested" | No formal audit; no screen-reader pass recorded yet |
| "Playlists always take 3 seconds" | "The slow trace went from 12.6 s to 3.2 s; today's live check was 3.0 s" | One before and one after trace, plus one live check |
| "The labeller detects all vendor pitches" | "All 5 sponsored keynotes came out as pitch, from the transcript alone" | 54 talks is a small sample |
| Exact Radar follow-up numbers (CPU 0.3%, memory 22%) with no screenshot on screen | Show S4 while saying them | Radar's API returns the run's first saved finding, not the follow-up |

## Proof checklist

Mirrors the plan's checklist. ✅ done with evidence, ⏳ in progress, ❌ not done.

| Track | Item | Status | Where |
|---|---|---|---|
| Elastic | A clip card showing its keyword, semantic or both tag | ✅ | Live app; [`ClipCard.tsx`](../web/src/components/ClipCard.tsx); screenshot ❌ |
| Elastic | The Under the hood panel with the real query | ✅ | Live app; [`Playlist.tsx`](../web/src/components/Playlist.tsx); screenshot ❌ |
| Elastic | The "what this conference said" term chips for one stack | ⏳ | In progress (`significant_text`) |
| Gitar | The PR list: every feature through a reviewed PR | ✅ | [PRs #1–#20](https://github.com/BrendanBeckstrom/brogrammers2026/pulls?q=is%3Apr) |
| Gitar | The review instructions in the repo | ✅ | [`.gitar/`](../.gitar/review/conventions.md) |
| Gitar | One Gitar comment that caught a real problem, and the commit that fixed it | ✅ | [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) → [`b017b70`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/b017b70) |
| Gitar | One CI failure that Gitar diagnosed and fixed with auto-apply | ✅ | [#1](https://github.com/BrendanBeckstrom/brogrammers2026/pull/1) → [`f22fcc9`](https://github.com/BrendanBeckstrom/brogrammers2026/commit/f22fcc9); diagnosis in S6 only |
| Dash0 | One trace from a browser click through search, rerank and memo | ⏳ | Backend trace ✅ (`901fc63e…`, S1; `72a68cc2…` today); browser RUM/propagation code is on main, but token activation and live end-to-end verification remain |
| Dash0 | The Debrief health dashboard and both SLOs | ⏳ | Dashboard/SLO definitions are on main; import/apply and live verification/screenshot remain |
| Dash0 | Watch clicks by rank from real-user monitoring | ⏳ | RUM SDK and event delegation are on main; token activation, UI event attributes and live event verification remain |
| Dash0 | An Agent0 or MCP question, its finding, and the fix PR | ✅ | S2 → [#15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) |
| Radar | The topology view of the namespace with every workload | ❌ | Screenshot needed |
| Radar | The MCP question log in the README | ❌ | Add an "Operating Debrief" section |
| Radar | The AI investigation: what it found, the fix PR, the state after the fix | ✅ | S3, S4 → [#14](https://github.com/BrendanBeckstrom/brogrammers2026/pull/14); Checks 0 after [#18](https://github.com/BrendanBeckstrom/brogrammers2026/pull/18), [#19](https://github.com/BrendanBeckstrom/brogrammers2026/pull/19) |
| Merge Forward | One clip's Read view and plain-language note | ⏳ | Read view ✅; plain-language note ❌ |
| Merge Forward | Notes from the keyboard-only and screen-reader pass | ⏳ | Keyboard ✅ ([#11](https://github.com/BrendanBeckstrom/brogrammers2026/pull/11), headless Chrome); screen reader ❌ |
| Submission | Public repo with a README: setup steps, AI-use disclosure, the Operating Debrief section | ⏳ | Setup ✅, AI disclosure ✅, Operating Debrief ❌ |
| Submission | Submitted by 17:15, leaving 15 minutes before the 17:30 close | ❌ | — |

## Screenshot key

All in the team's Google Doc "Hackathon plans" (per team notes).

| Id | Screenshot | Used in |
|---|---|---|
| S1 | Dash0: slow trace `a9172d99…` (12.6 s) and after trace `901fc63e…` (3.2 s). The after-trace id appears only here, not in a PR | Dash0, cross-track |
| S2 | Agent0's analysis (29 clips, 3,573 in / 1,970 out / 341 reasoning tokens) | Dash0, cross-track |
| S3 | Radar AI investigation findings on Deployment `app` | Radar |
| S4 | Radar investigation follow-up answer (cluster ruled out) | Radar, cross-track |
| S5 | Radar Checks before (3 findings) | Radar |
| S6 | Gitar's CI diagnosis on PR #1 | Gitar |
