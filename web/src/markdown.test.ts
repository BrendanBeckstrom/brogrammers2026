import { describe, expect, it } from 'vitest'
import { parseInline, parseMarkdown } from './markdown'

describe('parseInline', () => {
  it('parses bold, code and links', () => {
    expect(parseInline('Try **sharding** with `rate()` [here](https://example.com/a).')).toEqual([
      { kind: 'text', text: 'Try ' },
      { kind: 'strong', children: [{ kind: 'text', text: 'sharding' }] },
      { kind: 'text', text: ' with ' },
      { kind: 'code', text: 'rate()' },
      { kind: 'text', text: ' ' },
      { kind: 'link', href: 'https://example.com/a', children: [{ kind: 'text', text: 'here' }] },
      { kind: 'text', text: '.' },
    ])
  })

  it('drops unsafe link targets to plain text', () => {
    expect(parseInline('[click](javascript:alert(1))')).toEqual([
      { kind: 'text', text: 'click' },
      { kind: 'text', text: ')' },
    ])
  })
})

describe('parseMarkdown', () => {
  it('splits headings, lists, paragraphs and rules', () => {
    const blocks = parseMarkdown('# Title\n\nSome text\nmore text.\n\n- one\n- two\n  continued\n\n1. first\n2. second\n\n---\n')
    expect(blocks).toEqual([
      { kind: 'heading', level: 1, children: [{ kind: 'text', text: 'Title' }] },
      { kind: 'paragraph', children: [{ kind: 'text', text: 'Some text more text.' }] },
      {
        kind: 'list',
        ordered: false,
        items: [[{ kind: 'text', text: 'one' }], [{ kind: 'text', text: 'two continued' }]],
      },
      {
        kind: 'list',
        ordered: true,
        items: [[{ kind: 'text', text: 'first' }], [{ kind: 'text', text: 'second' }]],
      },
      { kind: 'rule' },
    ])
  })
})
