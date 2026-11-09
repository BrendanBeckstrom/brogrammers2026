import { useId, useMemo, useState } from 'react'
import type { PlaylistFilter, PlaylistRequest, PlaylistResponse } from '../api'
import { playlistSummary } from '../format'
import { btnSecondary, errorNotice } from '../ui'
import { ClipCard } from './ClipCard'
import { ShareButton } from './ShareButton'
import { Spinner } from './Spinner'
import { Terms } from './Terms'

const FILTERS: { value: PlaylistFilter; label: string }[] = [
  { value: 'all', label: 'All talks' },
  { value: 'attended', label: 'Talks we attended' },
  { value: 'missed', label: 'Talks we missed' },
]

interface Props {
  playlist: PlaylistResponse
  request: PlaylistRequest
  sharedRequest: PlaylistRequest
  updating: boolean
  error: string | null
  toolNames: Map<string, string>
  /** When the playlist came from a share link: when it was saved. */
  sharedAt?: string | null
  onFilterChange: (filter: PlaylistFilter) => void
}

const savedDate = new Intl.DateTimeFormat('en', { dateStyle: 'medium' })

function formatSaved(iso: string): string | null {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : savedDate.format(date)
}

export function Playlist({
  playlist,
  request,
  sharedRequest,
  updating,
  error,
  toolNames,
  sharedAt,
  onFilterChange,
}: Props) {
  const filterHint = useId()
  const hoodId = useId()
  const [hoodOpen, setHoodOpen] = useState(false)
  const noAttended = request.attended_talk_ids.length === 0
  const summary = playlistSummary(playlist.total_seconds, playlist.clips)
  const debugQuery = useMemo(
    () => (playlist.debug ? JSON.stringify(playlist.debug.query, null, 2) : null),
    [playlist.debug],
  )
  const hasClips = playlist.clips.length > 0
  const saved = sharedAt ? formatSaved(sharedAt) : null

  return (
    <div className="space-y-6" aria-busy={updating}>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          {sharedAt ? (
            <p className="mb-2 inline-flex items-center gap-1.5 rounded-full border border-line-strong bg-surface-2 px-3 py-1 text-xs font-semibold text-ink">
              Shared playlist{saved ? ` · saved ${saved}` : ''}
            </p>
          ) : null}
          <p className="font-display text-2xl font-semibold text-ink sm:text-3xl">
            {hasClips ? summary : 'Nothing to watch yet'}
          </p>
          {updating ? (
            <p className="mt-1 flex items-center gap-2 text-sm text-ink-2">
              <Spinner /> Updating…
            </p>
          ) : null}
        </div>
        {hasClips ? <ShareButton request={sharedRequest} clipIds={playlist.clips.map((c) => c.clip_id)} /> : null}
      </div>

      {hasClips ? <Terms terms={playlist.terms} /> : null}

      <fieldset aria-describedby={noAttended ? filterHint : undefined}>
        <legend className="text-sm font-semibold text-ink">Show</legend>
        <div className="mt-2 inline-flex max-w-full flex-wrap gap-1 rounded-2xl border border-line bg-surface-2 p-1">
          {FILTERS.map((f) => {
            const disabled = f.value !== 'all' && noAttended
            const checked = request.filter === f.value
            return (
              <label
                key={f.value}
                className={`flex items-center gap-2 rounded-xl border px-3 py-1.5 text-sm ${
                  checked ? 'border-brand bg-surface font-semibold text-ink shadow-sm' : 'border-transparent text-ink'
                } ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-surface'}`}
              >
                <input
                  type="radio"
                  name="playlist-filter"
                  value={f.value}
                  checked={checked}
                  disabled={disabled}
                  onChange={() => onFilterChange(f.value)}
                  className="size-4 accent-[var(--brand)]"
                />
                {f.label}
              </label>
            )
          })}
        </div>
        {noAttended ? (
          <p id={filterHint} className="mt-2 text-sm text-ink-2">
            To filter by attendance, tick the talks you attended above and build again.
          </p>
        ) : null}
      </fieldset>

      {error ? <p className={errorNotice}>{error}</p> : null}

      {!hasClips ? (
        <div className="rounded-2xl border border-dashed border-line-strong bg-surface-2 p-6 text-center sm:p-8">
          <svg aria-hidden="true" viewBox="0 0 64 64" className="mx-auto size-14 text-ink-2">
            <rect x="10" y="14" width="44" height="36" rx="8" fill="none" stroke="currentColor" strokeWidth="3" />
            <path d="M20 26h24M20 34h14" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
            <circle cx="46" cy="44" r="9" fill="var(--canvas)" stroke="var(--accent)" strokeWidth="3" />
            <path d="m52.5 50.5 5 5" stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
          </svg>
          <p className="mt-3 font-semibold text-ink">No clips for this stack.</p>
          <p className="mx-auto mt-1 max-w-prose text-ink-2">
            {playlist.message ?? 'No strong matches for these tools. Try adding more of your stack.'}
          </p>
          <p className="mt-3 text-sm text-ink-2">
            Debrief would rather show nothing than weak matches. Add more of your stack, or try an{' '}
            <a href="#hero-heading" className="font-medium text-brand-text underline underline-offset-2">
              example stack
            </a>
            .
          </p>
        </div>
      ) : (
        <ol className={`space-y-4 motion-safe:transition-opacity ${updating ? 'opacity-60' : ''}`}>
          {playlist.clips.map((clip, i) => (
            <li key={clip.clip_id}>
              <ClipCard clip={clip} index={i} toolNames={toolNames} />
            </li>
          ))}
        </ol>
      )}

      <div>
        <button
          type="button"
          aria-expanded={hoodOpen}
          aria-controls={hoodId}
          onClick={() => setHoodOpen((v) => !v)}
          className={btnSecondary}
        >
          <span aria-hidden="true">{hoodOpen ? '▾' : '▸'}</span>
          Under the hood
        </button>
        <div id={hoodId} hidden={!hoodOpen} className="mt-3 space-y-4 rounded-2xl border border-line bg-surface-2 p-4 text-sm">
          <div>
            <h3 className="font-semibold text-ink">Trace id</h3>
            <p className="mt-1 font-mono break-all text-ink">{playlist.trace_id || 'none'}</p>
          </div>
          {playlist.debug ? (
            <>
              <div>
                <h3 className="font-semibold text-ink">Timings</h3>
                <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 font-mono text-ink">
                  {Object.entries(playlist.debug.timings_ms).map(([step, ms]) => (
                    <div key={step} className="contents">
                      <dt>{step}</dt>
                      <dd className="tabular-nums">{ms} ms</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div>
                <h3 className="font-semibold text-ink">Search query sent to Elastic</h3>
                <pre className="mt-1 rounded-xl bg-code-bg p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-code-ink">
                  {debugQuery}
                </pre>
              </div>
            </>
          ) : (
            <p className="text-ink-2">The API sent no query or timing details for this playlist.</p>
          )}
        </div>
      </div>
    </div>
  )
}
