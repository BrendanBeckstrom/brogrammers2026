// Real-user monitoring (RUM) with Dash0's web SDK (@dash0/sdk-web).
//
// The settings come from the API at runtime (GET /api/config), so no token is baked into the
// build or committed. When the API answers `{"rum": null}` (DASH0_WEB_TOKEN unset), or anything
// at all goes wrong, nothing is loaded and the page works as before: initRum() never throws and
// never blocks rendering. The SDK is imported lazily, so it stays out of the main bundle.
//
// What it sends: page views, navigation timing, web vitals, JS errors and one span per fetch.
// Same-origin /api/* calls carry a W3C `traceparent`, so each browser request span is the parent
// of the API's server span in Dash0. Clicks on elements marked `data-rum-event="<name>"` become
// Dash0 custom events named <name>, with only `rank` (data-rank) and `clip_id` (data-clip-id).
//
// Privacy: never transcript text or the pain sentence. Session replay and frustration signals
// (rage clicks report the clicked element's text, which can be a transcript quote) stay off,
// and the click events carry only the two attributes above, never element text.

export interface RumConfig {
  endpoint: string
  token: string
  service_name: string
  environment: string
  version?: string
}

type Attributes = Record<string, string | number>

/** The two functions of @dash0/sdk-web that Debrief uses; tests pass a fake. */
export interface RumSdk {
  init(options: {
    serviceName: string
    serviceVersion?: string
    environment?: string
    endpoint: { url: string; authToken: string }
    additionalSignalAttributes?: Attributes
    propagators?: { type: 'traceparent'; match: RegExp[] }[]
    enabledInstrumentations?: InstrumentationName[]
  }): void
  sendEvent(name: string, options?: { attributes?: Attributes }): void
}

type InstrumentationName = '@dash0/navigation' | '@dash0/web-vitals' | '@dash0/error' | '@dash0/fetch' | '@dash0/xhr'

/** Everything except session recording, frustration signals and profiling (see top of file). */
export const INSTRUMENTATIONS: InstrumentationName[] = [
  '@dash0/navigation',
  '@dash0/web-vitals',
  '@dash0/error',
  '@dash0/fetch',
  '@dash0/xhr',
]

export const RUM_ATTRIBUTE = 'data-rum-event'
const EVENT_NAME = /^[a-z][a-z0-9_]{0,63}$/
const MAX_ID_CHARS = 200

type FetchFn = typeof fetch

interface ClickTarget {
  closest?(selector: string): { getAttribute(name: string): string | null } | null
}

interface EventSource {
  addEventListener(type: 'click', listener: (event: { target: unknown }) => void, capture: boolean): void
  removeEventListener(type: 'click', listener: (event: { target: unknown }) => void, capture: boolean): void
}

// HTTPS only, except a local OTLP sink for checks on a laptop.
const ENDPOINT = /^(https:\/\/|http:\/\/(localhost|127\.0\.0\.1)(:\d+)?(\/|$))/

function isRumConfig(value: unknown): value is RumConfig {
  if (!value || typeof value !== 'object') return false
  const v = value as Record<string, unknown>
  return (
    typeof v.endpoint === 'string' &&
    ENDPOINT.test(v.endpoint) &&
    typeof v.token === 'string' &&
    v.token.length > 0 &&
    typeof v.service_name === 'string' &&
    typeof v.environment === 'string'
  )
}

/** The RUM settings from GET /api/config, or null when RUM is off or the call fails. */
export async function fetchRumConfig(fetchFn: FetchFn = fetch): Promise<RumConfig | null> {
  try {
    const response = await fetchFn('/api/config')
    if (!response.ok) return null
    const body: unknown = await response.json()
    const rum = body && typeof body === 'object' && 'rum' in body ? body.rum : null
    return isRumConfig(rum) ? rum : null
  } catch {
    return null
  }
}

/**
 * The custom event for a clicked element: its `data-rum-event` name plus `rank` and `clip_id`
 * when the element has `data-rank` / `data-clip-id`. Nothing else is read from the element.
 */
export function rumEventFor(target: unknown): { name: string; attributes: Attributes } | null {
  const closest = (target as ClickTarget | null)?.closest
  if (typeof closest !== 'function') return null
  const el = closest.call(target, `[${RUM_ATTRIBUTE}]`)
  if (!el) return null
  const name = (el.getAttribute(RUM_ATTRIBUTE) ?? '').trim()
  if (!EVENT_NAME.test(name)) return null
  const attributes: Attributes = {}
  const rank = Number.parseInt(el.getAttribute('data-rank') ?? '', 10)
  if (Number.isFinite(rank)) attributes.rank = rank
  const clipId = (el.getAttribute('data-clip-id') ?? '').trim().slice(0, MAX_ID_CHARS)
  if (clipId) attributes.clip_id = clipId
  return { name, attributes }
}

/**
 * One capture-phase click listener on the document for every `[data-rum-event]` element, so
 * markup only needs the attribute. Returns a function that removes the listener.
 */
export function installClickDelegation(doc: EventSource, send: RumSdk['sendEvent']): () => void {
  const listener = (event: { target: unknown }) => {
    try {
      const rumEvent = rumEventFor(event.target)
      if (rumEvent) send(rumEvent.name, { attributes: rumEvent.attributes })
    } catch {
      // Telemetry must never break a click.
    }
  }
  doc.addEventListener('click', listener, true)
  return () => doc.removeEventListener('click', listener, true)
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/** The SDK options for a config. Exported for tests. */
export function initOptions(config: RumConfig, origin: string | undefined): Parameters<RumSdk['init']>[0] {
  const version = config.version || 'dev'
  // Same-origin requests get `traceparent` from the SDK anyway; naming /api/ makes the intent
  // explicit and keeps it if the API ever moves to its own origin.
  const apiUrls = origin ? [new RegExp(`^${escapeRegExp(origin)}/api/`), /^\/api\//] : [/^\/api\//]
  return {
    serviceName: config.service_name,
    serviceVersion: version,
    environment: config.environment,
    endpoint: { url: config.endpoint, authToken: config.token },
    additionalSignalAttributes: {
      'deployment.environment': config.environment,
      'service.version': version,
    },
    propagators: [{ type: 'traceparent', match: apiUrls }],
    enabledInstrumentations: INSTRUMENTATIONS,
  }
}

export interface RumDeps {
  fetchFn?: FetchFn
  loadSdk?: () => Promise<RumSdk>
  doc?: EventSource
  origin?: string
}

let started = false

/**
 * Start RUM if the API has a web token. Resolves to true when the SDK was initialised.
 * Never rejects, so `void initRum()` is safe before or after the first render.
 */
export async function initRum(deps: RumDeps = {}): Promise<boolean> {
  if (started) return false
  try {
    const config = await fetchRumConfig(deps.fetchFn)
    if (!config) return false
    const sdk = await (deps.loadSdk ?? (() => import('@dash0/sdk-web')))()
    if (started) return false
    sdk.init(initOptions(config, deps.origin ?? globalThis.location?.origin))
    started = true
    const doc = deps.doc ?? (typeof document === 'undefined' ? undefined : document)
    if (doc) installClickDelegation(doc, (name, options) => sdk.sendEvent(name, options))
    return true
  } catch {
    return false
  }
}

/** Tests only: forget that RUM was started. */
export function resetRumForTests(): void {
  started = false
}
