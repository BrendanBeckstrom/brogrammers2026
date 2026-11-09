// Shared Tailwind class strings, built on the colour tokens in index.css, so every
// button, field and notice looks the same in light and dark mode.

export const btnPrimary =
  'inline-flex items-center justify-center gap-2 rounded-xl bg-brand px-5 py-3 text-base font-semibold text-brand-ink shadow-sm hover:bg-brand-strong aria-disabled:cursor-not-allowed aria-disabled:opacity-70 motion-safe:transition-colors'

export const btnSecondary =
  'inline-flex items-center justify-center gap-2 rounded-xl border border-line-strong bg-surface px-4 py-2 text-sm font-semibold text-ink hover:bg-surface-2 aria-disabled:cursor-not-allowed aria-disabled:opacity-60 motion-safe:transition-colors'

export const fieldClass =
  'mt-1 block w-full rounded-xl border border-line-strong bg-surface px-3 py-2 text-ink placeholder:text-ink-2'

export const sectionCard = 'rounded-3xl border border-line bg-surface p-5 shadow-sm sm:p-8'

export const errorNotice = 'rounded-xl border border-danger-line bg-danger-bg p-3 text-sm text-danger-text'

export const linkClass = 'font-medium text-brand-text underline underline-offset-2 hover:text-ink'
