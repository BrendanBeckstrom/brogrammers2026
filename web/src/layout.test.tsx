import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import App from './App'
import { ClipCard } from './components/ClipCard'
import { HERO_SUMMARY } from './components/Landing'
import { PLAYLIST_CLIPS } from './fixtures/data'

describe('landing page', () => {
  const html = renderToStaticMarkup(<App />)

  it('runs hero, how it works, why, example, then the stack form', () => {
    const order = ['hero-heading', 'how-heading', 'why-heading', 'example-heading', 'stack-heading'].map((id) =>
      html.indexOf(`id="${id}"`),
    )
    expect(order.every((i) => i >= 0)).toBe(true)
    expect([...order].sort((a, b) => a - b)).toEqual(order)
  })

  it('describes Debrief instead of showing stat tiles', () => {
    expect(html).toContain(HERO_SUMMARY.replace("'", '&#x27;'))
    expect(html).not.toContain('<dl')
    expect(html).not.toMatch(/tools tracked|\b54 talks/)
  })

  it('links the nav to each section', () => {
    for (const href of ['#how-heading', '#example-heading', '#stack-heading']) expect(html).toContain(`href="${href}"`)
  })
})

describe('clip card quote', () => {
  const clip = PLAYLIST_CLIPS[0]
  const html = renderToStaticMarkup(<ClipCard clip={clip} index={0} toolNames={new Map()} />)

  it('starts collapsed, with a toggle that controls the quote', () => {
    const quoteId = /<blockquote id="([^"]+)"[^>]*class="[^"]*line-clamp-2/.exec(html)?.[1]
    expect(quoteId).toBeTruthy()
    expect(html).toMatch(new RegExp(`aria-expanded="false" aria-controls="${quoteId}"`))
    expect(html).toContain('Show full quote')
  })

  it('keeps the why line and the watch hook outside the collapsed quote', () => {
    expect(html).toContain('Why for you: ')
    expect(html).toContain(`data-rum-event="watch_click" data-rank="1" data-clip-id="${clip.clip_id}"`)
  })
})
