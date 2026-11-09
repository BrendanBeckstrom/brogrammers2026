import { describe, expect, it } from 'vitest'
import { TAGLINE } from './App'

describe('App', () => {
  it('exports the tagline', () => {
    expect(TAGLINE).toContain('20 minutes')
  })
})
