import { useId, useMemo, useState, type FormEvent } from 'react'
import {
  DEPENDENCY_FILE_TYPES,
  dependencyFileName,
  errorMessage,
  getTalks,
  postStack,
  type StackResponse,
  type StackTool,
  type Talk,
  type Tool,
} from '../api'
import { formatSpeakers, plural } from '../format'
import { useLoad, type Load } from '../useLoad'
import { Spinner } from './Spinner'

export interface StackChoice {
  tools: string[]
  pain: string
  attended_talk_ids: string[]
}

const MAX_FILE_BYTES = 256 * 1024

const loadTalks = () => getTalks()

const fieldClass =
  'mt-1 block w-full rounded-lg border border-slate-400 bg-white px-3 py-2 text-slate-900 placeholder:text-slate-500'
const secondaryButton =
  'inline-flex items-center gap-2 rounded-lg border border-slate-400 bg-white px-3 py-2 text-sm font-medium text-slate-900 hover:bg-slate-100 aria-disabled:cursor-not-allowed aria-disabled:opacity-60'

interface Props {
  tools: Load<Tool[]>
  onRetryTools: () => void
  building: boolean
  announce: (message: string) => void
  onBuild: (choice: StackChoice) => void
}

export function StackInput({ tools, onRetryTools, building, announce, onBuild }: Props) {
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
  const [talks, retryTalks] = useLoad<Talk[]>(loadTalks)

  const [selected, setSelected] = useState<string[]>([])
  const [extraTools, setExtraTools] = useState<StackTool[]>([])
  const [fileName, setFileName] = useState<string>('go.mod')
  const [fileContent, setFileContent] = useState('')
  const [matchedContent, setMatchedContent] = useState<string | null>(null)
  const [stack, setStack] = useState<Load<StackResponse> | null>(null)
  const [pain, setPain] = useState('')
  const [attended, setAttended] = useState<string[]>([])
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
      <fieldset aria-describedby={`${ids.tools}-hint`}>
        <legend className="text-base font-semibold text-slate-900">Tools your team runs</legend>
        <p id={`${ids.tools}-hint`} className="mt-1 text-sm text-slate-700">
          Select every tool that applies. {selected.length > 0 ? `${plural(selected.length, 'tool')} selected.` : ''}
        </p>
        {tools.status === 'loading' ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-slate-700">
            <Spinner /> Loading tools…
          </p>
        ) : null}
        {tools.status === 'error' ? (
          <div className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">
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
                    className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-sm font-medium motion-safe:transition-colors ${
                      pressed
                        ? 'border-indigo-700 bg-indigo-700 text-white hover:bg-indigo-800'
                        : 'border-slate-400 bg-white text-slate-900 hover:bg-slate-100'
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
      <fieldset className="rounded-xl border border-slate-300 p-4">
        <legend className="px-1 text-base font-semibold text-slate-900">Or use a dependency file</legend>
        <p id={ids.pasteHint} className="text-sm text-slate-700">
          Debrief maps package names to tools. Supports go.mod, package.json, requirements.txt and Helm Chart.yaml.
          Nothing is stored.
        </p>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div>
            <label htmlFor={ids.file} className="text-sm font-medium text-slate-900">
              Choose a file
            </label>
            <input
              id={ids.file}
              type="file"
              accept=".mod,.json,.txt,.yaml,.yml"
              onChange={(e) => void pickFile(e.target.files?.[0])}
              className="mt-1 block w-full text-sm text-slate-800 file:mr-3 file:rounded-lg file:border file:border-slate-400 file:bg-white file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-900 hover:file:bg-slate-100"
            />
          </div>
          <div>
            <label htmlFor={ids.fileType} className="text-sm font-medium text-slate-900">
              File type
            </label>
            <select
              id={ids.fileType}
              value={fileName}
              onChange={(e) => setFileName(e.target.value)}
              className={fieldClass}
            >
              {DEPENDENCY_FILE_TYPES.map((name) => (
                <option key={name} value={name}>
                  {name}
                </option>
              ))}
            </select>
          </div>
        </div>
        <label htmlFor={ids.paste} className="mt-3 block text-sm font-medium text-slate-900">
          Or paste its contents
        </label>
        <textarea
          id={ids.paste}
          aria-describedby={ids.pasteHint}
          rows={5}
          spellCheck={false}
          value={fileContent}
          onChange={(e) => setFileContent(e.target.value)}
          placeholder={'require (\n\tgithub.com/argoproj/argo-cd/v2 v2.13.1\n)'}
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
          {!fileContent.trim() ? <span className="text-sm text-slate-700">Paste or choose a file first.</span> : null}
        </div>

        {stack?.status === 'error' ? (
          <p className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">{stack.message}</p>
        ) : null}
        {stack?.status === 'ready' ? <StackResult result={stack.data} /> : null}
      </fieldset>

      {/* Pain */}
      <div>
        <label htmlFor={ids.pain} className="text-base font-semibold text-slate-900">
          What hurts right now? <span className="font-normal text-slate-700">(optional)</span>
        </label>
        <p id={ids.painHint} className="mt-1 text-sm text-slate-700">
          One sentence. Clips about it rank higher.
        </p>
        <input
          id={ids.pain}
          type="text"
          maxLength={200}
          aria-describedby={ids.painHint}
          value={pain}
          onChange={(e) => setPain(e.target.value)}
          placeholder="Our Argo CD syncs are slow"
          className={fieldClass}
        />
      </div>

      {/* Attended talks */}
      <details className="group rounded-xl border border-slate-300 bg-white">
        <summary className="cursor-pointer rounded-xl px-4 py-3 text-base font-semibold text-slate-900 hover:bg-slate-50">
          Talks we attended <span className="font-normal text-slate-700">(optional)</span>
          {attended.length > 0 ? (
            <span className="ml-2 text-sm font-normal text-slate-700">· {attended.length} selected</span>
          ) : null}
        </summary>
        <div className="border-t border-slate-200 p-4">
          <p className="text-sm text-slate-700">Tick talks someone on the team saw, then filter the playlist to what you missed.</p>
          {talks.status === 'loading' ? (
            <p className="mt-3 flex items-center gap-2 text-sm text-slate-700">
              <Spinner /> Loading talks…
            </p>
          ) : null}
          {talks.status === 'error' ? (
            <div className="mt-3 rounded-lg border border-red-300 bg-red-50 p-3 text-sm text-red-900">
              <p>Could not load the talk list: {talks.message}</p>
              <button type="button" onClick={retryTalks} className={`${secondaryButton} mt-2`}>
                Try again
              </button>
            </div>
          ) : null}
          {talks.status === 'ready' ? (
            <fieldset className="mt-3">
              <legend className="sr-only">Talks we attended</legend>
              <label htmlFor={ids.talkFilter} className="text-sm font-medium text-slate-900">
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
                  <li key={talk.talk_id} className="flex gap-3 rounded-lg p-2 hover:bg-slate-50">
                    <input
                      id={`${ids.talks}-${i}`}
                      type="checkbox"
                      aria-describedby={`${ids.talks}-${i}-by`}
                      checked={attended.includes(talk.talk_id)}
                      onChange={() => toggleTalk(talk.talk_id)}
                      className="mt-1 size-4 shrink-0 cursor-pointer accent-indigo-700"
                    />
                    <div>
                      <label htmlFor={`${ids.talks}-${i}`} className="block cursor-pointer text-sm font-medium text-slate-900">
                        {talk.title}
                      </label>
                      <p id={`${ids.talks}-${i}-by`} className="text-sm text-slate-700">
                        {formatSpeakers(talk.speakers)}
                      </p>
                    </div>
                  </li>
                ))}
              </ul>
              {visibleTalks.length === 0 ? <p className="mt-2 text-sm text-slate-700">No talks match that filter.</p> : null}
            </fieldset>
          ) : null}
        </div>
      </details>

      <div>
        <button
          type="submit"
          aria-disabled={busy}
          aria-describedby={formError ? ids.formError : undefined}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-700 px-5 py-3 text-base font-semibold text-white shadow-sm hover:bg-indigo-800 aria-disabled:cursor-not-allowed aria-disabled:opacity-70"
        >
          {building ? <Spinner /> : null}
          {building ? 'Building your playlist…' : 'Build my playlist'}
        </button>
        {formError ? (
          <p id={ids.formError} className="mt-2 text-sm font-medium text-red-800">
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
        <h3 className="font-semibold text-slate-900">Matched tools</h3>
        {result.tools.length === 0 ? (
          <p className="mt-1 text-slate-700">No known tools in this file.</p>
        ) : (
          <ul className="mt-2 flex flex-wrap gap-2">
            {result.tools.map((tool) => (
              <li
                key={tool.id}
                className="inline-flex flex-wrap items-baseline gap-x-1.5 rounded-full border border-emerald-700 bg-emerald-50 px-3 py-1 text-emerald-950"
              >
                <span aria-hidden="true">✓</span>
                <span className="font-medium">{tool.name}</span>
                <span className="text-xs text-emerald-900">
                  {tool.source === 'picked' ? 'picked by you' : `from ${tool.source}`}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>
      {result.unknown_packages.length > 0 ? (
        <div>
          <h3 className="font-semibold text-slate-900">
            Not recognised ({result.unknown_packages.length})
          </h3>
          <p className="text-slate-700">These packages don't map to a tool in our dictionary, so they won't shape the playlist.</p>
          <ul className="mt-1 flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs text-slate-800">
            {result.unknown_packages.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
