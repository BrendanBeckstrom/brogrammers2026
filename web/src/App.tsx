import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  apiFetch,
  buildPlaylistRequest,
  errorMessage,
  getTools,
  postPlaylist,
  type PlaylistFilter,
  type PlaylistRequest,
  type PlaylistResponse,
} from './api'
import { Memo } from './components/Memo'
import { Playlist } from './components/Playlist'
import { Spinner } from './components/Spinner'
import { StackInput, type StackChoice } from './components/StackInput'
import { playlistAnnouncement } from './format'
import { useLoad } from './useLoad'
import { fetchVersion } from './version'

export const TAGLINE = "Don't watch 300 hours of KubeCon. Watch the 20 minutes that matter to your team."

interface PlaylistState {
  /** The request in flight, if any. */
  pending: PlaylistRequest | null
  /** The last playlist that loaded, with the request that produced it. */
  shown: { request: PlaylistRequest; response: PlaylistResponse; version: number } | null
  error: string | null
}

const loadTools = () => getTools()

const sectionClass = 'rounded-2xl border border-slate-300 bg-white p-5 shadow-sm sm:p-8'

export default function App() {
  const [version, setVersion] = useState<string | null>(null)
  const [tools, retryTools] = useLoad(loadTools)
  const [announcement, setAnnouncement] = useState({ id: 0, text: '' })
  const [playlist, setPlaylist] = useState<PlaylistState | null>(null)
  const requestSeq = useRef(0)
  const focusOnLoad = useRef(false)
  const playlistHeading = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    let cancelled = false
    fetchVersion(apiFetch).then((v) => {
      if (!cancelled) setVersion(v)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const announce = useCallback((text: string) => setAnnouncement((a) => ({ id: a.id + 1, text })), [])

  const toolNames = useMemo(
    () => new Map(tools.status === 'ready' ? tools.data.map((t) => [t.id, t.name] as const) : []),
    [tools],
  )

  async function loadPlaylist(request: PlaylistRequest, focusWhenLoaded: boolean) {
    const seq = ++requestSeq.current
    focusOnLoad.current = focusWhenLoaded
    setPlaylist((prev) => ({ pending: request, shown: prev?.shown ?? null, error: null }))
    try {
      const response = await postPlaylist(request)
      if (seq !== requestSeq.current) return
      setPlaylist({ pending: null, shown: { request, response, version: seq }, error: null })
      announce(playlistAnnouncement(response.total_seconds, response.clips, response.message))
    } catch (error) {
      if (seq !== requestSeq.current) return
      const message = `Could not build the playlist: ${errorMessage(error)}`
      setPlaylist((prev) => ({ pending: null, shown: prev?.shown ?? null, error: message }))
      announce(message)
    }
  }

  // After a new build finishes (or fails), move focus to the playlist heading so
  // keyboard and screen reader users land on the result.
  useEffect(() => {
    if (focusOnLoad.current && playlist && playlist.pending === null) {
      focusOnLoad.current = false
      playlistHeading.current?.focus()
    }
  }, [playlist])

  function build(choice: StackChoice) {
    void loadPlaylist(buildPlaylistRequest({ ...choice, filter: 'all' }), true)
  }

  function changeFilter(filter: PlaylistFilter) {
    const base = playlist?.shown?.request
    if (base) void loadPlaylist({ ...base, filter }, false)
  }

  const shown = playlist?.shown ?? null
  const pending = playlist?.pending ?? null
  const building = pending !== null

  return (
    <div className="flex min-h-screen flex-col bg-slate-50 text-slate-900">
      <a
        href="#main"
        className="sr-only rounded-lg bg-white px-4 py-2 font-semibold text-indigo-800 shadow focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-10"
      >
        Skip to main content
      </a>
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto max-w-3xl px-4 py-6 sm:py-8">
          <h1 className="text-3xl font-bold tracking-tight text-slate-900">Debrief</h1>
          <p className="mt-2 text-lg text-slate-700">{TAGLINE}</p>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-3xl flex-1 space-y-8 px-4 py-8">
        <section aria-labelledby="stack-heading" className={sectionClass}>
          <h2 id="stack-heading" className="text-2xl font-semibold text-slate-900">
            Your stack
          </h2>
          <p className="mt-1 mb-6 text-slate-700">
            Tell Debrief what your team runs. You get a playlist of the clips that matter, each a few minutes long.
          </p>
          <StackInput
            tools={tools}
            onRetryTools={retryTools}
            building={building}
            announce={announce}
            onBuild={build}
          />
        </section>

        {playlist ? (
          <section aria-labelledby="playlist-heading" className={sectionClass}>
            <h2
              id="playlist-heading"
              ref={playlistHeading}
              tabIndex={-1}
              className="mb-4 text-2xl font-semibold text-slate-900"
            >
              Your playlist
            </h2>
            {shown ? (
              <Playlist
                playlist={shown.response}
                request={pending ?? shown.request}
                updating={building}
                error={playlist.error}
                toolNames={toolNames}
                onFilterChange={changeFilter}
              />
            ) : building ? (
              <p className="flex items-center gap-2 text-slate-700">
                <Spinner /> Searching every talk for your stack…
              </p>
            ) : playlist.error ? (
              <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">{playlist.error}</p>
            ) : null}
          </section>
        ) : null}

        {shown && shown.response.clips.length > 0 ? (
          <section aria-labelledby="memo-heading" className={sectionClass}>
            <h2 id="memo-heading" className="mb-2 text-2xl font-semibold text-slate-900">
              Memo
            </h2>
            <Memo
              key={shown.version}
              tools={shown.request.tools}
              clipIds={shown.response.clips.map((c) => c.clip_id)}
              announce={announce}
            />
          </section>
        ) : null}
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto max-w-3xl px-4 py-4 text-sm text-slate-700">
          Clips link to the talk recordings on YouTube. Transcripts are automatic captions.
          {version ? <span className="ml-2 font-mono text-xs">version {version}</span> : null}
        </div>
      </footer>

      <output aria-live="polite" className="sr-only">
        <span key={announcement.id}>{announcement.text}</span>
      </output>
    </div>
  )
}
