---
title: "Task-numbered PRs"
description: "Every feature lands as a small PR tied to one task in docs/BRIEF.md"
when: "A pull request is opened or its title changes"
actions: "Comment asking for a task-numbered title when the title does not start with 'Task <number>:'"
type: check
---

# Task-numbered PRs

Each pull request implements one task from the Tasks table in `docs/BRIEF.md` and is titled `Task N: <what>` (for example `Task 4: tool dictionary and tagging`). If the title doesn't start with `Task` followed by a number and a colon, comment once asking the author to rename it and to paste the task's check output into the PR description. Docs-only or chore PRs may use `Chore:` instead.
