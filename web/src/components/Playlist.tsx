import { useId, useMemo, useState } from 'react'
import type { PlaylistFilter, PlaylistRequest, PlaylistResponse } from '../api'
import { playlistSummary } from '../format'
import { ClipCard } from './ClipCard'
import { Spinner } from './Spinner'

const FILTERS: { value: PlaylistFilter; label: string }[] = [
  { value: 'all', label: 'All talks' },
  { value: 'attended', label: 'Talks we attended' },
  { value: 'missed', label: 'Talks we missed' },
]

interface Props {
  playlist: PlaylistResponse
  request: PlaylistRequest
  updating: boolean
  error: string | null
  toolNames: Map<string, string>
  onFilterChange: (filter: PlaylistFilter) => void
}

export function Playlist({ playlist, request, updating, error, toolNames, onFilterChange }: Props) {
  const filterHint = useId()
  const hoodId = useId()
  const [hoodOpen, setHoodOpen] = useState(false)
  const noAttended = request.attended_talk_ids.length === 0
  const summary = playlistSummary(playlist.total_seconds, playlist.clips)
  const debugQuery = useMemo(
    () => (playlist.debug ? JSON.stringify(playlist.debug.query, null, 2) : null),
    [playlist.debug],
  )

  return (
    <div className="space-y-5" aria-busy={updating}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-xl font-medium text-slate-900">
          {playlist.clips.length > 0 ? summary : 'Nothing to watch yet'}
        </p>
        {updating ? (
          <p className="flex items-center gap-2 text-sm text-slate-700">
            <Spinner /> Updating…
          </p>
        ) : null}
      </div>

      <fieldset aria-describedby={noAttended ? filterHint : undefined}>
        <legend className="text-sm font-semibold text-slate-900">Show</legend>
        <div className="mt-2 flex flex-wrap gap-2">
          {FILTERS.map((f) => {
            const disabled = f.value !== 'all' && noAttended
            const checked = request.filter === f.value
            return (
              <label
                key={f.value}
                className={`flex items-center gap-2 rounded-lg border px-3 py-1.5 text-sm ${
                  checked ? 'border-indigo-700 bg-indigo-50 font-semibold text-indigo-950' : 'border-slate-400 bg-white text-slate-900'
                } ${disabled ? 'cursor-not-allowed opacity-60' : 'cursor-pointer hover:bg-slate-100'}`}
              >
                <input
                  type="radio"
                  name="playlist-filter"
                  value={f.value}
                  checked={checked}
                  disabled={disabled}
                  onChange={() => onFilterChange(f.value)}
                  className="size-4 accent-indigo-700"
                />
                {f.label}
              </label>
            )
          })}
        </div>
        {noAttended ? (
          <p id={filterHint} className="mt-2 text-sm text-slate-700">
            To filter by attendance, tick the talks you attended above and build again.
          </p>
        ) : null}
      </fieldset>

      {error ? (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">{error}</p>
      ) : null}

      {playlist.clips.length === 0 ? (
        <div className="rounded-xl border border-slate-300 bg-white p-5 text-slate-800">
          <p className="font-semibold text-slate-900">No clips for this stack.</p>
          <p className="mt-1">
            {playlist.message ?? 'No strong matches for these tools. Try adding more of your stack.'}
          </p>
        </div>
      ) : (
        <ol className="space-y-4">
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
          className="inline-flex items-center gap-2 rounded-lg border border-slate-400 bg-white px-3 py-2 text-sm font-medium text-slate-900 hover:bg-slate-100"
        >
          <span aria-hidden="true">{hoodOpen ? '▾' : '▸'}</span>
          Under the hood
        </button>
        <div id={hoodId} hidden={!hoodOpen} className="mt-3 space-y-4 rounded-xl border border-slate-300 bg-white p-4 text-sm">
          <div>
            <h3 className="font-semibold text-slate-900">Trace id</h3>
            <p className="mt-1 font-mono break-all text-slate-800">{playlist.trace_id || 'none'}</p>
          </div>
          {playlist.debug ? (
            <>
              <div>
                <h3 className="font-semibold text-slate-900">Timings</h3>
                <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 font-mono text-slate-800">
                  {Object.entries(playlist.debug.timings_ms).map(([step, ms]) => (
                    <div key={step} className="contents">
                      <dt>{step}</dt>
                      <dd className="tabular-nums">{ms} ms</dd>
                    </div>
                  ))}
                </dl>
              </div>
              <div>
                <h3 className="font-semibold text-slate-900">Search query sent to Elastic</h3>
                <pre className="mt-1 rounded-lg bg-slate-900 p-3 font-mono text-xs leading-relaxed break-words whitespace-pre-wrap text-slate-100">
                  {debugQuery}
                </pre>
              </div>
            </>
          ) : (
            <p className="text-slate-700">The API sent no query or timing details for this playlist.</p>
          )}
        </div>
      </div>
    </div>
  )
}
