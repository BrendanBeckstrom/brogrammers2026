# Debrief — notes for coding agents

Debrief turns a conference's recorded talks into a short, cited briefing for one engineering team: stack in, clip playlist out, memo on request. Built at the WeMakeDevs Cloud Native Hackathon (Salt Lake City).

**Deadline: submissions close 5:30 PM MT, Mon Nov 9, 2026.** Speed matters more than polish. Never cut: real transcripts, timestamp links, the rehearsed hero clip.

## Source of truth

- `docs/BRIEF.md` is the spec: contracts (tools.yaml, chunk record, Elastic mapping, API shapes), the 14 tasks with their checks, acceptance, and "Do not do". Its **Overrides** section wins over the rest of it.
- The contracts are fixed. If one is wrong, change `docs/BRIEF.md` first, then the code.
- Anything marked "You choose" in the brief is a human decision: stop and ask.

## Work fast: use subagents

We are racing a deadline. **Use subagents whenever work can run in parallel**, and keep the main thread for coordination:

- Independent tasks (e.g. Task 3 clean/chunk and Task 4 tools/tagging) go to parallel subagents, each on its own branch or worktree, each opening its own PR.
- Delegate broad reads (docs, other repos, long files, web pages) to a subagent and keep only its conclusion.
- Give each subagent a tight brief: the task number, the files it owns, the check it must pass, and the files it must not touch.
- Don't run two agents on the same files. Don't redo work a subagent was given; wait for its result.

## Layout

```
api/        FastAPI app (main.py, stack.py, search.py, rerank.py, memo.py, llm.py, telemetry.py, cache/)
ingest/     offline pipeline: download -> clean -> chunk -> tag -> label -> index
web/        Vite + React + TypeScript + Tailwind (built into the same image as the API)
data/       talks.csv, tools.yaml (committed); raw/ and chunks.jsonl (gitignored)
deploy/     Kustomize manifests Argo CD syncs from main (Radar starter layout)
tests/      pytest, fixtures/, golden_stacks.yaml
docs/       BRIEF.md
```

## Commands

`make setup`, `make test` (ruff + pytest + oxlint + vitest), `make api` (:8000), `make web` (:5173, proxies /api), `make ingest`, `make index`, `make golden`. Python is 3.12 via `uv`; web uses `npm`.

## Conventions

- **One PR per task**, titled `Task N: <what>`, branched from `main`. Keep PRs small so Gitar's review says something. Never push straight to `main`: a push to `main` deploys.
- `make test` must pass before you open a PR. Run the task's own check and paste its output in the PR body.
- Config comes only from env vars (`.env.example` lists every one). Only `api/llm.py` talks to the model provider (`LLM_PROVIDER` = `anthropic` | `openai`).
- Type hints on all Python. Pin every dependency exactly (`uv add --bounds exact`, `npm i -E`) and run `uv run pip-audit` / `npm audit` after adding one.
- Every module that calls Elastic or the model must run against a fake, so unit tests need no network.
- Spans and logs carry ids, counts, scores and token totals — never transcript text, prompts or keys.
- Model output must be grounded in clip text from the index: every why line and memo line cites a clip; uncited lines are dropped.
- Two failed attempts at a check → stop and report what you tried.

## Secrets

The repo and the container image are **public**. Never commit or log `.env`, API keys, tokens or kubeconfigs. On the cluster, secrets come from the Kubernetes Secret `app-credentials`, which a person creates; only its name goes in git.

## AI disclosure

The hackathon requires disclosing AI assistance. Commits made with an agent carry a `Co-Authored-By` trailer, and the README has an AI disclosure section — keep both.

## Cluster and Radar

We deploy with the Radar hackathon starter (skyhook-io/cnh-starter): a push to `main` builds one image from the root `Dockerfile` (API plus built web app on port 8080), the Action commits its tag into `deploy/kustomization.yaml`, and Argo CD syncs `deploy/` into namespace `team-<team>`. Operate the cluster through the `radar-cloud` MCP, using the skills in `.agents/skills/`. The starter's agent notes follow; where they say "push to `main`", our one-PR-per-task rule above still applies.

@AGENTS.md
