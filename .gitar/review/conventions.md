# Debrief review conventions

The spec is `docs/BRIEF.md`. Its contracts (tools.yaml shape, chunk record, Elastic `clips` mapping, API request/response shapes, clip shape) are fixed: flag any code that drifts from them unless the same PR updates `docs/BRIEF.md` first.

## Must flag

- Any secret, API key, token, kubeconfig or `.env` content in code, config, manifests, logs or test fixtures. The repo and the container image are public.
- A dependency added without an exact pin (`==` in pyproject.toml, no `^`/`~` in web/package.json).
- Code outside `api/llm.py` that imports `anthropic` or `openai` or calls a model provider directly.
- Model output that is not grounded in clip text from the index: every "why" line and memo takeaway must cite a clip id that was requested, and uncited lines must be dropped.
- The memo route trusting clip text sent by the browser instead of loading it from the index by id.
- OpenTelemetry spans or log lines that carry transcript text, prompts or keys. Ids, counts, scores and token totals only.
- Unit tests that need the network. Elastic and model calls must be swappable for a fake.
- Python functions without type hints.

## Correctness gotchas

- `watch_url` is `https://www.youtube.com/watch?v={youtube_id}&t={start_sec}s` and must match the clip's `start_sec`.
- Clips run 60–240 seconds, start at a sentence start two seconds early, and come from at most 3 chunks per talk.
- Group search hits by `talk_id` in Python (max three per talk); don't rely on Elastic `collapse` alongside the RRF retriever.
- If the model call fails, `/api/playlist` returns Elastic's order, not an error.
- A stack whose tools appear in no chunk returns zero clips and a message, never weak matches.
- Bare "argo" is ambiguous (Argo CD vs Argo Workflows) — aliases must not name two tools.
- Kubernetes manifests in `deploy/` must keep Service `app` on port 80, `/healthz` and `/readyz` probes, resource requests and limits, and `runAsNonRoot`.

## Product promises (each rule protects one)

- **Citations:** every model output shown to a user (a "why" line, a memo line) must cite a clip id from the request. Flag any code path that returns model text without passing the citation check.
- **Watch links:** build them only with the shared `watch_url()` helper, never by string concatenation or f-strings elsewhere, so the link always lands on the clip's first second.
- **Search changes:** a change to search, clip building, ranking, tagging or `data/tools.yaml` must update `tests/golden_stacks.yaml` or say in the PR description why it doesn't need to.
- **Observability:** every new API route gets an OpenTelemetry span and a test. Spans carry ids, counts, scores and token totals only.
- **Accessibility (WCAG 2.2 AA):** every UI control has an accessible name, focus is visible, everything works by keyboard, and no state is shown by colour alone. Loading results are announced through an ARIA live region.
- **Pins:** dependency versions are pinned exactly.
