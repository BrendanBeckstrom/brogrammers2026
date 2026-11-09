---
title: "Search changes update the golden stacks"
description: "Ranking or tagging changes must keep the acceptance stacks honest"
when: "A PR modifies api/search.py, api/rerank.py, api/stack.py, ingest/tag.py, ingest/chunk.py or data/tools.yaml"
actions: "If tests/golden_stacks.yaml is not changed and the PR description doesn't explain why, comment asking for one or the other"
type: check
---

# Search changes update the golden stacks

Debrief's promise is that a team's stack returns the right clips. `tests/golden_stacks.yaml` and `make golden` are how we know that still holds. When a PR changes how chunks are tagged, searched, grouped, merged or ranked, it must either update `tests/golden_stacks.yaml` (new expectations, new stacks) or state in the description why the existing golden stacks still cover it. If neither is present, comment once asking for it.
