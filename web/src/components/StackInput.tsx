import { useId, useMemo, useState, type FormEvent } from 'react'
import {
  DEPENDENCY_FILE_TYPES,
  dependencyFileName,
  errorMessage,
  postStack,
  type StackResponse,
  type StackTool,
  type Talk,
  type Tool,
} from '../api'
import { formatSpeakers, plural } from '../format'
import { btnPrimary, btnSecondary, errorNotice, fieldClass } from '../ui'
import type { Load } from '../useLoad'
import { Spinner } from './Spinner'

export interface StackChoice {
  tools: string[]
  pain: string
  attended_talk_ids: string[]
}

const MAX_FILE_BYTES = 256 * 1024

/** Stable ids: App focuses the first tool chip; the Build button is easy to find in tests and RUM. */
export const STACK_TOOLS_ID = 'stack-tools'
const BUILD_BUTTON_ID = 'build-playlist'

const secondaryButton = btnSecondary

interface Props {
  tools: Load<Tool[]>
  onRetryTools: () => void
  talks: Load<Talk[]>
  onRetryTalks: () => void
  /** Starting values (an example stack or a shared playlist); remount with a new `key` to apply new ones. */
  initial?: StackChoice
  building: boolean
  announce: (message: string) => void
  onBuild: (choice: StackChoice) => void
}

export function StackInput({ tools, onRetryTools, talks, onRetryTalks, initial, building, announce, onBuild }: Props) {
  const ids = {
    tools: useId(),
    file: useId(),
    fileType: useId(),
    paste: useId(),
    pasteHint: useId(),
    pain: useId(),
    painHint: useId(),
    talkFilter: useId(),
    talks: useId(),
    formError: useId(),
  }
  const retryTalks = onRetryTalks

  const [selected, setSelected] = useState<string[]>(() => initial?.tools ?? [])
  // Preset tools the tool list doesn't know still get a chip, named by id.
  const [extraTools, setExtraTools] = useState<StackTool[]>(() =>
    (initial?.tools ?? []).map((id) => ({ id, name: id, source: 'picked' })),
  )
  const [fileName, setFileName] = useState<string>('go.mod')
  const [fileContent, setFileContent] = useState('')
  const [matchedContent, setMatchedContent] = useState<string | null>(null)
  const [stack, setStack] = useState<Load<StackResponse> | null>(null)
  const [pain, setPain] = useState(() => initial?.pain ?? '')
  const [attended, setAttended] = useState<string[]>(() => initial?.attended_talk_ids ?? [])
  const [talkFilter, setTalkFilter] = useState('')
  const [formError, setFormError] = useState<string | null>(null)

  const chips = useMemo(() => {
    const known = tools.status === 'ready' ? tools.data.map((t) => ({ id: t.id, name: t.name })) : []
    const knownIds = new Set(known.map((t) => t.id))
    return [...known, ...extraTools.filter((t) => !knownIds.has(t.id)).map((t) => ({ id: t.id, name: t.name }))]
  }, [tools, extraTools])

  const toggleTool = (id: string) =>
    setSelected((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]))

  const toggleTalk = (id: string) =>
    setAttended((current) => (current.includes(id) ? current.filter((x) => x !== id) : [...current, id]))

  const visibleTalks = useMemo(() => {
    if (talks.status !== 'ready') return []
    const q = talkFilter.trim().toLowerCase()
    if (!q) return talks.data
    return talks.data.filter(
      (t) => t.title.toLowerCase().includes(q) || formatSpeakers(t.speakers).toLowerCase().includes(q),
    )
  }, [talks, talkFilter])

  async function pickFile(file: File | undefined) {
    if (!file) return
    if (file.size > MAX_FILE_BYTES) {
      const message = `${file.name} is larger than 256 KB. Paste just the dependency section instead.`
      setStack({ status: 'error', message })
      announce(message)
      return
    }
    setFileName(dependencyFileName(file.name))
    setFileContent(await file.text())
    setMatchedContent(null)
  }

  /** Sends the picked tools and the dependency file to /api/stack; returns the merged tool ids. */
  async function matchStack(): Promise<string[] | null> {
    const content = fileContent
    if (!content.trim()) return selected
    setStack({ status: 'loading' })
    try {
      const result = await postStack({ tools: selected, dependency_file: { name: fileName, content } })
      setStack({ status: 'ready', data: result })
      setMatchedContent(content)
      setExtraTools(result.tools)
      const merged = [...selected]
      for (const tool of result.tools) if (!merged.includes(tool.id)) merged.push(tool.id)
      setSelected(merged)
      const fromFile = result.tools.filter((t) => t.source !== 'picked').length
      announce(
        `Matched ${plural(fromFile, 'tool')} from ${fileName}. ${plural(result.unknown_packages.length, 'package')} not recognised.`,
      )
      return merged
    } catch (error) {
      const message = `Could not read ${fileName}: ${errorMessage(error)}`
      setStack({ status: 'error', message })
      announce(message)
      return null
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (busy) return
    setFormError(null)
    let toolIds = selected
    if (fileContent.trim() && fileContent !== matchedContent) {
      const merged = await matchStack()
      if (merged === null) return
      toolIds = merged
    }
    if (toolIds.length === 0) {
      const message = 'Pick at least one tool, or paste a dependency file, before building the playlist.'
      setFormError(message)
      announce(message)
      return
    }
    onBuild({ tools: toolIds, pain: pain.trim(), attended_talk_ids: attended })
  }

  const busy = building || stack?.status === 'loading'

  return (
    <form onSubmit={submit} noValidate className="space-y-8">
      {/* Tools */}
      <fieldset id={STACK_TOOLS_ID} aria-describedby={`${ids.tools}-hint`}>
        <legend className="text-base font-semibold text-ink">Tools your team runs</legend>
        <p id={`${ids.tools}-hint`} className="mt-1 text-sm text-ink-2">
          Select every tool that applies. {selected.length > 0 ? `${plural(selected.length, 'tool')} selected.` : ''}
        </p>
        {tools.status === 'loading' ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-ink-2">
            <Spinner /> Loading tools…
          </p>
        ) : null}
        {tools.status === 'error' ? (
          <div className={`mt-3 ${errorNotice}`}>
            <p>Could not load the tool list: {tools.message}</p>
            <button type="button" onClick={onRetryTools} className={`${secondaryButton} mt-2`}>
              Try again
            </button>
          </div>
        ) : null}
        {chips.length > 0 ? (
          <ul className="mt-3 flex flex-wrap gap-2">
            {chips.map((tool) => {
              const pressed = selected.includes(tool.id)
              return (
                <li key={tool.id}>
                  <button
                    type="button"
                    aria-pressed={pressed}
                    onClick={() => toggleTool(tool.id)}
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3.5 py-1.5 text-sm font-medium motion-safe:transition-colors ${
                      pressed
                        ? 'border-brand bg-brand text-brand-ink hover:bg-brand-strong'
                        : 'border-line-strong bg-surface text-ink hover:bg-surface-2'
                    }`}
                  >
                    <span aria-hidden="true" className="w-3.5 text-center">
                      {pressed ? '✓' : '+'}
                    </span>
                    {tool.name}
                  </button>
                </li>
              )
            })}
          </ul>
        ) : null}
      </fieldset>

      {/* Dependency file */}
      <fieldset className="rounded-2xl border border-line bg-canvas p-4 sm:p-5">
        <legend className="float-left mb-2 w-full p-0 text-base font-semibold text-ink">Or use a dependency file</legend>
        <p id={ids.pasteHint} className="clear-both text-sm text-ink-2">
          Debrief maps package names to tools. Supports go.mod, package.json, requirements.txt and Helm Chart.yaml.
          Nothing is stored.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor={ids.file} className="text-sm font-medium text-ink">
              Choose a file
            </label>
            <input
              id={ids.file}
              type="file"
              accept=".mod,.json,.txt,.yaml,.yml"
              onChange={(e) => void pickFile(e.target.files?.[0])}
              className="mt-1 block w-full max-w-full text-sm text-ink file:mr-3 file:rounded-xl file:border file:border-line-strong file:bg-surface file:px-3 file:py-2 file:text-sm file:font-semibold file:text-ink hover:file:bg-surface-2"
            />
          </div>
          <div>
            <label htmlFor={ids.fileType} className="text-sm font-medium text-ink">
              File type
            </label>
            <div className="relative">
              <select
                id={ids.fileType}
                value={fileName}
                onChange={(e) => setFileName(e.target.value)}
                className={`${fieldClass} appearance-none pr-10`}
              >
                {DEPENDENCY_FILE_TYPES.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <svg
                aria-hidden="true"
                viewBox="0 0 20 20"
                className="pointer-events-none absolute top-1/2 right-3 mt-0.5 size-4 -translate-y-1/2 text-ink-2"
              >
                <path d="M5 7.5 10 12.5 15 7.5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </div>
          </div>
        </div>
        <label htmlFor={ids.paste} className="mt-3 block text-sm font-medium text-ink">
          Or paste its contents
        </label>
        <textarea
          id={ids.paste}
          aria-describedby={ids.pasteHint}
          rows={5}
          spellCheck={false}
          value={fileContent}
          onChange={(e) => setFileContent(e.target.value)}
          placeholder={'Paste your go.mod, package.json, requirements.txt or Chart.yaml. For example:\nrequire (\n\tgithub.com/argoproj/argo-cd/v2 v2.13.1\n)'}
          className={`${fieldClass} font-mono text-sm`}
        />
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => {
              if (!busy && fileContent.trim()) void matchStack()
            }}
            aria-disabled={busy || !fileContent.trim()}
            className={secondaryButton}
          >
            {stack?.status === 'loading' ? <Spinner /> : null}
            Match packages
          </button>
          {!fileContent.trim() ? <span className="text-sm text-ink-2">Paste or choose a file first.</span> : null}
        </div>

        {stack?.status === 'error' ? (
          <p className={`mt-3 ${errorNotice}`}>{stack.message}</p>
        ) : null}
        {stack?.status === 'ready' ? <StackResult result={stack.data} /> : null}
      </fieldset>

      {/* Pain */}
      <div>
        <label htmlFor={ids.pain} className="text-base font-semibold text-ink">
          What hurts right now? <span className="font-normal text-ink-2">(optional)</span>
        </label>
        <p id={ids.painHint} className="mt-1 text-sm text-ink-2">
          One sentence. Clips about it rank higher.
        </p>
        <input
          id={ids.pain}
          type="text"
          maxLength={200}
          aria-describedby={ids.painHint}
          value={pain}
          onChange={(e) => setPain(e.target.value)}
          placeholder="e.g. our Argo CD syncs are slow"
          className={fieldClass}
        />
      </div>

      {/* Attended talks */}
      <details className="group rounded-2xl border border-line bg-canvas">
        <summary className="cursor-pointer rounded-2xl px-4 py-3 text-base font-semibold text-ink hover:bg-surface-2">
          Talks we attended <span className="font-normal text-ink-2">(optional)</span>
          {attended.length > 0 ? (
            <span className="ml-2 text-sm font-normal text-ink-2">· {attended.length} selected</span>
          ) : null}
        </summary>
        <div className="border-t border-line p-4">
          <p className="text-sm text-ink-2">Tick talks someone on the team saw, then filter the playlist to what you missed.</p>
          {talks.status === 'loading' ? (
            <p className="mt-3 flex items-center gap-2 text-sm text-ink-2">
              <Spinner /> Loading talks…
            </p>
          ) : null}
          {talks.status === 'error' ? (
            <div className={`mt-3 ${errorNotice}`}>
              <p>Could not load the talk list: {talks.message}</p>
              <button type="button" onClick={retryTalks} className={`${secondaryButton} mt-2`}>
                Try again
              </button>
            </div>
          ) : null}
          {talks.status === 'ready' ? (
            <fieldset className="mt-3">
              <legend className="sr-only">Talks we attended</legend>
              <label htmlFor={ids.talkFilter} className="text-sm font-medium text-ink">
                Filter talks by title or speaker
              </label>
              <input
                id={ids.talkFilter}
                type="search"
                value={talkFilter}
                onChange={(e) => setTalkFilter(e.target.value)}
                className={fieldClass}
              />
              <ul className="mt-3 max-h-72 space-y-1 overflow-y-auto pr-1">
                {visibleTalks.map((talk, i) => (
                  <li key={talk.talk_id} className="flex gap-3 rounded-xl p-2 hover:bg-surface-2">
                    <input
                      id={`${ids.talks}-${i}`}
                      type="checkbox"
                      aria-describedby={`${ids.talks}-${i}-by`}
                      checked={attended.includes(talk.talk_id)}
                      onChange={() => toggleTalk(talk.talk_id)}
                      className="mt-1 size-4 shrink-0 cursor-pointer accent-[var(--brand)]"
                    />
                    <div>
                      <label htmlFor={`${ids.talks}-${i}`} className="block cursor-pointer text-sm font-medium text-ink">
                        {talk.title}
                      </label>
                      <p id={`${ids.talks}-${i}-by`} className="text-sm text-ink-2">
                        {formatSpeakers(talk.speakers)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
              {visibleTalks.length === 0 ? <p className="mt-2 text-sm text-ink-2">No talks match that filter.</p> : null}
            </fieldset>
          ) : null}
        </div>
      </details>

      <div>
        <button
          id={BUILD_BUTTON_ID}
          type="submit"
          data-rum-event="playlist_build"
          aria-disabled={busy}
          aria-describedby={formError ? ids.formError : undefined}
          className={`${btnPrimary} w-full px-6 text-lg sm:w-auto`}
        >
          {building ? <Spinner /> : null}
          {building ? 'Building your playlist…' : 'Build my playlist'}
        </button>
        {formError ? (
          <p id={ids.formError} className="mt-2 text-sm font-medium text-danger-text">
            {formError}
          </p>
        ) : null}
      </div>
    </form>
  )
}

function StackResult({ result }: { result: StackResponse }) {
  return (
    <div className="mt-4 space-y-3 text-sm">
      <div>
        <h3 className="font-semibold text-ink">Matched tools</h3>
        {result.tools.length === 0 ? (
          <p className="mt-1 text-ink-2">No known tools in this file.</p>
        ) : (
          <ul className="mt-2 flex flex-wrap gap-2">
            {result.tools.map((tool) => (
              <li
                key={tool.id}
                className="inline-flex flex-wrap items-baseline gap-x-1.5 rounded-full border border-ok-line bg-ok-bg px-3 py-1 text-ok-text"
              >
                <span aria-hidden="true">✓</span>
                <span className="font-medium">{tool.name}</span>
                <span className="text-xs">
                  {tool.source === 'picked' ? 'picked by you' : `from ${tool.source}`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {result.unknown_packages.length > 0 ? (
        <div>
          <h3 className="font-semibold text-ink">
            Not recognised ({result.unknown_packages.length})
          </h3>
          <p className="text-ink-2">These packages don't map to a tool in our dictionary, so they won't shape the playlist.</p>
          <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs text-ink">
            {result.unknown_packages.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
