import { useId, useState } from 'react'
import { topTerms, type SignificantTerm } from '../api'
import { InfoIcon } from './Icons'

/** "What this conference said about your stack": Elastic's significant terms, as plain (non-interactive) chips. */
export function Terms({ terms }: { terms: SignificantTerm[] | undefined }) {
  const headingId = useId()
  const tipId = useId()
  const [open, setOpen] = useState(false)
  const shown = topTerms(terms, 8)
  if (shown.length === 0) return null

  return (
    <section aria-labelledby={headingId} className="rounded-2xl border border-line bg-surface-2 p-4 sm:p-5">
      <div className="flex items-start justify-between gap-3">
        <h3 id={headingId} className="font-display text-lg font-semibold text-ink">
          What this conference said about your stack
        </h3>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={tipId}
          onClick={() => setOpen((v) => !v)}
          onKeyDown={(e) => {
            if (e.key === 'Escape' && open) {
              e.preventDefault()
              setOpen(false)
            }
          }}
          className="inline-flex shrink-0 items-center gap-1 rounded-full border border-line-strong bg-surface px-2.5 py-1 text-xs font-semibold text-ink hover:bg-canvas"
        >
          <InfoIcon className="size-3.5" />
          What's this?
        </button>
      </div>
      <p
        id={tipId}
        hidden={!open}
        className="mt-2 rounded-xl border border-line bg-surface p-3 text-sm leading-relaxed text-ink-2"
      >
        Significant terms Elasticsearch found in the clips that match your stack: words and phrases these talks use far
        more often than the rest of the conference. The number is how many matching clips use the term.
      </p>
      <ul className="mt-3 flex flex-wrap gap-2">
        {shown.map((t) => (
          <li
            key={t.term}
            className="inline-flex items-baseline gap-1.5 rounded-full border border-line bg-surface px-3 py-1 text-sm"
          >
            <span className="font-medium text-ink">{t.term}</span>
            <span className="text-xs text-ink-2 tabular-nums">
              {t.doc_count}
              <span className="sr-only"> {t.doc_count === 1 ? 'clip' : 'clips'}</span>
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
