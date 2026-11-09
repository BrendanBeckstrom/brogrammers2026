import { useId, useState } from 'react'
import { errorMessage, postMemo, type MemoResponse } from '../api'
import { plural } from '../format'
import { btnPrimary, btnSecondary, errorNotice } from '../ui'
import { CheckIcon, CopyIcon, MemoIcon } from './Icons'
import { Markdown } from './Markdown'
import { Spinner } from './Spinner'

type MemoState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; memo: MemoResponse; madeAt: string }

interface Props {
  tools: string[]
  clipIds: string[]
  /** "Argo CD, Prometheus": shown in the memo's header. */
  stackLabel?: string
  announce: (message: string) => void
}

const memoDate = new Intl.DateTimeFormat('en', { dateStyle: 'long' })

export function Memo({ tools, clipIds, stackLabel, announce }: Props) {
  const sourceId = useId()
  const [state, setState] = useState<MemoState>({ status: 'idle' })
  const [copyStatus, setCopyStatus] = useState('')

  async function makeMemo() {
    setState({ status: 'loading' })
    setCopyStatus('')
    try {
      const memo = await postMemo({ tools, clip_ids: clipIds })
      setState({ status: 'ready', memo, madeAt: memoDate.format(new Date()) })
      announce(`Memo ready, citing ${plural(memo.cited_clip_ids.length, 'clip')}.`)
    } catch (error) {
      const message = `Could not make the memo: ${errorMessage(error)}`
      setState({ status: 'error', message })
      announce(message)
    }
  }

  async function copy(markdown: string) {
    try {
      if (!navigator.clipboard) throw new Error('Clipboard unavailable')
      await navigator.clipboard.writeText(markdown)
      setCopyStatus('Copied')
    } catch {
      setCopyStatus('Could not copy. Open “Markdown source” below and copy it from there.')
    }
  }

  return (
    <div className="space-y-5">
      <p className="text-ink-2">
        One page for your team: three takeaways, two or three action items and a watch-together list, each linked to
        its clip. Ready for Slack, email or a wiki.
      </p>
      <div className="flex flex-wrap items-center gap-3">
        <button
          type="button"
          data-rum-event="memo_make"
          onClick={() => {
            if (state.status !== 'loading') void makeMemo()
          }}
          aria-disabled={state.status === 'loading'}
          className={btnPrimary}
        >
          {state.status === 'loading' ? <Spinner /> : <MemoIcon className="size-5" />}
          {state.status === 'loading' ? 'Writing the memo…' : state.status === 'ready' ? 'Make memo again' : 'Make memo'}
        </button>
        {state.status === 'ready' ? (
          <>
            <button
              type="button"
              data-rum-event="memo_copy"
              onClick={() => void copy(state.memo.markdown)}
              className={`${btnSecondary} py-3`}
            >
              <CopyIcon />
              Copy Markdown
            </button>
            <output aria-live="polite" className="text-sm font-medium text-ink-2">
              {copyStatus ? (
                <>
                  {copyStatus === 'Copied' ? <CheckIcon className="mr-1 inline size-4 align-[-3px] text-ok-text" /> : null}
                  {copyStatus}
                </>
              ) : null}
            </output>
          </>
        ) : null}
      </div>

      {state.status === 'error' ? <p className={errorNotice}>{state.message}</p> : null}

      {state.status === 'loading' ? (
        <div aria-hidden="true" className="rounded-2xl border border-line bg-surface p-6 sm:p-10">
          <div className="space-y-3 motion-safe:animate-pulse">
            <div className="h-6 w-2/3 rounded bg-surface-2" />
            <div className="h-4 w-full rounded bg-surface-2" />
            <div className="h-4 w-11/12 rounded bg-surface-2" />
            <div className="h-4 w-4/5 rounded bg-surface-2" />
            <div className="mt-6 h-5 w-1/3 rounded bg-surface-2" />
            <div className="h-4 w-full rounded bg-surface-2" />
            <div className="h-4 w-3/4 rounded bg-surface-2" />
          </div>
        </div>
      ) : null}

      {state.status === 'ready' ? (
        <div className="space-y-4">
          <article
            aria-label="Memo"
            className="relative overflow-hidden rounded-2xl border border-line bg-surface shadow-md"
          >
            <div aria-hidden="true" className="h-1.5 bg-[linear-gradient(90deg,var(--brand),var(--accent))]" />
            <div className="border-b border-line px-6 py-4 sm:px-10">
              <p className="text-xs font-semibold tracking-[0.2em] text-accent uppercase">Memo</p>
              <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-sm">
                <dt className="text-ink-2">To</dt>
                <dd className="text-ink">The team</dd>
                {stackLabel ? (
                  <>
                    <dt className="text-ink-2">Re</dt>
                    <dd className="text-ink">{stackLabel}</dd>
                  </>
                ) : null}
                <dt className="text-ink-2">Date</dt>
                <dd className="text-ink">{state.madeAt}</dd>
                <dt className="text-ink-2">Sources</dt>
                <dd className="text-ink">{plural(state.memo.cited_clip_ids.length, 'cited clip')}</dd>
              </dl>
            </div>
            <div className="px-6 py-6 sm:px-10 sm:py-8">
              <Markdown source={state.memo.markdown} headingOffset={2} />
            </div>
          </article>
          <details className="rounded-2xl border border-line bg-surface">
            <summary className="cursor-pointer rounded-2xl px-4 py-3 text-sm font-semibold text-ink hover:bg-surface-2">
              Markdown source
            </summary>
            <div className="border-t border-line p-4">
              <label htmlFor={sourceId} className="sr-only">
                Memo Markdown source
              </label>
              <textarea
                id={sourceId}
                readOnly
                rows={12}
                value={state.memo.markdown}
                className="block w-full rounded-xl border border-line-strong bg-canvas p-3 font-mono text-xs text-ink"
              />
            </div>
          </details>
        </div>
      ) : null}
    </div>
  )
}
