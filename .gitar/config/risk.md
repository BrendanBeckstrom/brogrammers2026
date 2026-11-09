Treat these as high risk and review them closely:
- deploy/, argocd/, Dockerfile and .github/workflows/ — they change what runs on the cluster and how images are built and published (the repo and image are public).
- api/llm.py — the only code that talks to the model provider and handles its key.
- Anything that reads secrets or environment variables, or writes logs or span attributes.
- api/memo.py and api/rerank.py — the citation check that keeps model output grounded lives here.
