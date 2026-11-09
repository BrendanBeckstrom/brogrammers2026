---
title: "Every API route has a span and a test"
description: "New or changed FastAPI routes are traced to Dash0 and tested"
when: "A PR adds or changes a FastAPI route under api/"
actions: "Comment listing any route that lacks an OpenTelemetry span or a test in tests/"
type: check
---

# Every API route has a span and a test

Every request through Debrief must be explainable from its Dash0 trace ("why was this clip picked?"). For each route a PR adds or changes under `api/`, check that its work runs inside an OpenTelemetry span (in addition to FastAPI auto-instrumentation) whose attributes are ids, counts, scores or token totals, never transcript text, prompts or keys, and that `tests/` exercises the route with fakes and no network. Comment once listing each route that is missing either.
