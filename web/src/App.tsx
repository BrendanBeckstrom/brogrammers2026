import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  apiFetch,
  buildPlaylistRequest,
  errorMessage,
  getSharedPlaylist,
  getTalks,
  getTools,
  postPlaylist,
  sharedPlaylistId,
  type PlaylistFilter,
  type PlaylistRequest,
  type PlaylistResponse,
} from './api'
import { GitHubIcon, LogoMark } from './components/Icons'
import { Landing } from './components/Landing'
import { Memo } from './components/Memo'
import { Playlist } from './components/Playlist'
import { Spinner } from './components/Spinner'
import { STACK_TOOLS_ID, StackInput, type StackChoice } from './components/StackInput'
import { GITHUB_URL, toolList, type ExampleStack } from './examples'
import { countTalks, playlistAnnouncement, plural, watchMinutes } from './format'
import { btnSecondary, errorNotice, sectionCard } from './ui'
import { useLoad } from './useLoad'
import { fetchVersion } from './version'

export const TAGLINE = "Don't watch 300 hours of KubeCon. Watch the 20 minutes that matter to your team."

const navLink = 'rounded-lg px-2 py-2 text-ink-2 hover:bg-surface-2 hover:text-ink sm:px-3'

interface PlaylistState {
  /** The request in flight, if any. */
  pending: PlaylistRequest | null
  /** The last playlist that loaded, with the request that produced it. */
  shown: { request: PlaylistRequest; response: PlaylistResponse; version: number; sharedAt?: string } | null
  error: string | null
  /** The request that failed, so "Try again" can resend it. */
  failed: PlaylistRequest | null
}

type SharedLoad = { status: 'none' } | { status: 'loading' } | { status: 'error'; message: string }

const loadTools = () => getTools()
const loadTalks = () => getTalks()

const reducedMotion = () =>
  typeof window !== 'undefined' && window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/** Scroll `container` into view and move focus to `target` without a second jump. */
function reveal(target: HTMLElement | null, container: HTMLElement | null) {
  if (!target) return
  ;(container ?? target).scrollIntoView({ behavior: reducedMotion() ? 'auto' : 'smooth', block: 'start' })
  target.focus({ preventScroll: true })
}

/** Drop `?p=<id>` from the address bar once the person builds their own playlist. */
function clearShareParam() {
  const url = new URL(window.location.href)
  if (!url.searchParams.has('p')) return
  url.searchParams.delete('p')
  window.history.replaceState(null, '', url.pathname + url.search + url.hash)
}

export default function App() {
  const [version, setVersion] = useState<string | null>(null)
  const [tools, retryTools] = useLoad(loadTools)
  const [talks, retryTalks] = useLoad(loadTalks)
  const [announcement, setAnnouncement] = useState({ id: 0, text: '' })
  const [playlist, setPlaylist] = useState<PlaylistState | null>(null)
  const [preset, setPreset] = useState<{ key: number; choice?: StackChoice }>({ key: 0 })
  const [shared, setShared] = useState<SharedLoad>(() =>
    typeof window !== 'undefined' && sharedPlaylistId(window.location.search) ? { status: 'loading' } : { status: 'none' },
  )
  const requestSeq = useRef(0)
  const focusOnLoad = useRef(false)
  const playlistHeading = useRef<HTMLHeadingElement>(null)
  const stackHeading = useRef<HTMLHeadingElement>(null)
  const stackSection = useRef<HTMLElement>(null)

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

  // A shared link (?p=<id>): load the saved playlist, render it as-is and pre-fill the form.
  useEffect(() => {
    const id = sharedPlaylistId(window.location.search)
    if (!id) return
    let cancelled = false
    const startSeq = requestSeq.current
    getSharedPlaylist(id).then(
      (saved) => {
        if (cancelled || requestSeq.current !== startSeq) return
        const seq = ++requestSeq.current
        focusOnLoad.current = true
        setShared({ status: 'none' })
        setPreset((p) => ({
          key: p.key + 1,
          choice: {
            tools: saved.request.tools ?? [],
            pain: saved.request.pain ?? '',
            attended_talk_ids: saved.request.attended_talk_ids ?? [],
          },
        }))
        setPlaylist({
          pending: null,
          shown: { request: saved.request, response: saved.response, version: seq, sharedAt: saved.created_at },
          error: null,
          failed: null,
        })
        announce(
          `Shared ${playlistAnnouncement(saved.response.total_seconds, saved.response.clips, saved.response.message).replace(/^Playlist/, 'playlist')}`,
        )
      },
      (error: unknown) => {
        if (cancelled || requestSeq.current !== startSeq) return
        const message =
          error instanceof ApiError && error.status === 404
            ? 'That shared playlist link has expired or never existed. Build a fresh one below; it only takes a few seconds.'
            : `Could not load the shared playlist: ${errorMessage(error)}`
        setShared({ status: 'error', message })
        announce(message)
      },
    )
    return () => {
      cancelled = true
    }
  }, [announce])

  async function loadPlaylist(request: PlaylistRequest, focusWhenLoaded: boolean) {
    const seq = ++requestSeq.current
    focusOnLoad.current = focusWhenLoaded
    setPlaylist((prev) => ({ pending: request, shown: prev?.shown ?? null, error: null, failed: null }))
    try {
      const response = await postPlaylist(request)
      if (seq !== requestSeq.current) return
      setPlaylist({ pending: null, shown: { request, response, version: seq }, error: null, failed: null })
      announce(playlistAnnouncement(response.total_seconds, response.clips, response.message))
    } catch (error) {
      if (seq !== requestSeq.current) return
      const message = `Could not build the playlist: ${errorMessage(error)}`
      setPlaylist((prev) => ({ pending: null, shown: prev?.shown ?? null, error: message, failed: request }))
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
    clearShareParam()
    setShared({ status: 'none' })
    void loadPlaylist(buildPlaylistRequest({ ...choice, filter: 'all' }), true)
  }

  function changeFilter(filter: PlaylistFilter) {
    const base = playlist?.pending ?? playlist?.shown?.request
    if (base) void loadPlaylist({ ...base, filter }, false)
  }

  /** Hero CTA: bring the stack form into view and focus its first tool chip. */
  function startBuilding() {
    const firstChip = document.querySelector<HTMLButtonElement>(`#${STACK_TOOLS_ID} button[aria-pressed]`)
    reveal(firstChip ?? stackHeading.current, stackSection.current)
  }

  /** Example stack: fill in the form and build straight away. */
  function applyExample(example: ExampleStack) {
    const choice: StackChoice = { tools: example.tools, pain: example.pain, attended_talk_ids: [] }
    setPreset((p) => ({ key: p.key + 1, choice }))
    announce(
      `${example.name} example: ${toolList(example.tools, toolNames)}${example.pain ? `, “${example.pain}”` : ''}. Building the playlist…`,
    )
    build(choice)
  }

  const shown = playlist?.shown ?? null
  const pending = playlist?.pending ?? null
  const building = pending !== null
  const clips = shown?.response.clips ?? []
  const showPlaylist = playlist !== null || shared.status === 'loading'

  return (
    <div className="flex min-h-screen flex-col bg-canvas text-ink">
      <a
        href="#main"
        className="sr-only rounded-lg bg-surface px-4 py-2 font-semibold text-brand-text shadow focus:not-sr-only focus:absolute focus:top-3 focus:left-3 focus:z-50"
      >
        Skip to main content
      </a>
      <header className="border-b border-line bg-surface/80 backdrop-blur">
        <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
          <p className="flex items-center gap-2.5">
            <LogoMark />
            <span className="font-display text-2xl font-semibold tracking-tight text-ink">Debrief</span>
          </p>
          <nav aria-label="Site">
            <ul className="flex items-center gap-0.5 text-sm font-semibold sm:gap-2">
              <li className="hidden sm:block">
                <a href="#how-heading" className={navLink}>
                  How it works
                </a>
              </li>
              <li>
                <a href="#example-heading" className={navLink}>
                  Example
                </a>
              </li>
              <li>
                <a href="#stack-heading" className={navLink}>
                  Try it
                </a>
              </li>
              <li>
                <a
                  href={GITHUB_URL}
                  target="_blank"
                  rel="noopener noreferrer"
                  className={`${navLink} inline-flex items-center gap-1.5`}
                >
                  <GitHubIcon />
                  <span className="sr-only sm:not-sr-only">GitHub</span>
                  <span className="sr-only"> (opens in a new tab)</span>
                </a>
              </li>
            </ul>
          </nav>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="flex-1">
        <Landing
          toolNames={toolNames}
          busy={building}
          onStart={startBuilding}
          onExample={applyExample}
        />

        <div className="mx-auto max-w-4xl space-y-6 px-4 pt-10 sm:pt-14">
          {shared.status === 'error' ? (
            <div className="rounded-2xl border border-line-strong bg-surface-2 p-4 text-ink">
              <p className="font-semibold">Shared playlist not found</p>
              <p className="mt-1 text-ink-2">{shared.message}</p>
            </div>
          ) : null}

          <section ref={stackSection} aria-labelledby="stack-heading" className={sectionCard}>
            <p className="text-sm font-semibold tracking-wide text-accent uppercase">Step 1</p>
            <h2
              id="stack-heading"
              ref={stackHeading}
              tabIndex={-1}
              className="mt-1 font-display text-3xl font-semibold text-ink"
            >
              Try it with your team's stack
            </h2>
            <p className="mt-2 mb-8 text-ink-2">
              Pick the tools your team runs and say what's hurting. You get a playlist of the clips that matter, each a
              few minutes long.
            </p>
            <StackInput
              key={preset.key}
              initial={preset.choice}
              tools={tools}
              onRetryTools={retryTools}
              talks={talks}
              onRetryTalks={retryTalks}
              building={building}
              announce={announce}
              onBuild={build}
            />
          </section>
        </div>

        {shown && clips.length > 0 ? (
          <section
            aria-label="Playlist summary"
            className="sticky top-0 z-30 mt-8 border-y border-line bg-canvas/90 backdrop-blur"
          >
            <div className="mx-auto flex max-w-4xl flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2.5">
              <p className="flex flex-wrap items-baseline gap-x-2 text-sm text-ink">
                <span className="font-display text-lg font-semibold">
                  {watchMinutes(shown.response.total_seconds)} min
                </span>
                <span aria-hidden="true" className="text-ink-2">
                  ·
                </span>
                <span>{plural(clips.length, 'clip')}</span>
                <span aria-hidden="true" className="text-ink-2">
                  ·
                </span>
                <span>{plural(countTalks(clips), 'talk')}</span>
                {building ? (
                  <span className="inline-flex items-center gap-1.5 text-ink-2">
                    <Spinner /> Updating…
                  </span>
                ) : null}
              </p>
              <ul className="flex items-center gap-1 text-sm font-semibold">
                <li>
                  <a href="#playlist-heading" className="rounded-lg px-2.5 py-1.5 text-brand-text hover:bg-surface-2">
                    Clips
                  </a>
                </li>
                <li>
                  <a href="#memo-heading" className="rounded-lg px-2.5 py-1.5 text-brand-text hover:bg-surface-2">
                    Memo
                  </a>
                </li>
                <li>
                  <a href="#stack-heading" className="rounded-lg px-2.5 py-1.5 text-brand-text hover:bg-surface-2">
                    Edit stack
                  </a>
                </li>
              </ul>
            </div>
          </section>
        ) : null}

        <div className="mx-auto max-w-4xl space-y-8 px-4 pt-8 pb-16 sm:pb-20">
          {showPlaylist ? (
            <section aria-labelledby="playlist-heading" className={sectionCard}>
              <p className="text-sm font-semibold tracking-wide text-accent uppercase">Step 2</p>
              <h2
                id="playlist-heading"
                ref={playlistHeading}
                tabIndex={-1}
                className="mt-1 mb-5 font-display text-3xl font-semibold text-ink"
              >
                Your playlist
              </h2>
              {shown ? (
                <Playlist
                  key={shown.version}
                  playlist={shown.response}
                  request={pending ?? shown.request}
                  sharedRequest={shown.request}
                  updating={building}
                  error={playlist?.error ?? null}
                  toolNames={toolNames}
                  sharedAt={shown.sharedAt ?? null}
                  onFilterChange={changeFilter}
                />
              ) : building || shared.status === 'loading' ? (
                <LoadingPlaylist
                  text={
                    shared.status === 'loading'
                      ? 'Loading the shared playlist…'
                      : 'Searching the talks for your stack… Reranking can take a few seconds.'
                  }
                />
              ) : playlist?.error ? (
                <div className={`${errorNotice} text-base`}>
                  <p>{playlist.error}</p>
                  {playlist.failed ? (
                    <button
                      type="button"
                      onClick={() => {
                        if (playlist.failed) void loadPlaylist(playlist.failed, true)
                      }}
                      className={`${btnSecondary} mt-3`}
                    >
                      Try again
                    </button>
                  ) : null}
                </div>
              ) : null}
            </section>
          ) : null}

          {shown && clips.length > 0 ? (
            <section aria-labelledby="memo-heading" className={sectionCard}>
              <p className="text-sm font-semibold tracking-wide text-accent uppercase">Step 3</p>
              <h2 id="memo-heading" tabIndex={-1} className="mt-1 mb-3 font-display text-3xl font-semibold text-ink">
                Memo
              </h2>
              <Memo
                key={shown.version}
                tools={shown.request.tools}
                clipIds={clips.map((c) => c.clip_id)}
                stackLabel={toolList(shown.request.tools, toolNames)}
                announce={announce}
              />
            </section>
          ) : null}
        </div>
      </main>

      <footer className="border-t border-line bg-surface">
        <div className="mx-auto grid max-w-6xl gap-8 px-4 py-10 text-sm text-ink-2 md:grid-cols-[1.4fr_1fr]">
          <div className="space-y-3">
            <p className="flex items-center gap-2">
              <LogoMark className="size-6" />
              <span className="font-display text-lg font-semibold text-ink">Debrief</span>
            </p>
            <p className="max-w-prose text-ink">{TAGLINE}</p>
            <p className="max-w-prose">
              Short quotes from public CNCF recordings; every clip links to the original video. Transcripts are
              automatic captions.
            </p>
            <p className="max-w-prose">Built with AI coding assistants (Claude Code); reviewed by people and Gitar.</p>
          </div>
          <div className="space-y-3 md:text-right">
            <p>
              <a
                href={GITHUB_URL}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-2 rounded-lg font-semibold text-brand-text underline underline-offset-2 hover:text-ink"
              >
                <GitHubIcon />
                Source on GitHub
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            </p>
            <p>Free and public. No accounts, nothing stored about you.</p>
            <p>Made at the WeMakeDevs Cloud Native Hackathon, Salt Lake City.</p>
            {version ? <p className="font-mono text-xs">version {version}</p> : null}
          </div>
        </div>
      </footer>

      <output aria-live="polite" className="sr-only">
        <span key={announcement.id}>{announcement.text}</span>
      </output>
    </div>
  )
}

/** First-load state: a few placeholder cards (static when motion is reduced) and a spoken-friendly line. */
function LoadingPlaylist({ text }: { text: string }) {
  return (
    <div>
      <p className="flex items-center gap-2 text-ink-2">
        <Spinner /> {text}
      </p>
      <ul aria-hidden="true" className="mt-5 space-y-4">
        {[0, 1, 2].map((i) => (
          <li key={i} className="rounded-2xl border border-line bg-surface p-5 sm:p-6">
            <div className="space-y-3 motion-safe:animate-pulse">
              <div className="flex gap-2">
                <div className="h-5 w-20 rounded-full bg-surface-2" />
                <div className="h-5 w-32 rounded-full bg-surface-2" />
              </div>
              <div className="h-6 w-4/5 rounded bg-surface-2" />
              <div className="h-4 w-1/3 rounded bg-surface-2" />
              <div className="h-16 w-full rounded-xl bg-surface-2" />
              <div className="flex gap-2">
                <div className="h-10 w-28 rounded-xl bg-surface-2" />
                <div className="h-10 w-24 rounded-xl bg-surface-2" />
              </div>
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
