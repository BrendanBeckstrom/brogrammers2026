import { useId } from 'react'
import { BUILT_AT, CONFERENCE, EXAMPLE_STACKS, STATIC_CLIP_COUNT, toolList, type ExampleStack } from '../examples'
import { btnPrimary } from '../ui'
import { ArrowDownIcon, ListIcon, MemoIcon, StackIcon } from './Icons'

interface Props {
  talkCount: number
  toolCount: number | null
  toolNames: Map<string, string>
  busy: boolean
  onStart: () => void
  onExample: (example: ExampleStack) => void
}

const STEPS = [
  {
    icon: StackIcon,
    title: 'Tell it your stack',
    text: 'Pick the tools your team runs, or paste a go.mod, package.json, requirements.txt or Chart.yaml. Add one sentence about what hurts.',
  },
  {
    icon: ListIcon,
    title: 'Get a cited clip playlist',
    text: 'Hybrid search over every transcript finds the few minutes that matter. Each clip opens the recording at its first second.',
  },
  {
    icon: MemoIcon,
    title: 'Share a one-page memo',
    text: 'Three takeaways, action items and a watch-together list, every line linked to its clip. Copy it as Markdown.',
  },
]

const BUILT_WITH = [
  { name: 'Elastic', role: 'hybrid search' },
  { name: 'Kubernetes + Radar', role: 'runs and watches it' },
  { name: 'Dash0', role: 'OpenTelemetry' },
  { name: 'Gitar', role: 'PR review' },
  { name: 'Claude', role: 'rerank and memo' },
]

const number = new Intl.NumberFormat('en')

export function Landing({ talkCount, toolCount, toolNames, busy, onStart, onExample }: Props) {
  const examplesHint = useId()

  return (
    <>
      <section aria-labelledby="hero-heading" className="relative overflow-hidden border-b border-line">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_30rem_at_85%_-10%,var(--brand-soft),transparent_70%),radial-gradient(40rem_24rem_at_-10%_110%,var(--accent-soft),transparent_70%)]"
        />
        <div className="relative mx-auto grid max-w-6xl gap-10 px-4 py-12 sm:py-16 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:items-center lg:gap-14 lg:py-20">
          <div>
            <p className="text-sm font-semibold tracking-wide text-accent uppercase">{BUILT_AT}</p>
            <h1
              id="hero-heading"
              className="mt-3 font-display text-4xl leading-[1.08] font-semibold tracking-tight text-balance text-ink sm:text-5xl lg:text-6xl"
            >
              Don't watch 300 hours of KubeCon.{' '}
              <span className="text-brand-text">Watch the 20 minutes that matter to your team.</span>
            </h1>
            <p className="mt-5 max-w-2xl text-lg leading-relaxed text-ink-2">
              Debrief reads what speakers actually said, then hands your team a short playlist of cited clips for your stack,
              each opening at the right second, and a one-page memo to share.
            </p>
            <div className="mt-8 flex flex-wrap items-center gap-3">
              <button type="button" onClick={onStart} className={`${btnPrimary} px-6 text-lg`}>
                Build your team's playlist
                <ArrowDownIcon className="size-5" />
              </button>
              <a href="#how-heading" className="rounded-lg px-2 py-2 font-semibold text-brand-text underline-offset-4 hover:underline">
                How it works
              </a>
            </div>

            <dl className="mt-10 grid max-w-xl grid-cols-3 gap-3 sm:gap-4">
              <Stat label="talks" value={number.format(talkCount)} />
              <Stat label="clips" value={number.format(STATIC_CLIP_COUNT)} />
              <Stat label="tools tracked" value={toolCount === null ? '30' : number.format(toolCount)} />
            </dl>
            <p className="mt-3 max-w-xl text-sm text-ink-2">
              {number.format(talkCount)} talks from {CONFERENCE}, the latest KubeCon with recordings online; this week&apos;s
              Salt Lake City talks are one ingest run away. One free page, no sign-up.
            </p>
          </div>

          <div className="rounded-3xl border border-line bg-surface/90 p-5 shadow-sm backdrop-blur sm:p-6">
            <h2 className="font-display text-2xl font-semibold text-ink">Try an example stack</h2>
            <p id={examplesHint} className="mt-1 text-sm text-ink-2">
              One click fills in the form below and builds the playlist.
            </p>
            <ul className="mt-4 space-y-3">
              {EXAMPLE_STACKS.map((example) => (
                <li key={example.id}>
                  <button
                    type="button"
                    aria-describedby={examplesHint}
                    aria-disabled={busy}
                    onClick={() => {
                      if (!busy) onExample(example)
                    }}
                    className="group block w-full rounded-2xl border border-line-strong bg-surface p-4 text-left hover:border-brand hover:bg-brand-soft aria-disabled:cursor-not-allowed aria-disabled:opacity-70 motion-safe:transition-colors"
                  >
                    <span className="flex items-center justify-between gap-3">
                      <span className="text-lg font-semibold text-ink">{example.name}</span>
                      <span aria-hidden="true" className="text-brand-text motion-safe:transition-transform motion-safe:group-hover:translate-x-0.5">
                        →
                      </span>
                    </span>
                    <span className="mt-1 block text-sm font-medium text-brand-text">
                      {toolList(example.tools, toolNames)}
                    </span>
                    <span className="mt-1 block text-sm text-ink-2">
                      {example.pain ? <>“{example.pain}”</> : example.blurb}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <div className="border-b border-line bg-surface">
        <section aria-labelledby="how-heading" className="mx-auto max-w-6xl px-4 pt-12 sm:pt-14">
          <h2 id="how-heading" tabIndex={-1} className="font-display text-3xl font-semibold text-ink">
            How it works
          </h2>
          <ol className="mt-8 grid gap-6 md:grid-cols-3">
            {STEPS.map((step, i) => (
              <li key={step.title} className="relative rounded-2xl border border-line bg-canvas p-5">
                <div className="flex items-center gap-3">
                  <span className="grid size-10 place-items-center rounded-xl bg-brand-soft text-brand-text">
                    <step.icon className="size-5" />
                  </span>
                  <span className="font-mono text-sm text-ink-2">Step {i + 1}</span>
                </div>
                <h3 className="mt-4 text-lg font-semibold text-ink">{step.title}</h3>
                <p className="mt-1 leading-relaxed text-ink-2">{step.text}</p>
              </li>
            ))}
          </ol>
        </section>

        <div className="mx-auto max-w-6xl px-4 pb-12 sm:pb-14">
          <div className="mt-10 grid gap-8 border-t border-line pt-8 lg:grid-cols-2">
            <div>
              <h2 className="text-sm font-semibold tracking-wide text-accent uppercase">Who it's for</h2>
              <p className="mt-2 leading-relaxed text-ink">
                The teammates who stayed home, the engineer back from the trip with forty tabs open, the manager who
                needs the gist by Monday, and whoever plans the next trip. Above all, it's{' '}
                <strong className="font-semibold">for people who couldn't be in the room</strong>: every clip has
                readable captions, and the whole page works by keyboard and screen reader.
              </p>
            </div>
            <div>
              <h2 className="text-sm font-semibold tracking-wide text-accent uppercase">Built with</h2>
              <ul className="mt-3 flex flex-wrap gap-2">
                {BUILT_WITH.map((item) => (
                  <li
                    key={item.name}
                    className="inline-flex items-baseline gap-1.5 rounded-full border border-line-strong bg-canvas px-3 py-1.5 text-sm"
                  >
                    <span className="font-semibold text-ink">{item.name}</span>
                    <span className="text-ink-2">{item.role}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col-reverse rounded-2xl border border-line bg-surface/80 px-3 py-3 sm:px-4">
      <dt className="text-sm text-ink-2">{label}</dt>
      <dd className="font-display text-2xl font-semibold text-ink tabular-nums sm:text-3xl">{value}</dd>
    </div>
  )
}
