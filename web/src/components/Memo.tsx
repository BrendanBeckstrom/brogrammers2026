import { useId, useState } from 'react'
import { errorMessage, postMemo, type MemoResponse } from '../api'
import { plural } from '../format'
import { Markdown } from './Markdown'
import { Spinner } from './Spinner'

type MemoState = { status: 'idle' } | { status: 'loading' } | { status: 'error'; message: string } | { status: 'ready'; memo: MemoResponse }

interface Props {
  tools: string[]
  clipIds: string[]
  announce: (message: string) => void
}

export function Memo({ tools, clipIds, announce }: Props) {
  const sourceId = useId()
  const [state, setState] = useState<MemoState>({ status: 'idle' })
  const [copyStatus, setCopyStatus] = useState('')

  async function makeMemo() {
    setState({ status: 'loading' })
    setCopyStatus('')
    try {
      const memo = await postMemo({ tools, clip_ids: clipIds })
      setState({ status: 'ready', memo })
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
    <div className="space-y-4">
      <p className="text-slate-800">
        One page for your team: three takeaways, two or three action items and a watch-together list, each linked to
        its clip.
      </p>
      <button
        type="button"
        onClick={() => {
          if (state.status !== 'loading') void makeMemo()
        }}
        aria-disabled={state.status === 'loading'}
        className="inline-flex items-center gap-2 rounded-lg bg-indigo-700 px-5 py-3 text-base font-semibold text-white shadow-sm hover:bg-indigo-800 aria-disabled:cursor-not-allowed aria-disabled:opacity-70"
      >
        {state.status === 'loading' ? <Spinner /> : null}
        {state.status === 'loading' ? 'Writing the memo…' : state.status === 'ready' ? 'Make memo again' : 'Make memo'}
      </button>

      {state.status === 'error' ? (
        <p className="rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">{state.message}</p>
      ) : null}

      {state.status === 'ready' ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <button
              type="button"
              onClick={() => void copy(state.memo.markdown)}
              className="inline-flex items-center gap-2 rounded-lg border border-slate-400 bg-white px-4 py-2 text-sm font-semibold text-slate-900 hover:bg-slate-100"
            >
              Copy Markdown
            </button>
            <output aria-live="polite" className="text-sm font-medium text-slate-800">
              {copyStatus ? (
                <>
                  {copyStatus === 'Copied' ? <span aria-hidden="true">✓ </span> : null}
                  {copyStatus}
                </>
              ) : null}
            </output>
          </div>
          <article aria-label="Memo" className="rounded-xl border border-slate-300 bg-white p-5 sm:p-6">
            <Markdown source={state.memo.markdown} headingOffset={2} />
          </article>
          <details className="rounded-xl border border-slate-300 bg-white">
            <summary className="cursor-pointer rounded-xl px-4 py-3 text-sm font-semibold text-slate-900 hover:bg-slate-50">
              Markdown source
            </summary>
            <div className="border-t border-slate-200 p-4">
              <label htmlFor={sourceId} className="sr-only">
                Memo Markdown source
              </label>
              <textarea
                id={sourceId}
                readOnly
                rows={12}
                value={state.memo.markdown}
                className="block w-full rounded-lg border border-slate-400 bg-slate-50 p-3 font-mono text-xs text-slate-900"
              />
            </div>
          </details>
        </div>
      ) : null}
    </div>
  )
}
