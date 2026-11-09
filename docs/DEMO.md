# Debrief demo: video script, shot list and hero clips

For the team recording the submission video (16:30–17:15) and presenting live (17:30–18:30). Evidence and links for every claim are in [`TRACKS.md`](TRACKS.md). Live checks below were made against <https://brogrammers.cnh.radarhq.io> at about 13:50 MT, with 6 API calls in total.

**The one thing that has to land:** a judge sees a card, clicks Watch, and the speaker says what the card promised. Rehearse that click before anything else.

## 1. The pitch

**One sentence:** Debrief turns KubeCon's recorded talks into the 20 minutes that matter to one team: paste your `go.mod`, get a cited clip playlist that opens each talk at the right second, and a one-page memo for your manager.

**30-second problem statement** (numbers from `overview.pdf`, "The problem"):

> A KubeCon Europe runs over 300 sessions: 331 in 2024, for more than 12,000 people. A seasoned attendee manages about four a day, so even a diligent engineer sees under 5% of the program. Abstracts are a weak guide: 73% of accepted KubeCon NA 2024 speakers were vendor employees. The recordings go online, but nobody scrubs hours of video for the six minutes about their tool. So the team back home hears "it was great". Only 36% of regular attendees say their last event clearly delivered measurable value.

| Number | Source (as cited in overview.pdf) | Kind of source |
|---|---|---|
| 331 sessions, 12,000+ people (KubeCon EU 2024) | Jimmy Song on the CNCF report | Organizer report |
| About 4 sessions a day | Jimmy Dahlqvist (re:Invent veteran's guide) | Attendee guide |
| 73% of accepted KubeCon NA 2024 speakers were vendor employees | CNCF | Organizer data |
| 36% felt their last event clearly delivered measurable value | Hospitality.today on Skift, 2026 | Trade survey, skews toward executives |
| Over 90% of attendees value on-demand content | Freeman learning study, 2026 | Vendor survey |
| Sessions per person fell from 5.3 to 4.2 (spring 2025 → spring 2026) | MarTech360 on RainFocus; Skift | Vendor platform data |

Most of these come from companies that sell event software. Use them as framing, and don't put more than two on screen.

## 2. Main video script (about 3:00)

**Path: paste the demo `go.mod`** ([`tests/fixtures/demo-go.mod`](../tests/fixtures/demo-go.mod)). The best hero clip is in that playlist and in the offline cache. The GitOps example button is quicker, but none of its clips works as a hero (see section 4), so it's only a fallback.

| Time | On screen | What we say (word for word) |
|---|---|---|
| 0:00 | CNCF YouTube channel, KubeCon EU 2026 playlist, scrolling fast | "This is one KubeCon. Hundreds of recorded talks. Your team runs Argo CD, Istio and Prometheus. How much of this should they watch?" |
| 0:12 | One slide: **331 sessions · ~4 a day · 36%**, with sources in small type | "A KubeCon Europe runs over three hundred sessions. One engineer goes, sees about four talks a day, and the team back home hears 'it was great.' Only thirty-six percent of regular attendees say their last event clearly delivered measurable value." |
| 0:30 | Landing page → **Build your team's playlist** → paste the demo go.mod into "Or use a dependency file". Tool chips appear | "Debrief starts from what your team actually runs. Paste a go.mod, and it finds the tools: Argo CD, Istio, Envoy, Prometheus, OpenTelemetry and cert-manager." |
| 0:48 | **Build my playlist**. Header "24 minutes across 9 talks" (read whatever it shows). Scroll slowly past three cards: the *Deep dive* / *Introduction* label, "Found by keyword and semantic search", and the "What this conference said about your stack" chips | "Out of fifty-four KubeCon talks, here are the twenty-four minutes that matter to this stack. Each card says why it's for you, quotes the speaker, labels it deep dive or intro, and shows which half of the search found it. Up top: what this conference said about your stack." |
| 1:10 | **HERO.** Point at the card *Declarative...ish? Fixing Hidden Argo CD Pitfalls…* (16:45–18:07). Find it by title, not by rank: its position changes between runs | "This card says: managing cert-manager, Prometheus and Istio across ten clusters means fifty applications. Let's check." |
| 1:16 | Click **Watch**. YouTube opens at 16:45; press `c` for captions. Let it play until "…10 Kubernetes clusters that you maintain" | *Say nothing for about 20 seconds.* (In the edit, you can start the clip at 16:52, "So, let's imagine…", to cut the silence to about 12 s.) |
| 1:38 | **Take 3** (see section 5): Argo CD only, with *Declarative...ish?* ticked under "Talks we attended". Click **Talks we missed**, then scroll to *Sponsored Keynote: Argo CD vs the World*, labelled **Product pitch** | "Someone on the team saw that talk live, so tick it and show only what we missed. Debrief reads what was said, not the abstract, so it can tell a deep dive from a pitch: this sponsored keynote is labelled a product pitch from its transcript alone." |
| 2:00 | Back on the go.mod playlist: **Make memo** (cut the wait) → **Copy Markdown** → paste into a Slack DM and send | "One click turns the playlist into a memo: three takeaways, action items and a watch-together list. Every line cites a clip, or it's dropped. Copy, paste into Slack. And this is what your manager gets." |
| 2:22 | Open **Under the hood**: the RRF query JSON, the timings, the trace id. Copy the trace id → Dash0 tab → paste → the waterfall (`search.hybrid`, `search.elastic_rerank`, 3 × `rerank.batch`) | "None of it is a black box. Under the hood is the actual Elasticsearch query: keyword on your tools, semantic on what hurts, fused with RRF. And here's the same request in Dash0: search, rerank, three model calls in parallel, about three seconds end to end." |
| 2:45 | The landing headline, full screen | "Don't watch three hundred hours of KubeCon. Watch the twenty minutes that matter to your team. This is Debrief." |

Notes for the narrator:
- After Build, a banner says "No talk mentions Kubernetes; showing the rest of your stack." That happens because Kubernetes is excluded from matching by design (`exclude_from_matching` in `data/tools.yaml`), since it's said in every talk. Don't read it out. Crop it or scroll past it.
- The term chips are new (#26, merged about 13:50). The live API returned `terms` on the last call: *applications, metrics, pattern, receiver, already, put, manifest, extract*. Some are generic, so show the chips but don't read individual words.
- If a why line reads "Mentions Argo CD 4 times in this clip", a model batch fell back to the template (this happened once live on this stack). Build again, or use the cache.

## 3. "How we built it" (60–90 s, one beat per track)

Screenshot keys S1–S6 are from [TRACKS.md](TRACKS.md#screenshot-key). **S7–S10 are new and must be captured by 16:30.**

| # | Track | Show | What we say |
|---|---|---|---|
| 1 | **Elastic** (~18 s) | Live app: Under the hood JSON (the `rrf` retriever wrapped in `text_similarity_reranker`) → a card "Found by keyword" (the cert-manager *Project Update* card is keyword-only) → the term chips → `/api/insights` JSON in a tab (only if you've checked it shows sensible `empty_tools`) | "Elasticsearch is our only database, and it does the ranking. One RRF query: keyword on tool names, including how captions mishear them, like 'Argo city', and semantic on what hurts. Elastic's own Jina reranker reorders the top fifty in the same request. A significant-terms aggregation gives the conference's words for your stack, and the query log shows organizers which tools teams searched for that no talk covered." |
| 2 | **Gitar** (~18 s) | GitHub PR list → **S6** (Gitar's CI diagnosis on #1) with commit `f22fcc9` → `.gitar/config/risk.md` beside #15's "High risk" line → Gitar's request-changes on #20 → #21 | "Main is protected, so every change went through a pull request Gitar reviewed, with green CI. Our rules are about Debrief's promises: search changes update the golden stacks, every route gets a span and a test, the UI stays accessible. On PR one our CI broke for real, and Gitar diagnosed it and pushed the fix. It has pushed twenty-four commits to main, including a thread-pool starvation fix in our rerank, and on PR twenty it caught an offline-cache bug that we fixed in twenty-one." |
| 3 | **Dash0** (~20 s) | **S1** slow trace `a9172d99…` (12.6 s) → **S2** Agent0 (29 clips, 1,970 output tokens) → **S1** after trace `901fc63e…` (3.2 s) → **S8** Debrief health dashboard + 2 SLOs → **S9** RUM Watch clicks by rank (*S8/S9 only if live in Dash0*) | "Every request is one trace in Dash0. One playlist took twelve point six seconds. Agent0 read the trace: we were sending twenty-nine clips and getting almost two thousand output tokens back. We cut it to eighteen clips in three parallel batches, and the same request now takes about three seconds. Our dashboard tracks the product's promise, with SLOs on speed and on memo citations, and real-user monitoring shows which rank people actually click." |
| 4 | **Radar** (~18 s) | Radar Cloud: Argo CD app `app` synced in `team-brogrammers` (or **S10** topology) → README "MCP question log" → **S3** investigation → #14 → **S4** follow-up → **S5** Checks 3 findings → **S7** Checks 0 | "Debrief runs on the hackathon cluster, deployed by Argo CD from a deploy branch, so main stays protected. Our coding agent operated it through Radar's MCP, and the README logs every question. Radar's AI investigation tied our probe failures to a slow start, so we added a startup probe. When playlists were slow, one follow-up ruled the cluster out, which sent us to the model call. And Radar Checks for our namespace went from three findings to zero, over two rounds of fixes." |
| 5 | **Merge Forward** (~16 s) | Live app, keyboard only: Tab to an example stack → Enter → playlist (focus rings visible) → **Read** on *The Good, The Ugly, and The Bad…* (captions say "Isto"; the Read view should say "Istio", so check once) → the *Product pitch* label | "Debrief is the conference for people who couldn't be in the room: travel, childcare, visas. Every clip has a Read view with the captions, misheard tool names fixed, for Deaf and hard-of-hearing viewers or anyone who can't play audio. The whole flow works from the keyboard, results are announced to screen readers, and accessibility is checked twice: a lint in CI and a Gitar rule." |

S7 = Radar Checks after (0 findings) · S8 = Dash0 dashboard + SLOs · S9 = Dash0 RUM Watch clicks by rank · S10 = Radar topology of `team-brogrammers`. Add "we tested it with VoiceOver" to beat 5 only after the 16:35 pass.

## 4. Hero clip candidates

**Method:** 3 live playlists (GitOps example, Istio + Envoy, the demo go.mod's 7 tools) plus 1 "missed" variant. The 32 distinct clips in the first three playlists were timed against the YouTube auto-caption word timings in `data/raw/*.vtt`. Full text for A and B came from `GET /api/clips/{id}`. Offsets are measured from the `t=` second in the `watch_url`. Clips start at a chunk boundary, so the key sentence is never at +0 s.

**A person must watch each candidate once before recording:** auto-captions can lag 1–2 s, and the clip may land mid-sentence.

### A. Recommended: the speaker names our stack

| | |
|---|---|
| Talk | *Declarative...ish? Fixing Hidden Argo CD Pitfalls in Your GitOps Setup*, by Regina Voloshin (a recording she provided after her flight was cancelled) |
| Clip | `VfVQj4Oa3z0-1007`, 16:45–18:07, deep dive, found by both |
| watch_url | <https://www.youtube.com/watch?v=VfVQj4Oa3z0&t=1005s> |
| Start | **16:45** |
| The judge hears | **"So, let's imagine that you have five infrastructure applications, cert-manager, Prometheus, Grafana, external secrets, and Istio, and also let's imagine that all those apps need to be created in the 10 Kubernetes clusters that you maintain."** "So, let's imagine" at **+8 s**, "cert-manager" +12.6 s, "Prometheus" +14.4 s, "Istio" +16.8 s, "10 Kubernetes clusters" +22 s. Then "we are talking about 50 applications" (+26 s) and "please don't write those 50 individual application manifests" (+36 s) |
| Why line | Live: "Managing cert-manager, Prometheus and Istio across 10 clusters, about 50 applications, is a scaling problem." Cache: "Managing cert-manager, Prometheus and Istio across 10 clusters means 50 applications to maintain." |
| Quote | "So, let's imagine that you have five infrastructure applications, cert-manager, Prometheus, Grafana, external secrets, and Istio, and also let's imagine that all those apps need to be created in the 10 Kubernetes clusters that you maintain. So, in total, we are talking about 50 applications." |
| Why it's a good hero | We paste a go.mod, the card names three of its tools, and seconds later the speaker lists the same three tools out loud. That's the product's whole promise in one shot. It's in the go.mod playlist both live (#3) and cached (#6) |

### B. Backup: fastest payoff

| | |
|---|---|
| Talk | *Argo CD: Previewing Pull Request Changes in SECONDS!*, by Dag Bjerre Andersen and Sergey Shevchenko |
| Clip | `fcajag5di68-523~586`, 8:41–11:02, deep dive, found by both |
| watch_url | <https://www.youtube.com/watch?v=fcajag5di68&t=521s> |
| Start | **8:41** |
| The judge hears | **"But before you get too excited and go home and set this up to your production instance that deploys all your production workload, don't do that, please"** from **+4 s** to +11 s, then "created and deleted, created and deleted, created and deleted" (+14 s), "it's also going to overload your production Argo CD instance and crash it. I have done that a few times" (+25–29 s) and "always use a dedicated Argo CD instance" (+32 s) |
| Why line | Live: "Argo CD: use a dedicated instance for preview processes, based on repeated experience." Cache: "Argo CD: advises a dedicated Argo CD instance for preview environments, a practical deployment pattern." |
| Quote | "I have done that a few times. So, what I suggest is always use a dedicated Argo CD instance for this kind of preview process." |
| Why it's a good hero | The warning lands in 4 seconds and gets a laugh ("I have done that a few times"). It's in the same go.mod playlist (live #6, cached #9), so you can switch to it on the spot. The card's exact quote comes at +28 s, so let it run 30 s |

### C. For the "Service mesh" example, or a judge who runs Istio

| | |
|---|---|
| Talk | *Evolution or Revolution: Istio as the Network Platform for Cloud Native*, by Mitch Connors and Daniel Grimm (here Daniel is speaking: "you've already heard Mitch…") |
| Clip | `4K6s5y8HoBc-790`, 13:08–14:27, introduction, found by both |
| watch_url | <https://www.youtube.com/watch?v=4K6s5y8HoBc&t=788s> |
| Start | **13:08** |
| The judge hears | "So, you've already heard Mitch talk about ztunnel a little bit. **This is the purpose-built proxy for ambient mesh.**" (+5 to +8 s) "It allows you to do L4 routing and mTLS on your node" (+12 s), then **"it has way higher throughput, and lower latency than if you were to use like full-fledged Envoy proxy"** (+18 s), "it's written in Rust" (+26 s) |
| Why line | Live: "Rust-based proxy claims higher throughput, lower latency and easier maintenance than Envoy." Cache: the same, ending "than full Envoy." |
| Quote | "And it's it has way higher throughput, and lower latency than if you were to use like full-fledged Envoy proxy, simply because the code base is much smaller, it's written in Rust." |
| Why it's a good hero | An Istio maintainer gives a concrete, quotable claim. But it's low in the list (live #11 of 12, cached #9), and the key line comes at +18 s. Use it only when someone picks Istio + Envoy |

**Use A for the video.** It's the most literal "speaker says what the card promised", and it matches the go.mod we just pasted. Keep B one scroll away for the live presentation.

**Rejected:** the GitOps example's #1 clip (`VfVQj4Oa3z0-1530`) is the speaker plugging another lightning talk, with its quote at +36 s. Its #2 and #3 clips land at +24 s and +33 s. The top card for go.mod and mesh (`ToKv7JntLvY-1321`) has its quote at +24 s, and the captions are garbled. `4K6s5y8HoBc-1051` (Istio observability, also in the go.mod playlist) only reaches Prometheus at +25 s.

## 5. Recording plan

**Takes, in this order.** Record each as its own clip so one bad take doesn't sink the rest.
1. **Take 1, product (0:30–1:38 and 2:00–2:45):** paste go.mod → playlist → hero A → Make memo → Slack → Under the hood → Dash0. Do this first, while the live API is warm. If the why lines show template text, build again.
2. **Take 2, problem (0:00–0:30):** YouTube channel scroll, plus the slide.
3. **Take 3, missed + pitch (1:38–2:00):** reload → pick only **Argo CD** → tick *Declarative...ish?* under "Talks we attended" → Build → **Talks we missed** → scroll to *Sponsored Keynote: Argo CD vs the World* (Steve Fenton), **Product pitch**. The go.mod stack doesn't surface a pitch (checked live with the missed filter: 15 clips, none labelled pitch). The cached Argo CD-only "missed" playlist has it at #13, with the why line "Promotes a free report comparing Argo CD versus the world; mostly marketing." Check it live first. If it's missing, record this take against the local cache.
4. **Take 4, how we built it:** the screenshot and tab inserts from section 3.
5. **Take 5, tagline**, plus one **continuous run-through** of the whole demo, as the backup for the live presentation.

**Browser setup:** a fresh Chrome profile with no extensions and no bookmarks bar; zoom **125%**; Do Not Disturb on; Slack notifications muted; light theme. Turn YouTube captions on (`c`) the first time a video opens. If an ad plays before a talk, cut it in the edit. For the live presentation, play each hero video once beforehand. Pre-open these tabs, in order:

| # | Tab |
|---|---|
| 1 | <https://brogrammers.cnh.radarhq.io> (live site; check the footer commit matches `main`) |
| 2 | Dash0: the trace search (paste the id from Under the hood) |
| 3 | Dash0: the Debrief health dashboard and SLOs |
| 4 | Radar Cloud: Checks for `team-brogrammers` |
| 5 | Radar Cloud: investigation `chat_Ls_kRKpAfQvsbFpu` (S3, then the S4 follow-up) |
| 6 | GitHub: [PR #15](https://github.com/BrendanBeckstrom/brogrammers2026/pull/15) (Gitar review, High risk, `b017b70`) and [#1](https://github.com/BrendanBeckstrom/brogrammers2026/pull/1) (`f22fcc9`) |
| 7 | GitHub: [PR list](https://github.com/BrendanBeckstrom/brogrammers2026/pulls?q=is%3Apr) |
| 8 | The "Hackathon plans" Google Doc with S1–S10 |
| 9 | Slack: a DM to yourself, for the memo paste |

**Local fallback with `DEBRIEF_CACHE=on`** (from the README's "Demo safety" note): run `make api` (live, with `.env`) → `make cache` → stop it → `DEBRIEF_CACHE=on make api` plus `make web` → <http://localhost:5173>. Cached answers carry `X-Debrief-Cache: hit` and never touch Elastic or the model. Anything uncached goes live, or returns 503 when offline. Rebuild the cache after 16:30: the cache in git predates #26, so it has no term chips. Clip text for the Read view is built locally and stays out of git. `make golden ARGS=--cache` proves every golden stack is served from the cache.

**If the venue Wi-Fi fails:** (1) switch to a phone hotspot; (2) run the local cache on `localhost` for the app (YouTube and Dash0 still need network); (3) play the recorded video. It already contains the hero clip, so we never re-host or download a talk. Keep the video file on the laptop's desktop as well as wherever it's uploaded.

### Last 45 minutes (two people: **N** = narrator/recorder, **R** = runner)

- [ ] **16:30 Feature freeze.** No more merges except a fix for something that breaks the demo. Confirm the footer commit matches `main` (`/api/version`). (R)
- [ ] **16:30** `make cache`, then `make golden ARGS=--cache`; keep the cache local. (R)
- [ ] **16:30** Capture S7–S10 (Radar Checks at 0, Dash0 dashboard/SLOs, RUM by rank, topology). (R)
- [ ] **16:35** Takes 1–5. (N)
- [ ] **16:35** VoiceOver pass, 10 min (Cmd+F5): tab from the landing page → example stack → "Playlist loaded…" announced → Read → Make memo. Add 3–5 findings to the README. (R)
- [ ] **16:50** README check: live URL, setup, Operating Debrief, AI disclosure, link to TRACKS.md. Remove "every recorded talk" wording if there's time (see section 7). (R)
- [ ] **17:00** Edit: trim the memo wait and the hero lead-in; export; upload. (N)
- [ ] **17:10** Submission form: repo, live URL, video link, every track we enter. (N + R)
- [ ] **17:15 Submitted.** The form closes at 17:30. Then charge the laptop, turn on the hotspot, and open the tabs for the live presentation.

## 6. Q&A prep (judges include Kelsey Hightower and Wes Novack)

| Question | Short, honest answer |
|---|---|
| Why not just chat with the transcripts? | Chat starts from a blank prompt and answers in paragraphs. Debrief starts from your real stack, answers with timestamps you can click, and ends in a memo you can forward. There's no chat box on purpose. |
| Why RRF instead of only semantic search? | Captions mangle tool names ("Argo city", "Isto"), and embeddings blur similar products. Keyword search on tool names and their misspellings catches the exact tools; semantic search catches the pain sentence when no tool is named. RRF fuses the two by rank, without calibrating scores. Each card shows which half found it, and the cert-manager card was found by keyword alone. |
| How do you stop the model from inventing advice? | Elastic retrieves and reranks. The model only re-scores the top 18 clips and writes a why line of at most 20 words that must name a matched tool, or we use a template. For the memo, it sees only the chosen clips' text, loaded from Elastic by id (never from the browser). Every line must cite a requested clip or it's dropped, and drops are counted in Dash0. If the model fails, we return a 502, never a fake memo. |
| What's the data licensing? | These are public CNCF recordings. We store only the auto-caption text for search, show short quotes, and link to YouTube at the second. We never re-host video. We haven't asked CNCF; a real product would need their blessing, and would fit naturally as an LF/CNCF feature. |
| What breaks at 10x scale? | Not Elastic: 1,270 chunks becomes about 13,000. First the model rerank: cost and rate limits per request (3 calls, ~2 s). We'd cache answers per stack (the `answers` index already stores them) and lean harder on the in-Elastic reranker. Next, the hand-written tool dictionary (40 tools) and caption quality. |
| What did Radar actually find? | An investigation on Deployment `app` found probe failures at rollout, because Python took ~16 s to listen. We added a startup probe (#14). A follow-up ruled out the cluster for slow playlists (show S4), which pointed us to the model call. Checks: 3 findings fixed in #18, then 2 that appeared with 2 replicas fixed in #19, now 0. Radar didn't find the latency bug; Dash0 and Agent0 did. |
| How much did Gitar change? | 24 Gitar-authored commits on `main` (3 of them docs). They include real bugs: a CI tag that didn't exist (#1), model HTML reaching the memo (#12), a thread-pool starvation bug in rerank (#15), and offline Elastic returning 502 instead of a cache miss (#20 → #21). `risk.md` makes it treat deploy and LLM changes as high risk. |
| Where did AI help, and what did the team decide? | Claude Code wrote much of the code from our brief, and its commits carry `Co-Authored-By`. We decided the product (stack in, moments out, for the team that stayed home), Elastic as the only store with ranking in Elastic, the citation rule, the deploy-branch GitOps flow, secrets created by a person and never by the agent, and the cut list. We reviewed every PR. At runtime, Claude Haiku 5.5 writes why lines and Sonnet 5.5 writes the memo. |
| Does this need Kubernetes? | Honestly, no: it's one stateless container plus Elastic. We ran it on the hackathon cluster for the GitOps flow, 2 replicas behind a PDB, probes, and Radar. Adding a conference is an offline ingest job, which could run as a Kubernetes Job. |
| How do you know the clips are relevant? | Golden stacks run in CI as structural checks, plus a person's relevance check. Live, RUM Watch clicks by rank show whether people pick #1 more than #8 (*only if S9 exists*). We have no formal relevance benchmark. |

## 7. Claims to avoid

From [TRACKS.md](TRACKS.md#claims-to-avoid), with updates after #26 (merged about 13:50):

| Don't say | Say instead | Why |
|---|---|---|
| "Elastic hosts our LLM" | "Elastic does search and reranking; Anthropic writes the text" | The Elastic-hosted completion endpoints returned 403; the deployment sets `LLM_PROVIDER=anthropic` |
| "Playlists and queries are stored in Elastic", "shareable links", "/api/insights", "term chips" | Term chips: OK (the live API returned `terms`). Share links and insights: only after you've clicked Share and opened `/api/insights` once yourself | #26 is merged, but only `terms` was checked live |
| "We have RUM / a dashboard / SLOs" | Say so only once they're visible in Dash0 and in S8/S9 | The definitions are on main; live setup is unverified |
| "A browser click traces through to the backend" | Only after an end-to-end browser trace is verified | Depends on the RUM token being active |
| "Gitar auto-fixed 20 bugs" | "24 Gitar-authored commits on main" (3 are docs; recount before recording) | Not all are bug fixes |
| "You can see Gitar's CI diagnosis in PR #1" | Show S6, then the fix commit `f22fcc9` | The PR thread has the fix commit, not the diagnosis |
| "10/10 why lines after the fix" | "0/15 → 9/9 model why lines" | That's what PR #15 measured |
| "risk.md made every deploy PR High risk" | "13 of 20 PRs were rated High; from #8 on, Gitar cites our repository policy" | #5 was rated Medium |
| "Radar found the latency bug" | "Radar ruled out the cluster; Dash0 and Agent0 found the cause" | That's what happened |
| "Argo CD deploys from `main`" | "Argo CD syncs the `deploy` branch the Action publishes" | Deploy flow |
| "Fully WCAG 2.2 AA compliant" / "tested with VoiceOver" | "Built against WCAG 2.2 AA checks in CI and review; keyboard-tested" | No audit; VoiceOver only after the 16:35 pass |
| "Playlists always take 3 seconds" | "12.6 s → 3.2 s on the traced request; 3.1–3.8 s on today's live checks" | One before/after pair plus spot checks |
| "The labeller detects all vendor pitches" | "All 5 sponsored keynotes came out as pitch, from the transcript alone" | 54 talks is a small sample |
| Radar follow-up numbers (CPU 0.3%, memory 22%) with no screenshot | Show S4 while saying them | The API returns only the first finding |
| **New:** "Debrief covers every KubeCon talk" (the landing page says "every recorded talk") | "54 recorded talks from KubeCon EU 2026" | The index holds 54 talks. Fix the landing copy if there's time |
| **New:** "KubeCon has 300 hours of video" as a fact | It's the tagline. The fact is "331 sessions at KubeCon EU 2024" | Not measured |
| **New:** "No talk mentions Kubernetes" | Kubernetes is excluded from matching because every talk says it | `exclude_from_matching` in `data/tools.yaml` |
| **New:** "The model only writes the why line" (TRACKS says this too) | "Elastic retrieves and reranks the top 50; the model re-scores the top 18 and writes the why line" | Per `docs/BRIEF.md`, the playlist is re-sorted by model score and clips under 0.3 are dropped |
| **New:** "Every why line is written by the model" | "Most are; if a model call is slow, that clip gets a template line" | 1 of 3 batches fell back live on the go.mod stack today |
| **New:** "This is a real company's go.mod" | "A go.mod for a Go release tool" | `demo-go.mod` is a constructed example (`acme-platform/release-gate`) |
| **New:** "The video opens on the exact sentence" | "It opens at the clip's first second; the sentence comes a few seconds in" | Clips start at chunk boundaries (+4 to +8 s for A and B) |
| **New:** "Regina said this on stage" | "From Regina Voloshin's talk" | Her flight was cancelled and the session played her recording |
