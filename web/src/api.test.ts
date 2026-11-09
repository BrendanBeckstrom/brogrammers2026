import { describe, expect, it } from 'vitest'
import {
  ApiError,
  buildPlaylistRequest,
  dependencyFileName,
  getClipText,
  getSharedPlaylist,
  getTalks,
  getTools,
  postMemo,
  postPlaylist,
  postStack,
  savePlaylist,
  shareLink,
  sharedPlaylistId,
  topTerms,
} from './api'

interface Call {
  url: string
  method: string
  contentType: string | null
  body: unknown
}

/** A fake fetch that records each request and answers with `body`. */
function recorder(responseBody: unknown, status = 200) {
  const calls: Call[] = []
  const fetchFn: typeof fetch = async (input, init) => {
    calls.push({
      url: String(input),
      method: init?.method ?? 'GET',
      contentType: new Headers(init?.headers).get('Content-Type'),
      body: typeof init?.body === 'string' ? JSON.parse(init.body) : undefined,
    })
    return new Response(JSON.stringify(responseBody), { status })
  }
  return { calls, fetchFn }
}

describe('api request shapes', () => {
  it('GET /api/tools and /api/talks', async () => {
    const tools = recorder([{ id: 'argo-cd', name: 'Argo CD', chunk_count: 12 }])
    expect(await getTools(tools.fetchFn)).toEqual([{ id: 'argo-cd', name: 'Argo CD', chunk_count: 12 }])
    expect(tools.calls).toEqual([{ url: '/api/tools', method: 'GET', contentType: null, body: undefined }])

    const talks = recorder([])
    await getTalks(talks.fetchFn)
    expect(talks.calls[0]).toMatchObject({ url: '/api/talks', method: 'GET' })
  })

  it('POST /api/stack with tools and a dependency file', async () => {
    const { calls, fetchFn } = recorder({ tools: [], unknown_packages: [] })
    await postStack({ tools: ['istio'], dependency_file: { name: 'go.mod', content: 'module x' } }, fetchFn)
    expect(calls[0]).toEqual({
      url: '/api/stack',
      method: 'POST',
      contentType: 'application/json',
      body: { tools: ['istio'], dependency_file: { name: 'go.mod', content: 'module x' } },
    })
  })

  it('POST /api/playlist with the full contract', async () => {
    const { calls, fetchFn } = recorder({ total_seconds: 0, clips: [], trace_id: 't' })
    const request = buildPlaylistRequest({
      tools: ['argo-cd', 'prometheus'],
      pain: '  our Argo CD syncs are slow ',
      attended_talk_ids: ['abc'],
      filter: 'missed',
    })
    await postPlaylist(request, fetchFn)
    expect(calls[0].url).toBe('/api/playlist')
    expect(calls[0].method).toBe('POST')
    expect(calls[0].body).toEqual({
      tools: ['argo-cd', 'prometheus'],
      pain: 'our Argo CD syncs are slow',
      attended_talk_ids: ['abc'],
      filter: 'missed',
      budget_minutes: 25,
      rerank: true,
    })
  })

  it('defaults optional playlist fields', () => {
    expect(buildPlaylistRequest({ tools: ['istio'] })).toEqual({
      tools: ['istio'],
      pain: '',
      attended_talk_ids: [],
      filter: 'all',
      budget_minutes: 25,
      rerank: true,
    })
  })

  it('GET /api/clips/{id} escapes the id', async () => {
    const { calls, fetchFn } = recorder({ clip_id: 'a/b-1', text: 'hi' })
    expect(await getClipText('a/b-1', fetchFn)).toEqual({ clip_id: 'a/b-1', text: 'hi' })
    expect(calls[0]).toMatchObject({ url: '/api/clips/a%2Fb-1', method: 'GET' })
  })

  it('POST /api/memo with tools and clip ids only', async () => {
    const { calls, fetchFn } = recorder({ markdown: '# Memo', cited_clip_ids: ['c1'] })
    await postMemo({ tools: ['argo-cd'], clip_ids: ['c1', 'c2'] }, fetchFn)
    expect(calls[0]).toEqual({
      url: '/api/memo',
      method: 'POST',
      contentType: 'application/json',
      body: { tools: ['argo-cd'], clip_ids: ['c1', 'c2'] },
    })
  })
})

describe('share links', () => {
  it('POST /api/playlists with the request and clip ids', async () => {
    const { calls, fetchFn } = recorder({ id: 'k3x9', url: '/?p=k3x9' })
    const request = buildPlaylistRequest({ tools: ['argo-cd'], pain: 'slow syncs' })
    expect(await savePlaylist({ request, clip_ids: ['c1', 'c2'] }, fetchFn)).toEqual({ id: 'k3x9', url: '/?p=k3x9' })
    expect(calls[0]).toEqual({
      url: '/api/playlists',
      method: 'POST',
      contentType: 'application/json',
      body: { request, clip_ids: ['c1', 'c2'] },
    })
  })

  it('GET /api/playlists/{id} escapes the id and surfaces 404s', async () => {
    const ok = recorder({ request: {}, response: {}, created_at: '2026-11-09T19:30:00Z' })
    await getSharedPlaylist('a/b', ok.fetchFn)
    expect(ok.calls[0]).toMatchObject({ url: '/api/playlists/a%2Fb', method: 'GET' })

    const missing = recorder({ detail: 'Playlist not found' }, 404)
    const error = await getSharedPlaylist('nope', missing.fetchFn).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(404)
  })

  it('builds and reads ?p=<id> links', () => {
    expect(shareLink('https://brogrammers.cnh.radarhq.io', 'k3x9')).toBe('https://brogrammers.cnh.radarhq.io/?p=k3x9')
    expect(shareLink('http://localhost:5173/', 'a b')).toBe('http://localhost:5173/?p=a%20b')
    expect(sharedPlaylistId('?p=k3x9&mock=1')).toBe('k3x9')
    expect(sharedPlaylistId('?mock=1')).toBeNull()
    expect(sharedPlaylistId('?p=%20')).toBeNull()
  })
})

describe('significant terms', () => {
  it('keeps the strongest eight and tolerates a missing field', () => {
    const terms = Array.from({ length: 10 }, (_, i) => ({ term: `t${i}`, score: i / 10, doc_count: i }))
    expect(topTerms(terms).map((t) => t.term)).toEqual(['t9', 't8', 't7', 't6', 't5', 't4', 't3', 't2'])
    expect(topTerms(undefined)).toEqual([])
    expect(topTerms([{ term: ' ', score: 1, doc_count: 1 }])).toEqual([])
  })
})

describe('api errors', () => {
  it('surfaces FastAPI detail messages', async () => {
    const { fetchFn } = recorder({ detail: 'Elastic is unavailable' }, 503)
    const error = await getTools(fetchFn).catch((e: unknown) => e)
    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(503)
    expect((error as ApiError).message).toBe('Elastic is unavailable')
  })

  it('falls back to the status when the body is not JSON', async () => {
    const fetchFn: typeof fetch = async () => new Response('oops', { status: 500 })
    await expect(getTools(fetchFn)).rejects.toThrow('The Debrief API returned an error (500).')
  })

  it('reports network failures', async () => {
    const fetchFn: typeof fetch = async () => Promise.reject(new TypeError('Failed to fetch'))
    const error = await getTools(fetchFn).catch((e: unknown) => e)
    expect((error as ApiError).status).toBe(0)
    expect((error as ApiError).message).toMatch(/Could not reach the Debrief API/)
  })
})

describe('dependencyFileName', () => {
  it('keeps the four names the parser knows and maps others by extension', () => {
    expect(dependencyFileName('go.mod')).toBe('go.mod')
    expect(dependencyFileName('Chart.yaml')).toBe('Chart.yaml')
    expect(dependencyFileName('requirements-dev.txt')).toBe('requirements.txt')
    expect(dependencyFileName('C:\\code\\package.json')).toBe('package.json')
    expect(dependencyFileName('chart.yml')).toBe('Chart.yaml')
  })
})
