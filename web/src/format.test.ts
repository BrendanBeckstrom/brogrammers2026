import { describe, expect, it } from 'vitest'
import {
  formatClipRange,
  formatDuration,
  formatSpeakers,
  formatTimestamp,
  matchedByText,
  playlistAnnouncement,
  playlistSummary,
  talkLabelText,
  watchMinutes,
} from './format'

describe('formatTimestamp', () => {
  it('formats seconds as mm:ss', () => {
    expect(formatTimestamp(0)).toBe('00:00')
    expect(formatTimestamp(59)).toBe('00:59')
    expect(formatTimestamp(365)).toBe('06:05')
    expect(formatTimestamp(872)).toBe('14:32')
    expect(formatTimestamp(1090)).toBe('18:10')
  })

  it('adds hours past the hour and floors fractions', () => {
    expect(formatTimestamp(3725)).toBe('1:02:05')
    expect(formatTimestamp(872.9)).toBe('14:32')
    expect(formatTimestamp(-4)).toBe('00:00')
  })

  it('formats a clip range with an en dash', () => {
    expect(formatClipRange(872, 1090)).toBe('14:32–18:10')
  })
})

describe('formatDuration', () => {
  it('says minutes and seconds', () => {
    expect(formatDuration(218)).toBe('3 min 38 s')
    expect(formatDuration(240)).toBe('4 min')
    expect(formatDuration(45)).toBe('45 s')
  })
})

describe('total watch time', () => {
  const talks = (...ids: string[]) => ids.map((talk_id) => ({ talk_id }))

  it('rounds to whole minutes and counts distinct talks', () => {
    expect(playlistSummary(1320, talks('a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i'))).toBe('22 minutes across 9 talks')
    expect(playlistSummary(1350, talks('a', 'a', 'b'))).toBe('23 minutes across 2 talks')
  })

  it('uses singular words and never says zero minutes for a real clip', () => {
    expect(playlistSummary(75, talks('a'))).toBe('1 minute across 1 talk')
    expect(playlistSummary(20, talks('a'))).toBe('1 minute across 1 talk')
    expect(watchMinutes(0)).toBe(0)
  })

  it('handles an empty playlist', () => {
    expect(playlistSummary(0, [])).toBe('No clips')
    expect(playlistAnnouncement(0, [], 'No strong matches for Foo.')).toBe('Playlist loaded: 0 clips. No strong matches for Foo.')
  })

  it('announces clips and minutes', () => {
    expect(playlistAnnouncement(1320, talks('a', 'b'))).toBe('Playlist loaded: 2 clips, 22 minutes')
    expect(playlistAnnouncement(90, talks('a'))).toBe('Playlist loaded: 1 clip, 2 minutes')
  })
})

describe('labels', () => {
  it('names talk labels in words', () => {
    expect(talkLabelText('deep_dive')).toBe('Deep dive')
    expect(talkLabelText('intro')).toBe('Introduction')
    expect(talkLabelText('pitch')).toBe('Product pitch')
    expect(talkLabelText(null)).toBeNull()
  })

  it('says how a clip was found', () => {
    expect(matchedByText('keyword')).toBe('Found by keyword')
    expect(matchedByText('semantic')).toBe('Found by semantic search')
    expect(matchedByText('both')).toBe('Found by keyword and semantic search')
  })

  it('joins speakers', () => {
    expect(formatSpeakers(['Ana Ruiz'])).toBe('Ana Ruiz')
    expect(formatSpeakers(['Ana Ruiz', 'Jonas Becker'])).toBe('Ana Ruiz and Jonas Becker')
    expect(formatSpeakers(['A', 'B', 'C'])).toBe('A, B, and C')
    expect(formatSpeakers('Ana Ruiz; Jonas Becker')).toBe('Ana Ruiz; Jonas Becker')
  })
})
