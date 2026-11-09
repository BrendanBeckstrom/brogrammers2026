import { useId } from 'react'
import { BUILT_AT, CONFERENCE, EXAMPLE_STACKS, toolList, type ExampleStack } from '../examples'
import { btnPrimary, btnSecondary } from '../ui'
import { ArrowDownIcon, ListIcon, MemoIcon, StackIcon } from './Icons'

interface Props {
  toolNames: Map<string, string>
  busy: boolean
  onStart: () => void
  onExample: (example: ExampleStack) => void
}

export const HERO_SUMMARY =
  "Tell Debrief what your team runs and what's hurting. It reads the talks from KubeCon, picks the few minutes that matter to you, and hands you a playlist that opens each video at the right second, plus a one-page memo you can share."

const STEPS = [
  {
    icon: StackIcon,
    title: 'Tell it your stack',
    text: "Pick your team's tools, or paste a go.mod, package.json, requirements.txt or Chart.yaml. Add a sentence about what hurts.",
  },
  {
    icon: ListIcon,
    title: 'Get a playlist of clips',
    text: 'Debrief searches what speakers said and picks the moments that matter to you. Each clip opens the video at the right second.',
  },
  {
    icon: MemoIcon,
    title: 'Share a one-page memo',
    text: 'Takeaways, action items and a watch-together list, every line linked to its clip. Copy it as Markdown.',
  },
]

const BUILT_WITH = [
  { name: 'Elastic', role: 'hybrid search' },
  { name: 'Kubernetes + Radar', role: 'runs and watches it' },
  { name: 'Dash0', role: 'OpenTelemetry' },
  { name: 'Gitar', role: 'PR review' },
  { name: 'Claude', role: 'rerank and memo' },
]

const eyebrow = 'text-sm font-semibold tracking-wide text-accent uppercase'
const sectionHeading = 'mt-1 font-display text-3xl font-semibold text-ink sm:text-4xl'

export function Landing({ toolNames, busy, onStart, onExample }: Props) {
  const examplesHint = useId()

  return (
    <>
      <section aria-labelledby="hero-heading" className="relative overflow-hidden border-b border-line">
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_30rem_at_85%_-10%,var(--brand-soft),transparent_70%),radial-gradient(40rem_24rem_at_-10%_110%,var(--accent-soft),transparent_70%)]"
        />
        <div className="relative mx-auto max-w-4xl px-4 py-14 text-center sm:py-20 lg:py-24">
          <p className={eyebrow}>{BUILT_AT}</p>
          <h1
            id="hero-heading"
            tabIndex={-1}
            className="mt-4 font-display text-4xl leading-[1.08] font-semibold tracking-tight text-balance text-ink sm:text-5xl lg:text-6xl"
          >
            Don't watch 300 hours of KubeCon.{' '}
            <span className="text-brand-text">Watch the 20 minutes that matter to your team.</span>
          </h1>
          <p className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-pretty text-ink-2">{HERO_SUMMARY}</p>
          <div className="mt-9 flex flex-wrap items-center justify-center gap-3">
            <button type="button" onClick={onStart} className={`${btnPrimary} px-6 text-lg`}>
              Try it with your stack
              <ArrowDownIcon className="size-5" />
            </button>
            <a href="#example-heading" className={`${btnSecondary} px-5 py-3 text-base`}>
              See an example
            </a>
          </div>
        </div>
      </section>

      <section aria-labelledby="how-heading" className="border-b border-line bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-12 sm:py-16">
          <p className={eyebrow}>How it works</p>
          <h2 id="how-heading" tabIndex={-1} className={sectionHeading}>
            From your stack to a short playlist
          </h2>
          <ol className="mt-8 grid gap-5 md:grid-cols-3">
            {STEPS.map((step, i) => (
              <li key={step.title} className="rounded-2xl border border-line bg-canvas p-5">
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
        </div>
      </section>

      <section aria-labelledby="why-heading" className="border-b border-line">
        <div className="mx-auto grid max-w-6xl gap-10 px-4 py-12 sm:py-16 lg:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)] lg:gap-16">
          <div>
            <p className={eyebrow}>Why it matters</p>
            <h2 id="why-heading" tabIndex={-1} className={sectionHeading}>
              Conference knowledge rarely leaves the room
            </h2>
            <div className="mt-5 max-w-prose space-y-4 text-lg leading-relaxed text-ink">
              <p>
                One person gets to a handful of talks. The recordings go online, but nobody has time to scrub
                hours of video for the part about their tools, so the team back home hears &ldquo;it was great.&rdquo;
              </p>
              <p>
                Debrief is for the teammates who stayed home and{' '}
                <strong className="font-semibold">for people who couldn't be in the room</strong>. Every clip has
                readable captions, and the whole page works by keyboard and screen reader.
              </p>
            </div>
          </div>
          <div className="lg:pt-9">
            <h3 className={eyebrow}>Built with</h3>
            <ul className="mt-3 flex flex-wrap gap-2">
              {BUILT_WITH.map((item) => (
                <li
                  key={item.name}
                  className="inline-flex items-baseline gap-1.5 rounded-full border border-line-strong bg-surface px-3 py-1.5 text-sm"
                >
                  <span className="font-semibold text-ink">{item.name}</span>
                  <span className="text-ink-2">{item.role}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </section>

      <section aria-labelledby="example-heading" className="border-b border-line bg-surface">
        <div className="mx-auto max-w-6xl px-4 py-12 sm:py-16">
          <p className={eyebrow}>Example</p>
          <h2 id="example-heading" tabIndex={-1} className={sectionHeading}>
            See what a team gets
          </h2>
          <p id={examplesHint} className="mt-3 max-w-2xl text-lg leading-relaxed text-ink-2">
            Pick a team like yours. One click fills in the form below and builds their playlist.
          </p>
          <ul className="mt-8 grid gap-4 md:grid-cols-3">
            {EXAMPLE_STACKS.map((example) => (
              <li key={example.id}>
                <button
                  type="button"
                  aria-describedby={examplesHint}
                  aria-disabled={busy}
                  onClick={() => {
                    if (!busy) onExample(example)
                  }}
                  className="group flex h-full w-full flex-col rounded-2xl border border-line-strong bg-canvas p-5 text-left hover:border-brand hover:bg-brand-soft aria-disabled:cursor-not-allowed aria-disabled:opacity-70 motion-safe:transition-colors"
                >
                  <span className="text-xl font-semibold text-ink">{example.name}</span>
                  <span className="mt-1 block text-sm font-medium text-brand-text">
                    {toolList(example.tools, toolNames)}
                  </span>
                  <span className="mt-2 block flex-1 text-ink-2">
                    {example.pain ? <>&ldquo;{example.pain}&rdquo;</> : example.blurb}
                  </span>
                  <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-brand-text">
                    Build this playlist
                    <span
                      aria-hidden="true"
                      className="motion-safe:transition-transform motion-safe:group-hover:translate-x-0.5"
                    >
                      →
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
          <p className="mt-5 text-sm text-ink-2">
            Clips come from talks recorded at {CONFERENCE}.
          </p>
        </div>
      </section>
    </>
  )
}
