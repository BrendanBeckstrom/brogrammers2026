// Small hand-drawn icons. All are decorative (aria-hidden): every control that
// shows one also has a text label.
import type { ReactNode } from 'react'

function Icon({ children, className = 'size-4' }: { children: ReactNode; className?: string }) {
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={`shrink-0 ${className}`}
    >
      {children}
    </svg>
  )
}

type P = { className?: string }

export const PlayIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="M7 4.5v15l12-7.5z" fill="currentColor" stroke="none" />
  </Icon>
)

export const ReadIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="M4 5h11a3 3 0 0 1 3 3v11H7a3 3 0 0 1-3-3z" />
    <path d="M8 9h6M8 13h6" />
  </Icon>
)

export const ClockIcon = ({ className }: P) => (
  <Icon className={className}>
    <circle cx="12" cy="12" r="8.5" />
    <path d="M12 7.5V12l3 2" />
  </Icon>
)

export const LinkIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
    <path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
  </Icon>
)

export const CopyIcon = ({ className }: P) => (
  <Icon className={className}>
    <rect x="8" y="8" width="12" height="12" rx="2" />
    <path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2" />
  </Icon>
)

export const CheckIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </Icon>
)

export const InfoIcon = ({ className }: P) => (
  <Icon className={className}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 11v5.5M12 7.5v.01" />
  </Icon>
)

export const SearchIcon = ({ className }: P) => (
  <Icon className={className}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </Icon>
)

export const ArrowDownIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="M12 5v14M6 13l6 6 6-6" />
  </Icon>
)

export const ExternalIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="M14 5h5v5M19 5l-8 8" />
    <path d="M18 14v4a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h4" />
  </Icon>
)

export const MemoIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20z" />
    <path d="M14 3.5V8h4M10 12h5M10 15.5h5" />
  </Icon>
)

export const StackIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="m12 3.5 8.5 4.5-8.5 4.5L3.5 8z" />
    <path d="m3.5 12 8.5 4.5 8.5-4.5M3.5 16l8.5 4.5 8.5-4.5" />
  </Icon>
)

export const ListIcon = ({ className }: P) => (
  <Icon className={className}>
    <path d="M9 6.5h11M9 12h11M9 17.5h11" />
    <path d="M4 5.5 6 6.5 4 7.5zM4 11l2 1-2 1zM4 16.5l2 1-2 1z" fill="currentColor" />
  </Icon>
)

export const GitHubIcon = ({ className = 'size-4' }: P) => (
  <svg aria-hidden="true" focusable="false" viewBox="0 0 24 24" fill="currentColor" className={`shrink-0 ${className}`}>
    <path d="M12 2a10 10 0 0 0-3.16 19.49c.5.09.68-.22.68-.48v-1.7c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.11-1.47-1.11-1.47-.91-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.9 1.52 2.34 1.08 2.91.83.09-.65.35-1.08.63-1.33-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.65 0 0 .84-.27 2.75 1.02a9.5 9.5 0 0 1 5 0c1.91-1.29 2.75-1.02 2.75-1.02.55 1.38.2 2.4.1 2.65.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.68-4.57 4.93.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10 10 0 0 0 12 2z" />
  </svg>
)

/** The Debrief mark: a playlist card with a play cue. */
export const LogoMark = ({ className = 'size-8' }: P) => (
  <svg aria-hidden="true" focusable="false" viewBox="0 0 32 32" className={`shrink-0 ${className}`}>
    <rect x="1" y="1" width="30" height="30" rx="9" fill="var(--brand)" />
    <path d="M9 10.5h9M9 16h14M9 21.5h7" stroke="var(--brand-ink)" strokeWidth="2.4" strokeLinecap="round" />
    <path d="M20.5 19v6l5-3z" fill="var(--accent)" />
  </svg>
)
