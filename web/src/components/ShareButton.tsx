import { useId, useState } from 'react'
import { ApiError, errorMessage, savePlaylist, shareLink, type PlaylistRequest } from '../api'
import { btnSecondary, fieldClass } from '../ui'
import { CheckIcon, LinkIcon } from './Icons'
import { Spinner } from './Spinner'

type ShareState =
  | { status: 'idle' }
  | { status: 'saving' }
  | { status: 'ready'; link: string; copied: boolean }
  | { status: 'error'; message: string }

interface Props {
  request: PlaylistRequest
  clipIds: string[]
}

/** Saves the playlist (POST /api/playlists) and copies its `?p=<id>` link. */
export function ShareButton({ request, clipIds }: Props) {
  const linkId = useId()
  const [state, setState] = useState<ShareState>({ status: 'idle' })

  async function share() {
    setState({ status: 'saving' })
    try {
      const saved = await savePlaylist({ request, clip_ids: clipIds })
      const link = shareLink(window.location.origin, saved.id)
      let copied = false
      try {
        if (navigator.clipboard) {
          await navigator.clipboard.writeText(link)
          copied = true
        }
      } catch {
        copied = false
      }
      setState({ status: 'ready', link, copied })
    } catch (error) {
      const unavailable = error instanceof ApiError && (error.status === 404 || error.status === 405)
      setState({
        status: 'error',
        message: unavailable
          ? 'Sharing is not available on this server yet.'
          : `Could not save the playlist: ${errorMessage(error)}`,
      })
    }
  }

  const status =
    state.status === 'saving'
      ? 'Saving the playlist…'
      : state.status === 'ready'
        ? state.copied
          ? 'Link copied. Anyone with it sees this playlist.'
          : 'Link ready. Copy it from the box below.'
        : state.status === 'error'
          ? state.message
          : ''

  return (
    <div className="flex flex-col items-start gap-2 sm:items-end">
      <button
        type="button"
        onClick={() => {
          if (state.status !== 'saving') void share()
        }}
        aria-disabled={state.status === 'saving'}
        className={btnSecondary}
      >
        {state.status === 'saving' ? <Spinner /> : <LinkIcon />}
        Share this playlist
      </button>
      <output
        aria-live="polite"
        className={`text-sm font-medium sm:text-right ${state.status === 'error' ? 'text-danger-text' : 'text-ink-2'}`}
      >
        {state.status === 'ready' && state.copied ? <CheckIcon className="mr-1 inline size-4 align-[-3px]" /> : null}
        {status}
      </output>
      {state.status === 'ready' ? (
        <div className="w-full sm:w-80">
          <label htmlFor={linkId} className="sr-only">
            Share link
          </label>
          <input
            id={linkId}
            type="text"
            readOnly
            value={state.link}
            onFocus={(e) => e.currentTarget.select()}
            className={`${fieldClass} mt-0 font-mono text-xs`}
          />
        </div>
      ) : null}
    </div>
  )
}
