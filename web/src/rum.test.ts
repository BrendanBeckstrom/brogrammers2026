import { afterEach, describe, expect, it } from 'vitest'
import {
  INSTRUMENTATIONS,
  fetchRumConfig,
  initOptions,
  initRum,
  installClickDelegation,
  resetRumForTests,
  rumEventFor,
  type RumConfig,
  type RumSdk,
} from './rum'

const CONFIG: RumConfig = {
  endpoint: 'https://ingress.us-west-2.aws.dash0.com',
  token: 'auth_fake_web_token',
  service_name: 'debrief-web',
  environment: 'hackathon',
  version: 'abc1234',
}
const PAIN = 'our Argo CD syncs are slow and nobody knows why'
const QUOTE = 'We measured the sync loop in production'

// A minimal element tree: enough for closest('[data-rum-event]') and getAttribute.
class FakeElement {
  readonly attrs: Record<string, string>
  readonly parent: FakeElement | null
  readonly textContent: string

  constructor(attrs: Record<string, string>, parent: FakeElement | null = null, text = '') {
    this.attrs = attrs
    this.parent = parent
    this.textContent = text
  }

  getAttribute(name: string): string | null {
    return name in this.attrs ? this.attrs[name] : null
  }

  closest(selector: string): FakeElement | null {
    const attr = /^\[([a-z-]+)\]$/.exec(selector)?.[1]
    if (attr && attr in this.attrs) return this
    return this.parent?.closest(selector) ?? null
  }
}

type Listener = (event: { target: unknown }) => void

class FakeDocument {
  listeners: { listener: Listener; capture: boolean }[] = []
  addEventListener(_type: 'click', listener: Listener, capture: boolean) {
    this.listeners.push({ listener, capture })
  }
  removeEventListener(_type: 'click', listener: Listener) {
    this.listeners = this.listeners.filter((l) => l.listener !== listener)
  }
  click(target: unknown) {
    for (const { listener } of this.listeners) listener({ target })
  }
}

class FakeSdk implements RumSdk {
  inits: Parameters<RumSdk['init']>[0][] = []
  events: { name: string; options?: { attributes?: Record<string, string | number> } }[] = []
  init(options: Parameters<RumSdk['init']>[0]) {
    this.inits.push(options)
  }
  sendEvent(name: string, options?: { attributes?: Record<string, string | number> }) {
    this.events.push({ name, options })
  }
}

function jsonFetch(body: unknown, status = 200): { fetchFn: typeof fetch; calls: string[] } {
  const calls: string[] = []
  const fetchFn = (async (input: RequestInfo | URL) => {
    calls.push(String(input))
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
  }) as typeof fetch
  return { fetchFn, calls }
}

afterEach(() => resetRumForTests())

describe('rumEventFor (attribute mapping)', () => {
  it('maps watch_click with data-rank and data-clip-id to rank and clip_id', () => {
    const card = new FakeElement({}, null, QUOTE)
    const link = new FakeElement(
      { 'data-rum-event': 'watch_click', 'data-rank': '3', 'data-clip-id': 'abc123-872~932' },
      card,
      'Watch',
    )
    const icon = new FakeElement({}, link)
    expect(rumEventFor(icon)).toEqual({
      name: 'watch_click',
      attributes: { rank: 3, clip_id: 'abc123-872~932' },
    })
  })

  it('sends only the name for elements without rank or clip id', () => {
    for (const name of ['memo_copy', 'memo_make', 'playlist_build']) {
      expect(rumEventFor(new FakeElement({ 'data-rum-event': name }, null, PAIN))).toEqual({
        name,
        attributes: {},
      })
    }
  })

  it('ignores clicks outside marked elements, bad names and non-numeric ranks', () => {
    expect(rumEventFor(new FakeElement({}, null, QUOTE))).toBeNull()
    expect(rumEventFor(null)).toBeNull()
    expect(rumEventFor({})).toBeNull()
    expect(rumEventFor(new FakeElement({ 'data-rum-event': 'Bad Name!' }))).toBeNull()
    expect(rumEventFor(new FakeElement({ 'data-rum-event': 'watch_click', 'data-rank': 'top' }))).toEqual({
      name: 'watch_click',
      attributes: {},
    })
  })
})

describe('installClickDelegation', () => {
  it('sends one custom event per marked click, with no element text', () => {
    const doc = new FakeDocument()
    const sdk = new FakeSdk()
    const uninstall = installClickDelegation(doc, (n, o) => sdk.sendEvent(n, o))
    expect(doc.listeners).toHaveLength(1)
    expect(doc.listeners[0].capture).toBe(true)

    const card = new FakeElement({}, null, QUOTE)
    doc.click(new FakeElement({ 'data-rum-event': 'watch_click', 'data-rank': '1', 'data-clip-id': 'c1' }, card, QUOTE))
    doc.click(new FakeElement({ 'data-rum-event': 'playlist_build' }, null, PAIN))
    doc.click(new FakeElement({}, null, QUOTE)) // unmarked: nothing sent
    expect(sdk.events).toEqual([
      { name: 'watch_click', options: { attributes: { rank: 1, clip_id: 'c1' } } },
      { name: 'playlist_build', options: { attributes: {} } },
    ])
    const sent = JSON.stringify(sdk.events)
    expect(sent).not.toContain(QUOTE)
    expect(sent).not.toContain(PAIN)

    uninstall()
    doc.click(new FakeElement({ 'data-rum-event': 'memo_copy' }))
    expect(sdk.events).toHaveLength(2)
  })

  it('never lets a failing SDK break the click', () => {
    const doc = new FakeDocument()
    installClickDelegation(doc, () => {
      throw new Error('sdk down')
    })
    expect(() => doc.click(new FakeElement({ 'data-rum-event': 'memo_make' }))).not.toThrow()
  })
})

describe('fetchRumConfig', () => {
  it('returns the rum block, or null when it is off, malformed or unreachable', async () => {
    const on = jsonFetch({ rum: CONFIG })
    expect(await fetchRumConfig(on.fetchFn)).toEqual(CONFIG)
    expect(on.calls).toEqual(['/api/config'])
    expect(await fetchRumConfig(jsonFetch({ rum: null }).fetchFn)).toBeNull()
    expect(await fetchRumConfig(jsonFetch({ rum: { ...CONFIG, token: '' } }).fetchFn)).toBeNull()
    expect(await fetchRumConfig(jsonFetch({ rum: { ...CONFIG, endpoint: 'http://evil' } }).fetchFn)).toBeNull()
    expect(await fetchRumConfig(jsonFetch({ rum: { ...CONFIG, endpoint: 'http://localhost.evil.com' } }).fetchFn)).toBeNull()
    const sink = { ...CONFIG, endpoint: 'http://127.0.0.1:4319' }
    expect(await fetchRumConfig(jsonFetch({ rum: sink }).fetchFn)).toEqual(sink)
    expect(await fetchRumConfig(jsonFetch({}, 500).fetchFn)).toBeNull()
    const down = (async () => {
      throw new TypeError('network')
    }) as typeof fetch
    expect(await fetchRumConfig(down)).toBeNull()
  })
})

describe('initRum', () => {
  it('does nothing, and never loads the SDK, when rum is null', async () => {
    let loaded = false
    const doc = new FakeDocument()
    const ok = await initRum({
      fetchFn: jsonFetch({ rum: null }).fetchFn,
      loadSdk: async () => {
        loaded = true
        return new FakeSdk()
      },
      doc,
    })
    expect(ok).toBe(false)
    expect(loaded).toBe(false)
    expect(doc.listeners).toHaveLength(0)
  })

  it('initialises the SDK once and wires click delegation to sendEvent', async () => {
    const sdk = new FakeSdk()
    const doc = new FakeDocument()
    const deps = {
      fetchFn: jsonFetch({ rum: CONFIG }).fetchFn,
      loadSdk: async () => sdk,
      doc,
      origin: 'https://brogrammers.cnh.radarhq.io',
    }
    expect(await initRum(deps)).toBe(true)
    expect(await initRum(deps)).toBe(false) // second call is a no-op
    expect(sdk.inits).toHaveLength(1)
    const options = sdk.inits[0]
    expect(options.serviceName).toBe('debrief-web')
    expect(options.endpoint).toEqual({ url: CONFIG.endpoint, authToken: CONFIG.token })
    expect(options.additionalSignalAttributes).toEqual({
      'deployment.environment': 'hackathon',
      'service.version': 'abc1234',
    })
    expect(options.enabledInstrumentations).toEqual(INSTRUMENTATIONS)

    doc.click(new FakeElement({ 'data-rum-event': 'memo_copy' }))
    expect(sdk.events).toEqual([{ name: 'memo_copy', options: { attributes: {} } }])
  })

  it('resolves false instead of throwing when the SDK fails to load or init', async () => {
    const fetchFn = jsonFetch({ rum: CONFIG }).fetchFn
    await expect(
      initRum({ fetchFn, loadSdk: async () => Promise.reject(new Error('chunk 404')), doc: new FakeDocument() }),
    ).resolves.toBe(false)
    const broken = new FakeSdk()
    broken.init = () => {
      throw new Error('bad options')
    }
    await expect(initRum({ fetchFn, loadSdk: async () => broken, doc: new FakeDocument() })).resolves.toBe(false)
  })
})

describe('initOptions', () => {
  it('propagates traceparent to same-origin /api/* only, and keeps replay and rage clicks off', () => {
    const options = initOptions(CONFIG, 'https://brogrammers.cnh.radarhq.io')
    const [propagator] = options.propagators ?? []
    expect(propagator.type).toBe('traceparent')
    const matches = (url: string) => propagator.match.some((re) => re.test(url))
    expect(matches('https://brogrammers.cnh.radarhq.io/api/playlist')).toBe(true)
    expect(matches('/api/memo')).toBe(true)
    expect(matches('https://www.youtube.com/watch?v=x')).toBe(false)
    expect(matches('https://brogrammers.cnh.radarhq.io.evil.com/api/x')).toBe(false)
    expect(options.enabledInstrumentations).not.toContain('@dash0/session-recording')
    expect(options.enabledInstrumentations).not.toContain('@dash0/frustration-signals')
    expect(initOptions({ ...CONFIG, version: undefined }, undefined).serviceVersion).toBe('dev')
  })
})
