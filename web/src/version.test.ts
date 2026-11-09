import { describe, expect, it } from 'vitest'
import { fetchVersion } from './version'

const respond = (status: number, body: unknown): typeof fetch => async () =>
  new Response(JSON.stringify(body), { status })

describe('fetchVersion', () => {
  it('returns the version from /api/version', async () => {
    expect(await fetchVersion(respond(200, { version: 'abc1234' }))).toBe('abc1234')
  })

  it('returns null when the API fails', async () => {
    expect(await fetchVersion(respond(500, {}))).toBeNull()
    expect(await fetchVersion(async () => Promise.reject(new Error('offline')))).toBeNull()
  })
})
