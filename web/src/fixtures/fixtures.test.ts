import { describe, expect, it } from 'vitest'
import { ApiError, buildPlaylistRequest, getSharedPlaylist, postMemo, postPlaylist, savePlaylist } from '../api'
import { playlistAnnouncement, playlistSummary, watchUrl } from '../format'
import { parseMarkdown } from '../markdown'
import { MEMO, PLAYLIST, SHARED_DEMO } from './data'
import { mockFetch, mockPlaylist } from './mockFetch'

describe('playlist fixture', () => {
  it('summarises as the product promises', () => {
    expect(playlistSummary(PLAYLIST.total_seconds, PLAYLIST.clips)).toBe('22 minutes across 9 talks')
    expect(playlistAnnouncement(PLAYLIST.total_seconds, PLAYLIST.clips)).toBe('Playlist loaded: 9 clips, 22 minutes')
  })

  it('builds watch URLs with the clip start time', () => {
    expect(watchUrl('demo-argo-01', 872)).toBe('https://www.youtube.com/watch?v=demo-argo-01&t=872s')
  })

  it('keeps the clip contract', () => {
    for (const clip of PLAYLIST.clips) {
      const length = clip.end_sec - clip.start_sec
      expect(length).toBeGreaterThanOrEqual(60)
      expect(length).toBeLessThanOrEqual(240)
      expect(clip.watch_url).toBe(watchUrl(clip.talk_id, clip.start_sec))
      expect(clip.matched_tools.length).toBeGreaterThan(0)
    }
  })

  it('filters attended and missed talks in the mock', () => {
    const attended = ['demo-argo-01']
    const base = { tools: ['argo-cd', 'prometheus', 'opentelemetry', 'istio'], attended_talk_ids: attended }
    const missed = mockPlaylist(buildPlaylistRequest({ ...base, filter: 'missed' }))
    expect(missed.clips.some((c) => attended.includes(c.talk_id))).toBe(false)
    const only = mockPlaylist(buildPlaylistRequest({ ...base, filter: 'attended' }))
    expect(only.clips.map((c) => c.talk_id)).toEqual(attended)
    expect(playlistSummary(only.total_seconds, only.clips)).toBe('4 minutes across 1 talk')
  })

  it('returns zero clips and a message for a stack with no coverage', async () => {
    const response = await postPlaylist(buildPlaylistRequest({ tools: ['cobol'] }), mockFetch)
    expect(response.clips).toEqual([])
    expect(response.message).toMatch(/^No strong matches for cobol/)
  })
})

describe('memo fixture', () => {
  it('renders headings, numbered takeaways and clip links', async () => {
    const memo = await postMemo({ tools: ['argo-cd'], clip_ids: PLAYLIST.clips.map((c) => c.clip_id) }, mockFetch)
    expect(memo.markdown).toBe(MEMO.markdown)
    const blocks = parseMarkdown(memo.markdown)
    expect(blocks.filter((b) => b.kind === 'heading')).toHaveLength(4)
    const takeaways = blocks.find((b) => b.kind === 'list' && b.ordered)
    expect(takeaways?.kind === 'list' && takeaways.items).toHaveLength(3)
    const links = JSON.stringify(blocks).match(/"kind":"link","href":"https:\/\/www\.youtube\.com\/watch\?v=demo-[^"]+"/g)
    expect(links?.length).toBeGreaterThanOrEqual(6)
  })
})

describe('share and terms fixtures', () => {
  it('sends significant terms with a playlist and drops them when it is empty', async () => {
    const full = await postPlaylist(buildPlaylistRequest({ tools: ['argo-cd'] }), mockFetch)
    expect(full.terms?.length).toBeGreaterThan(8)
    const empty = await postPlaylist(buildPlaylistRequest({ tools: ['cobol'] }), mockFetch)
    expect(empty.terms).toBeUndefined()
  })

  it('saves a playlist and loads it back by id', async () => {
    const request = buildPlaylistRequest({ tools: ['istio'] })
    const saved = await savePlaylist({ request, clip_ids: ['x'] }, mockFetch)
    expect(saved.url).toBe(`/?p=${saved.id}`)
    const loaded = await getSharedPlaylist(saved.id, mockFetch)
    expect(loaded.request).toEqual(request)
    expect(loaded.response.clips.every((c) => c.matched_tools.includes('istio'))).toBe(true)
  })

  it('serves the demo playlist and 404s unknown ids', async () => {
    expect(await getSharedPlaylist('demo', mockFetch)).toEqual(SHARED_DEMO)
    const error = await getSharedPlaylist('missing', mockFetch).catch((e: unknown) => e)
    expect((error as ApiError).status).toBe(404)
  })
})
