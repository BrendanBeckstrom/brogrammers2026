// One-click example stacks on the landing section, and the static numbers it shows.

export interface ExampleStack {
  id: string
  name: string
  /** Tool ids from GET /api/tools. */
  tools: string[]
  pain: string
  blurb: string
}

export const EXAMPLE_STACKS: ExampleStack[] = [
  {
    id: 'gitops',
    name: 'GitOps',
    tools: ['argo-cd', 'prometheus'],
    pain: 'our Argo CD syncs are slow',
    blurb: 'Deploys from Git, watched by Prometheus.',
  },
  {
    id: 'observability',
    name: 'Observability',
    tools: ['opentelemetry', 'prometheus'],
    pain: '',
    blurb: 'Traces, metrics and the collector in between.',
  },
  {
    id: 'mesh',
    name: 'Service mesh',
    tools: ['istio', 'envoy'],
    pain: '',
    blurb: 'Sidecars, ambient mode and the proxy underneath.',
  },
]

export const CONFERENCE = 'KubeCon + CloudNativeCon Europe 2026'
/** Where Debrief was built. The indexed talks are the latest KubeCon with recordings online. */
export const BUILT_AT = 'Built in Salt Lake City · Cloud Native Hackathon 2026'

/** Shown until /api/talks answers, and when it can't. */
export const STATIC_TALK_COUNT = 54
/** Clips in the index; the API has no cheap count for this, so it is static. */
export const STATIC_CLIP_COUNT = 1270

export const GITHUB_URL = 'https://github.com/BrendanBeckstrom/brogrammers2026'

/** "Argo CD, Prometheus" from ids, falling back to the id when the tool list hasn't loaded. */
export function toolList(ids: string[], names: Map<string, string>): string {
  return ids.map((id) => names.get(id) ?? FALLBACK_NAMES[id] ?? id).join(', ')
}

const FALLBACK_NAMES: Record<string, string> = {
  'argo-cd': 'Argo CD',
  prometheus: 'Prometheus',
  opentelemetry: 'OpenTelemetry',
  istio: 'Istio',
  envoy: 'Envoy',
}
