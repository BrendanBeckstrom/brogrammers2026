// Every call the web app makes to the Debrief API (contracts: docs/BRIEF.md "API").
// Each function takes an optional fetch so tests can record requests without a network.
// In dev (`npm run dev`), add `?mock=1` to the URL to answer from web/src/fixtures instead.

export interface Tool {
  id: string
  name: string
  chunk_count: number
}

export interface DependencyFile {
  name: string
  content: string
}

export interface StackRequest {
  tools?: string[]
  dependency_file?: DependencyFile
}

export interface StackTool {
  id: string
  name: string
  source: string
}

export interface StackResponse {
  tools: StackTool[]
  unknown_packages: string[]
}

export interface Talk {
  talk_id: string
  title: string
  speakers: string[]
}

export type PlaylistFilter = 'all' | 'attended' | 'missed'
export type MatchedBy = 'keyword' | 'semantic' | 'both'
export type TalkLabel = 'deep_dive' | 'intro' | 'pitch'

export interface Clip {
  clip_id: string
  talk_id: string
  title: string
  speakers: string[]
  start_sec: number
  end_sec: number
  watch_url: string
  quote: string
  why: string
  matched_tools: string[]
  matched_by: MatchedBy
  talk_label: TalkLabel | null
  score: number
}

export interface PlaylistRequest {
  tools: string[]
  pain: string
  attended_talk_ids: string[]
  filter: PlaylistFilter
  budget_minutes: number
  rerank: boolean
}

export interface PlaylistDebug {
  query: unknown
  timings_ms: Record<string, number>
}

/** A significant term Elasticsearch found in the clips that match the stack. */
export interface SignificantTerm {
  term: string
  score: number
  doc_count: number
}

export interface PlaylistResponse {
  total_seconds: number
  clips: Clip[]
  trace_id: string
  message?: string
  debug?: PlaylistDebug
  /** "What this conference said about your stack": optional, absent on older APIs. */
  terms?: SignificantTerm[]
}

/** POST /api/playlists: save a playlist so it can be shared by link. */
export interface SavePlaylistRequest {
  request: PlaylistRequest
  clip_ids: string[]
}

export interface SavePlaylistResponse {
  id: string
  url: string
}

/** GET /api/playlists/{id}: a saved playlist, rendered as-is. */
export interface SharedPlaylist {
  request: PlaylistRequest
  response: PlaylistResponse
  created_at: string
}

export interface ClipText {
  clip_id: string
  text: string
  /** The captions with misheard tool names fixed (e.g. Psyllium -> Cilium); absent on older servers. */
  text_corrected?: string
  corrections?: { from: string; to: string }[]
}

export interface MemoRequest {
  tools: string[]
  clip_ids: string[]
}

export interface MemoResponse {
  markdown: string
  cited_clip_ids: string[]
}

export const BUDGET_MINUTES = 25

/** The dependency file names the stack parser understands. */
export const DEPENDENCY_FILE_TYPES = ['go.mod', 'package.json', 'requirements.txt', 'Chart.yaml'] as const

/** Map a picked file's name to one of the four names the stack parser understands. */
export function dependencyFileName(name: string): string {
  const base = name.split(/[\\/]/).pop() ?? name
  if ((DEPENDENCY_FILE_TYPES as readonly string[]).includes(base)) return base
  const lower = base.toLowerCase()
  if (lower.endsWith('.mod')) return 'go.mod'
  if (lower.endsWith('.json')) return 'package.json'
  if (lower.endsWith('.txt')) return 'requirements.txt'
  if (lower.endsWith('.yaml') || lower.endsWith('.yml')) return 'Chart.yaml'
  return 'go.mod'
}

export class ApiError extends Error {
  readonly status: number

  constructor(message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.status = status
  }
}

type FetchFn = typeof fetch

function mockRequested(): boolean {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).get('mock') === '1'
}

async function defaultFetch(): Promise<FetchFn> {
  // `import.meta.env.DEV` is a build-time constant, so production builds drop the
  // fixtures and the mock entirely.
  if (import.meta.env.DEV && mockRequested()) {
    const { mockFetch } = await import('./fixtures/mockFetch')
    return mockFetch
  }
  return fetch
}

/** `fetch`, or the fixture-backed mock in dev with `?mock=1`. For callers outside this module. */
export const apiFetch: FetchFn = async (input, init) => (await defaultFetch())(input, init)

async function errorDetail(response: Response): Promise<string | null> {
  try {
    const body: unknown = await response.json()
    if (body && typeof body === 'object' && 'detail' in body && typeof body.detail === 'string') {
      return body.detail
    }
  } catch {
    // Not JSON: fall through to the status message.
  }
  return null
}

async function request<T>(path: string, init: RequestInit | undefined, fetchFn?: FetchFn): Promise<T> {
  const doFetch = fetchFn ?? (await defaultFetch())
  let response: Response
  try {
    response = await doFetch(path, init)
  } catch {
    throw new ApiError('Could not reach the Debrief API. Check your connection and try again.', 0)
  }
  if (!response.ok) {
    const detail = await errorDetail(response)
    throw new ApiError(detail ?? `The Debrief API returned an error (${response.status}).`, response.status)
  }
  return (await response.json()) as T
}

function postJson(body: unknown): RequestInit {
  return {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }
}

export function getTools(fetchFn?: FetchFn): Promise<Tool[]> {
  return request<Tool[]>('/api/tools', undefined, fetchFn)
}

export function postStack(body: StackRequest, fetchFn?: FetchFn): Promise<StackResponse> {
  return request<StackResponse>('/api/stack', postJson(body), fetchFn)
}

export function getTalks(fetchFn?: FetchFn): Promise<Talk[]> {
  return request<Talk[]>('/api/talks', undefined, fetchFn)
}

export function buildPlaylistRequest(input: {
  tools: string[]
  pain?: string
  attended_talk_ids?: string[]
  filter?: PlaylistFilter
}): PlaylistRequest {
  return {
    tools: input.tools,
    pain: (input.pain ?? '').trim(),
    attended_talk_ids: input.attended_talk_ids ?? [],
    filter: input.filter ?? 'all',
    budget_minutes: BUDGET_MINUTES,
    rerank: true,
  }
}

export function postPlaylist(body: PlaylistRequest, fetchFn?: FetchFn): Promise<PlaylistResponse> {
  return request<PlaylistResponse>('/api/playlist', postJson(body), fetchFn)
}

export function getClipText(clipId: string, fetchFn?: FetchFn): Promise<ClipText> {
  return request<ClipText>(`/api/clips/${encodeURIComponent(clipId)}`, undefined, fetchFn)
}

export function postMemo(body: MemoRequest, fetchFn?: FetchFn): Promise<MemoResponse> {
  return request<MemoResponse>('/api/memo', postJson(body), fetchFn)
}

export function savePlaylist(body: SavePlaylistRequest, fetchFn?: FetchFn): Promise<SavePlaylistResponse> {
  return request<SavePlaylistResponse>('/api/playlists', postJson(body), fetchFn)
}

export function getSharedPlaylist(id: string, fetchFn?: FetchFn): Promise<SharedPlaylist> {
  return request<SharedPlaylist>(`/api/playlists/${encodeURIComponent(id)}`, undefined, fetchFn)
}

/** The link a shared playlist lives at: `https://<host>/?p=<id>`. */
export function shareLink(origin: string, id: string): string {
  return `${origin.replace(/\/+$/, '')}/?p=${encodeURIComponent(id)}`
}

/** The shared playlist id in a page URL's query string (`?p=<id>`), if any. */
export function sharedPlaylistId(search: string): string | null {
  const id = new URLSearchParams(search).get('p')?.trim()
  return id ? id : null
}

/** Up to `max` significant terms, strongest first; [] when the API sent none. */
export function topTerms(terms: SignificantTerm[] | null | undefined, max = 8): SignificantTerm[] {
  if (!Array.isArray(terms)) return []
  return terms
    .filter((t) => t && typeof t.term === 'string' && t.term.trim())
    .slice()
    .sort((a, b) => b.score - a.score)
    .slice(0, max)
}

export function errorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message
  if (error instanceof Error) return error.message
  return 'Something went wrong.'
}
