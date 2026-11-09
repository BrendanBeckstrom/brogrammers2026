import { useId, useState } from 'react'
import { errorMessage, getClipText, type Clip } from '../api'
import { formatClipRange, formatDuration, formatSpeakers, formatTimestamp, matchedByText, talkLabelText } from '../format'
import { btnPrimary, btnSecondary, errorNotice } from '../ui'
import { ClockIcon, ExternalIcon, PlayIcon, ReadIcon, SearchIcon } from './Icons'
import { Spinner } from './Spinner'

type TextState = { status: 'idle' } | { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; text: string }

const LABEL_STYLE: Record<string, string> = {
  deep_dive: 'border-deep-line bg-deep-bg text-deep-text',
  intro: 'border-intro-line bg-intro-bg text-intro-text',
  pitch: 'border-pitch-line bg-pitch-bg text-pitch-text',
}

/** A glyph per label, so the label never relies on colour alone (the text says it too). */
const LABEL_GLYPH: Record<string, string> = {
  deep_dive: '◆',
  intro: '○',
  pitch: '◇',
}

export function ClipCard({ clip, index, toolNames }: { clip: Clip; index: number; toolNames: Map<string, string> }) {
  const titleId = useId()
  const captionsId = useId()
  const [open, setOpen] = useState(false)
  const [text, setText] = useState<TextState>({ status: 'idle' })

  const rank = index + 1
  const start = formatTimestamp(clip.start_sec)
  const end = formatTimestamp(clip.end_sec)
  const label = talkLabelText(clip.talk_label)
  const found = matchedByText(clip.matched_by)
  const tools = clip.matched_tools.map((id) => toolNames.get(id) ?? id)
  const speakers = formatSpeakers(clip.speakers)

  async function loadText() {
    setText({ status: 'loading' })
    try {
      const result = await getClipText(clip.clip_id)
      setText({ status: 'ready', text: result.text })
    } catch (error) {
      setText({ status: 'error', message: errorMessage(error) })
    }
  }

  function toggleRead() {
    const next = !open
    setOpen(next)
    if (next && (text.status === 'idle' || text.status === 'error')) void loadText()
  }

  return (
    <article aria-labelledby={titleId} className="rounded-2xl border border-line bg-surface p-5 shadow-sm sm:p-6">
      <div className="flex gap-5">
        <p
          aria-hidden="true"
          className="hidden w-10 shrink-0 pt-1 font-display text-4xl leading-none font-semibold text-ink-2 tabular-nums sm:block"
        >
          {String(rank).padStart(2, '0')}
        </p>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2 text-xs font-semibold">
            <span className="text-ink-2 sm:sr-only">Clip {rank}</span>
            {label ? (
              <span
                className={`inline-flex items-center gap-1 rounded-full border px-2.5 py-0.5 ${LABEL_STYLE[clip.talk_label ?? ''] ?? ''}`}
              >
                <span aria-hidden="true">{LABEL_GLYPH[clip.talk_label ?? ''] ?? ''}</span>
                {label}
              </span>
            ) : null}
            {found ? (
              <span className="inline-flex items-center gap-1 rounded-full border border-line bg-surface-2 px-2.5 py-0.5 font-medium text-ink-2">
                <SearchIcon className="size-3" />
                {found}
              </span>
            ) : null}
          </div>

          <h3 id={titleId} className="mt-3 text-lg leading-snug font-semibold text-ink sm:text-xl">
            {clip.title}
          </h3>
          <p className="mt-1 text-sm text-ink-2">{speakers}</p>

          <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
            <span className="inline-flex items-center gap-1.5 rounded-lg border border-line bg-surface-2 px-2.5 py-1 font-mono font-semibold text-ink tabular-nums">
              <ClockIcon className="size-4 text-brand-text" />
              <span className="sr-only">Clip runs </span>
              {formatClipRange(clip.start_sec, clip.end_sec)}
            </span>
            <span className="text-ink-2">{formatDuration(clip.end_sec - clip.start_sec)}</span>
          </p>

          {clip.why ? (
            <p className="mt-4 leading-relaxed text-ink">
              <span className="font-semibold text-brand-text">Why for you: </span>
              {clip.why}
            </p>
          ) : null}
          {tools.length > 0 ? (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 text-xs">
              <span className="text-ink-2">Matched:</span>
              <ul className="flex flex-wrap gap-1.5">
                {tools.map((name) => (
                  <li key={name} className="rounded-md bg-brand-soft px-2 py-0.5 font-semibold text-brand-text">
                    {name}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {clip.quote ? (
            <figure className="relative mt-5 rounded-xl border-l-4 border-accent bg-surface-2 py-4 pr-4 pl-11 sm:pl-12">
              <span
                aria-hidden="true"
                className="absolute top-2 left-3 font-display text-5xl leading-none text-accent select-none"
              >
                “
              </span>
              <blockquote className="font-display text-lg leading-relaxed text-ink italic">{clip.quote}</blockquote>
              <figcaption className="mt-2 text-xs text-ink-2">
                {speakers ? `${speakers}, ` : ''}at {start}
              </figcaption>
            </figure>
          ) : null}

          <div className="mt-5 flex flex-wrap gap-2">
            <a
              href={clip.watch_url}
              target="_blank"
              rel="noopener noreferrer"
              data-rum-event="watch_click"
              data-rank={rank}
              data-clip-id={clip.clip_id}
              className={`${btnPrimary} py-2.5 text-sm`}
            >
              <PlayIcon />
              Watch
              <span className="sr-only">
                {' '}
                {clip.title} from {start} to {end} (opens YouTube in a new tab)
              </span>
              <ExternalIcon className="size-3.5 opacity-80" />
            </a>
            <button
              type="button"
              aria-expanded={open}
              aria-controls={captionsId}
              onClick={toggleRead}
              className={`${btnSecondary} py-2.5`}
            >
              <ReadIcon />
              Read
              <span className="sr-only"> automatic captions for {clip.title}</span>
              <span aria-hidden="true" className="text-ink-2">
                {open ? '▴' : '▾'}
              </span>
            </button>
          </div>

          <div id={captionsId} hidden={!open} className="mt-4">
            <section
              aria-label={`Automatic captions: ${clip.title}`}
              className="rounded-xl border border-line bg-canvas p-4 text-sm"
            >
              <h4 className="font-semibold text-ink">Automatic captions</h4>
              <p className="text-xs text-ink-2">Machine-generated by YouTube; names and terms may be misheard.</p>
              <div aria-live="polite" aria-busy={text.status === 'loading'} className="mt-2">
                {text.status === 'loading' ? (
                  <p className="flex items-center gap-2 text-ink-2">
                    <Spinner /> Loading captions…
                  </p>
                ) : null}
                {text.status === 'error' ? (
                  <p className={errorNotice}>
                    Could not load the captions: {text.message}{' '}
                    <button type="button" onClick={() => void loadText()} className="font-semibold underline">
                      Try again
                    </button>
                  </p>
                ) : null}
                {text.status === 'ready' ? (
                  <p className="max-w-prose leading-relaxed whitespace-pre-line text-ink">{text.text}</p>
                ) : null}
              </div>
            </section>
          </div>
        </div>
      </div>
    </article>
  )
}
