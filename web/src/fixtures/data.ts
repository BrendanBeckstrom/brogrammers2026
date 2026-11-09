// Sample API responses for the dev-only mock mode (`?mock=1`) and for unit tests.
// Talk titles, speakers, quotes and transcripts are invented for illustration; they are
// not real KubeCon talks and the "demo-*" video ids do not exist on YouTube.
import type { Clip, MemoResponse, PlaylistResponse, StackResponse, Talk, Tool } from '../api'
import { formatTimestamp } from '../format'

export const TOOLS: Tool[] = [
  { id: 'argo-cd', name: 'Argo CD', chunk_count: 64 },
  { id: 'argo-rollouts', name: 'Argo Rollouts', chunk_count: 18 },
  { id: 'argo-workflows', name: 'Argo Workflows', chunk_count: 22 },
  { id: 'flux', name: 'Flux', chunk_count: 9 },
  { id: 'helm', name: 'Helm', chunk_count: 31 },
  { id: 'crossplane', name: 'Crossplane', chunk_count: 12 },
  { id: 'backstage', name: 'Backstage', chunk_count: 15 },
  { id: 'prometheus', name: 'Prometheus', chunk_count: 57 },
  { id: 'opentelemetry', name: 'OpenTelemetry', chunk_count: 73 },
  { id: 'grafana', name: 'Grafana', chunk_count: 26 },
  { id: 'jaeger', name: 'Jaeger', chunk_count: 11 },
  { id: 'thanos', name: 'Thanos', chunk_count: 8 },
  { id: 'istio', name: 'Istio', chunk_count: 48 },
  { id: 'envoy', name: 'Envoy', chunk_count: 39 },
  { id: 'cilium', name: 'Cilium', chunk_count: 14 },
  { id: 'gateway-api', name: 'Gateway API', chunk_count: 21 },
  { id: 'kyverno', name: 'Kyverno', chunk_count: 7 },
  { id: 'opa', name: 'Open Policy Agent', chunk_count: 10 },
  { id: 'keda', name: 'KEDA', chunk_count: 6 },
  { id: 'postgres', name: 'PostgreSQL', chunk_count: 9 },
]

const speakersOf = {
  argo1: ['Ana Ruiz', 'Jonas Becker'],
  argo2: ['Priya Natarajan'],
  argo3: ['Marta Kowalski', 'Sam Okafor'],
  prom1: ['Lukas Meyer'],
  prom2: ['Elena Petrova'],
  otel1: ['Daniel Kim', 'Fatima Haddad'],
  otel2: ['Chris Novak'],
  otel3: ['Ines Duarte'],
  istio1: ['Kenji Watanabe', 'Laura Schmidt'],
}

export const TALKS: Talk[] = [
  { talk_id: 'demo-argo-01', title: 'Sync Waves at Scale: Cutting Argo CD Reconcile Time From Minutes to Seconds', speakers: speakersOf.argo1 },
  { talk_id: 'demo-argo-02', title: 'Previewing Every Pull Request With Argo CD and ApplicationSets', speakers: speakersOf.argo2 },
  { talk_id: 'demo-argo-03', title: 'From Jenkins Scripts to GitOps: Five Years of Argo CD at a Bank', speakers: speakersOf.argo3 },
  { talk_id: 'demo-prom-01', title: 'Prometheus 3 in Practice: Native Histograms and the New UI', speakers: speakersOf.prom1 },
  { talk_id: 'demo-prom-02', title: 'PromQL Rate Pitfalls and How to Read Them', speakers: speakersOf.prom2 },
  { talk_id: 'demo-otel-01', title: 'We Replaced Three Agents With One OpenTelemetry Collector', speakers: speakersOf.otel1 },
  { talk_id: 'demo-otel-02', title: 'Tail Sampling Without Tears: OpenTelemetry at Two Million Spans per Second', speakers: speakersOf.otel2 },
  { talk_id: 'demo-otel-03', title: 'OpenTelemetry Gateways for Platform Teams', speakers: speakersOf.otel3 },
  { talk_id: 'demo-istio-01', title: 'Ambient Mesh in Production: Dropping Sidecars Without Dropping Requests', speakers: speakersOf.istio1 },
  { talk_id: 'demo-istio-02', title: 'Istio Multi-Cluster Upgrades, Canary by Canary', speakers: ['Oliver Brandt'] },
  { talk_id: 'demo-envoy-01', title: 'Envoy Gateway Migration Lessons From 400 Ingress Rules', speakers: ['Nadia Rahman'] },
  { talk_id: 'demo-key-01', title: 'Keynote: The Next Ten Years of Cloud Native', speakers: ['Rosa Alvarez'] },
]

const watch = (talkId: string, startSec: number): string =>
  `https://www.youtube.com/watch?v=${talkId}&t=${startSec}s`

function clip(
  talkId: string,
  startSec: number,
  endSec: number,
  fields: Pick<Clip, 'quote' | 'why' | 'matched_tools' | 'matched_by' | 'talk_label' | 'score'>,
): Clip {
  const talk = TALKS.find((t) => t.talk_id === talkId)
  if (!talk) throw new Error(`unknown fixture talk ${talkId}`)
  return {
    clip_id: `${talkId}-${startSec}`,
    talk_id: talkId,
    title: talk.title,
    speakers: talk.speakers,
    start_sec: startSec,
    end_sec: endSec,
    watch_url: watch(talkId, startSec),
    ...fields,
  }
}

/** Nine clips from nine talks, 1,320 seconds: "22 minutes across 9 talks". */
export const PLAYLIST_CLIPS: Clip[] = [
  clip('demo-argo-01', 872, 1090, {
    quote:
      'We stopped syncing everything on every commit and started sharding by application group. Reconcile time went from eleven minutes to forty seconds.',
    why: 'You run Argo CD and your syncs are slow; this is how they cut reconcile time.',
    matched_tools: ['argo-cd'],
    matched_by: 'both',
    talk_label: 'deep_dive',
    score: 0.94,
  }),
  clip('demo-otel-01', 662, 860, {
    quote:
      'Three agents meant three configs and three upgrade schedules. One collector with a Prometheus receiver gave us the same metrics and one place to fix things.',
    why: 'You run OpenTelemetry and Prometheus; one collector can scrape both.',
    matched_tools: ['opentelemetry', 'prometheus'],
    matched_by: 'both',
    talk_label: 'deep_dive',
    score: 0.91,
  }),
  clip('demo-argo-02', 365, 521, {
    quote:
      'The pull request generator spins up a preview app per branch. Reviewers see the rendered diff before anything reaches the cluster.',
    why: 'Argo CD ApplicationSets give every pull request a preview environment.',
    matched_tools: ['argo-cd'],
    matched_by: 'keyword',
    talk_label: 'deep_dive',
    score: 0.88,
  }),
  clip('demo-prom-01', 192, 362, {
    quote:
      'Native histograms are stable now. You get far better resolution for latency with fewer series than classic buckets.',
    why: 'Prometheus 3 native histograms cut series count for your latency metrics.',
    matched_tools: ['prometheus'],
    matched_by: 'both',
    talk_label: 'intro',
    score: 0.84,
  }),
  clip('demo-istio-01', 500, 583, {
    quote:
      'Ambient mode moved the proxy out of the pod. Our memory per node dropped by a third and upgrades stopped needing a restart of every workload.',
    why: 'If Istio sidecars cost you memory, ambient mode is the migration path.',
    matched_tools: ['istio'],
    matched_by: 'both',
    talk_label: 'deep_dive',
    score: 0.81,
  }),
  clip('demo-prom-02', 580, 700, {
    quote:
      'Rate needs at least two samples in the window. If your scrape interval is thirty seconds, a one-minute range will lie to you.',
    why: 'Common PromQL rate mistakes that make Prometheus dashboards misleading.',
    matched_tools: ['prometheus'],
    matched_by: 'keyword',
    talk_label: 'deep_dive',
    score: 0.77,
  }),
  clip('demo-argo-03', 1270, 1420, {
    quote:
      'The hardest part was not Argo CD. It was getting forty teams to agree that Git is the only way into production.',
    why: 'How one company rolled Argo CD out to forty teams without a big bang.',
    matched_tools: ['argo-cd'],
    matched_by: 'semantic',
    talk_label: 'intro',
    score: 0.72,
  }),
  clip('demo-otel-03', 1065, 1170, {
    quote:
      'A gateway collector is where platform teams enforce policy: drop high-cardinality attributes, redact secrets, and route each team to its own backend.',
    why: 'An OpenTelemetry gateway lets your platform team control telemetry centrally.',
    matched_tools: ['opentelemetry'],
    matched_by: 'semantic',
    talk_label: null,
    score: 0.68,
  }),
  clip('demo-otel-02', 270, 390, {
    quote:
      'Tail sampling keeps every error and slow trace and drops most of the boring ones. Our hosted product does this for you out of the box.',
    why: 'Tail sampling in OpenTelemetry keeps the traces worth paying for.',
    matched_tools: ['opentelemetry'],
    matched_by: 'semantic',
    talk_label: 'pitch',
    score: 0.61,
  }),
]

export const PLAYLIST: PlaylistResponse = {
  total_seconds: PLAYLIST_CLIPS.reduce((sum, c) => sum + (c.end_sec - c.start_sec), 0),
  clips: PLAYLIST_CLIPS,
  trace_id: '4bf92f3577b34da6a3ce929d0e0e4736',
  debug: {
    query: {
      retriever: {
        rrf: {
          retrievers: [
            { standard: { query: { bool: { filter: [{ terms: { tools: ['argo-cd', 'prometheus'] } }] } } } },
            { standard: { query: { semantic: { field: 'text_semantic', query: 'our Argo CD syncs are slow' } } } },
          ],
          rank_window_size: 100,
        },
      },
      size: 60,
    },
    timings_ms: { stack: 3, search: 412, group: 2, rerank: 1840, total: 2271 },
  },
}

const sentences = (clipQuote: string, before: string, after: string): string => `${before} ${clipQuote} ${after}`

export const CLIP_TEXT: Record<string, string> = Object.fromEntries(
  PLAYLIST_CLIPS.map((c) => [
    c.clip_id,
    sentences(
      c.quote,
      'So let me show you what this looked like before we changed anything. We had the default settings everywhere and it worked fine until it did not.',
      'That was the single biggest change we made this year. If you take one thing from this part of the talk, measure first, then change one setting at a time and watch the dashboards for a full day before the next change.',
    ),
  ]),
)

export const STACK: StackResponse = {
  tools: [
    { id: 'argo-cd', name: 'Argo CD', source: 'github.com/argoproj/argo-cd/v2' },
    { id: 'prometheus', name: 'Prometheus', source: 'github.com/prometheus/client_golang' },
    { id: 'opentelemetry', name: 'OpenTelemetry', source: 'go.opentelemetry.io/otel' },
    { id: 'istio', name: 'Istio', source: 'istio.io/client-go' },
  ],
  unknown_packages: ['github.com/spf13/cobra', 'golang.org/x/sync', 'github.com/stretchr/testify'],
}

export const SAMPLE_GO_MOD = `module example.com/payments

go 1.23

require (
\tgithub.com/argoproj/argo-cd/v2 v2.13.1
\tgithub.com/prometheus/client_golang v1.20.5
\tgo.opentelemetry.io/otel v1.32.0
\tistio.io/client-go v1.24.0
\tgithub.com/spf13/cobra v1.8.1
\tgolang.org/x/sync v0.9.0
\tgithub.com/stretchr/testify v1.10.0
)
`

const c = (i: number): Clip => PLAYLIST_CLIPS[i]
const link = (x: Clip, label: string): string => `[${label}, ${formatTimestamp(x.start_sec)}](${x.watch_url})`

export const MEMO: MemoResponse = {
  markdown: `# Debrief: KubeCon EU 2026 for your stack

## Top three takeaways

1. **Shard Argo CD syncs by application group.** One team cut reconcile time from eleven minutes to forty seconds. ${link(c(0), 'Sync Waves at Scale')}
2. **One OpenTelemetry Collector can replace separate agents.** A Prometheus receiver keeps the same metrics with one config to maintain. ${link(c(1), 'Three Agents to One Collector')}
3. **Prometheus 3 native histograms are stable.** Better latency resolution with fewer series. ${link(c(3), 'Prometheus 3 in Practice')}

## Action items

- Try sharding the slowest Argo CD project by application group in staging. ${link(c(0), 'clip')}
- Check every \`rate()\` range in our dashboards is at least four scrape intervals. ${link(c(5), 'clip')}
- List the agents we run per node and price out a single collector. ${link(c(1), 'clip')}

## Watch together

- [Sync Waves at Scale (14:32–18:10)](${c(0).watch_url})
- [We Replaced Three Agents With One OpenTelemetry Collector (11:02–14:20)](${c(1).watch_url})
- [Ambient Mesh in Production (08:20–09:43)](${c(4).watch_url})
`,
  cited_clip_ids: [c(0).clip_id, c(1).clip_id, c(3).clip_id, c(5).clip_id, c(4).clip_id],
}
