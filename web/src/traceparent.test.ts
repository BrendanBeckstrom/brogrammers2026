import { describe, expect, it } from 'vitest'
import { traceparent, withTrace } from './traceparent'

const FORMAT = /^00-[0-9a-f]{32}-[0-9a-f]{16}-01$/

describe('traceparent', () => {
  it('is a valid W3C traceparent, fresh on every call', () => {
    const a = traceparent()
    expect(a).toMatch(FORMAT)
    expect(a).not.toMatch(/^00-0{32}-/)
    expect(traceparent()).not.toBe(a)
  })

  it('withTrace keeps existing headers and adds traceparent', () => {
    const init = withTrace({ method: 'POST', headers: { 'content-type': 'application/json' } })
    const headers = new Headers(init.headers)
    expect(init.method).toBe('POST')
    expect(headers.get('content-type')).toBe('application/json')
    expect(headers.get('traceparent')).toMatch(FORMAT)
  })
})
