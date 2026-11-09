// A fake `fetch` that answers the Debrief API from the fixtures, so the UI works
// before the API routes are merged. Dev-only: api.ts loads it when the URL has `?mock=1`.
import type { MemoRequest, PlaylistRequest, PlaylistResponse, StackRequest } from '../api'
import { CLIP_TEXT, MEMO, PLAYLIST, STACK, TALKS, TOOLS } from './data'

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })

export function mockPlaylist(req: PlaylistRequest): PlaylistResponse {
  const wanted = new Set(req.tools)
  const attended = new Set(req.attended_talk_ids)
  let clips = PLAYLIST.clips.filter((c) => c.matched_tools.some((t) => wanted.has(t)))
  if (req.filter === 'attended') clips = clips.filter((c) => attended.has(c.talk_id))
  if (req.filter === 'missed') clips = clips.filter((c) => !attended.has(c.talk_id))
  const total_seconds = clips.reduce((sum, c) => sum + (c.end_sec - c.start_sec), 0)
  const response: PlaylistResponse = { ...PLAYLIST, clips, total_seconds }
  if (clips.length === 0) {
    const names = req.tools.map((id) => TOOLS.find((t) => t.id === id)?.name ?? id).join(', ')
    response.message =
      req.filter === 'all'
        ? `No strong matches for ${names}. No talk at this conference covers these tools in depth.`
        : `No strong matches for ${names} among the talks you ${req.filter}.`
  }
  return response
}

export const mockFetch: typeof fetch = async (input, init) => {
  const url = new URL(typeof input === 'string' ? input : input instanceof URL ? input.href : input.url, 'http://mock')
  const method = (init?.method ?? 'GET').toUpperCase()
  const body: unknown = typeof init?.body === 'string' ? JSON.parse(init.body) : undefined
  // Feel like a network so loading states show.
  await new Promise((resolve) => setTimeout(resolve, url.pathname === '/api/playlist' || url.pathname === '/api/memo' ? 700 : 250))

  if (method === 'GET' && url.pathname === '/api/tools') return json(TOOLS)
  if (method === 'GET' && url.pathname === '/api/talks') return json(TALKS)
  if (method === 'GET' && url.pathname === '/api/version') return json({ version: 'mock' })
  if (method === 'POST' && url.pathname === '/api/stack') {
    const req = body as StackRequest
    const picked = (req.tools ?? [])
      .map((id) => TOOLS.find((t) => t.id === id))
      .filter((t) => t !== undefined)
      .map((t) => ({ id: t.id, name: t.name, source: 'picked' }))
    const fromFile = req.dependency_file?.content.trim() ? STACK.tools : []
    const seen = new Set<string>()
    const tools = [...fromFile, ...picked].filter((t) => !seen.has(t.id) && seen.add(t.id))
    return json({ tools, unknown_packages: fromFile.length ? STACK.unknown_packages : [] })
  }
  if (method === 'POST' && url.pathname === '/api/playlist') return json(mockPlaylist(body as PlaylistRequest))
  if (method === 'POST' && url.pathname === '/api/memo') {
    const req = body as MemoRequest
    return json({ ...MEMO, cited_clip_ids: MEMO.cited_clip_ids.filter((id) => req.clip_ids.includes(id)) })
  }
  const clipMatch = /^\/api\/clips\/(.+)$/.exec(url.pathname)
  if (method === 'GET' && clipMatch) {
    const clipId = decodeURIComponent(clipMatch[1])
    const text = CLIP_TEXT[clipId]
    return text ? json({ clip_id: clipId, text }) : json({ detail: 'Clip not found' }, 404)
  }
  return json({ detail: `Mock has no route for ${method} ${url.pathname}` }, 404)
}
