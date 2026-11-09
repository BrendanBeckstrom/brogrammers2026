import { describe, expect, it } from 'vitest'
import { fetchVersion } from './version'

const respond = (status: number, body: unknown): typeof fetch => async () =>
  new Response(JSON.stringify(body), { status })

describe('fetchVersion', () => {
  it('returns the version from /api/version', async () => {
    expect(await fetchVersion(respond(200, { version: 'abc1234' }))).toBe('abc1234')
  })

  it('sends a traceparent header', async () => {
    let sent: string | null = null
    const capture: typeof fetch = async (_input, init) => {
      sent = new Headers(init?.headers).get('traceparent')
      return new Response(JSON.stringify({ version: 'abc1234' }), { status: 200 })
    }
    await fetchVersion(capture)
    expect(sent).toMatch(/^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/)
  })

  it('returns null when the API fails', async () => {
    expect(await fetchVersion(respond(500, {}))).toBeNull()
    expect(await fetchVersion(async () => Promise.reject(new Error('offline')))).toBeNull()
  })
})
