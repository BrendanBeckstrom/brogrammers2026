// Pure formatting helpers for clip times, watch-time totals and labels.
import type { Clip, MatchedBy, TalkLabel } from './api'

const pad2 = (n: number): string => String(n).padStart(2, '0')

/** Build a YouTube URL that opens at the clip's first second. */
export function watchUrl(talkId: string, startSec: number): string {
  const url = new URL('https://www.youtube.com/watch')
  url.searchParams.set('v', talkId)
  url.searchParams.set('t', `${startSec}s`)
  return url.toString()
}

/** 872 -> "14:32"; past an hour, 3725 -> "1:02:05". */
export function formatTimestamp(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds))
  const hours = Math.floor(s / 3600)
  const minutes = Math.floor((s % 3600) / 60)
  const seconds = s % 60
  if (hours > 0) return `${hours}:${pad2(minutes)}:${pad2(seconds)}`
  return `${pad2(minutes)}:${pad2(seconds)}`
}

/** "14:32–18:10" (en dash). */
export function formatClipRange(startSec: number, endSec: number): string {
  return `${formatTimestamp(startSec)}–${formatTimestamp(endSec)}`
}

/** 218 -> "3 min 38 s", 45 -> "45 s", 240 -> "4 min". */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.round(totalSeconds))
  const minutes = Math.floor(s / 60)
  const seconds = s % 60
  if (minutes === 0) return `${seconds} s`
  if (seconds === 0) return `${minutes} min`
  return `${minutes} min ${seconds} s`
}

export function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

/** Whole minutes of watch time; any non-zero time counts as at least one minute. */
export function watchMinutes(totalSeconds: number): number {
  if (totalSeconds <= 0) return 0
  return Math.max(1, Math.round(totalSeconds / 60))
}

export function countTalks(clips: Pick<Clip, 'talk_id'>[]): number {
  return new Set(clips.map((clip) => clip.talk_id)).size
}

/** "22 minutes across 9 talks". */
export function playlistSummary(totalSeconds: number, clips: Pick<Clip, 'talk_id'>[]): string {
  if (clips.length === 0) return 'No clips'
  return `${plural(watchMinutes(totalSeconds), 'minute')} across ${plural(countTalks(clips), 'talk')}`
}

/** What the live region says when a playlist arrives. */
export function playlistAnnouncement(totalSeconds: number, clips: Pick<Clip, 'talk_id'>[], message?: string): string {
  if (clips.length === 0) return `Playlist loaded: 0 clips.${message ? ` ${message}` : ''}`
  return `Playlist loaded: ${plural(clips.length, 'clip')}, ${plural(watchMinutes(totalSeconds), 'minute')}`
}

const LABELS: Record<TalkLabel, string> = {
  deep_dive: 'Deep dive',
  intro: 'Introduction',
  pitch: 'Product pitch',
}

export function talkLabelText(label: TalkLabel | null | undefined): string | null {
  return label ? (LABELS[label] ?? null) : null
}

const MATCHED_BY: Record<MatchedBy, string> = {
  keyword: 'keyword',
  semantic: 'semantic search',
  both: 'keyword and semantic search',
}

export function matchedByText(matchedBy: MatchedBy | null | undefined): string | null {
  return matchedBy ? `Found by ${MATCHED_BY[matchedBy] ?? matchedBy}` : null
}

const listFormat = new Intl.ListFormat('en', { style: 'long', type: 'conjunction' })

/** ["A", "B", "C"] -> "A, B, and C". Accepts a plain string too, in case the API sends one. */
export function formatSpeakers(speakers: string[] | string | null | undefined): string {
  if (!speakers) return ''
  if (typeof speakers === 'string') return speakers
  return listFormat.format(speakers.filter(Boolean))
}
