// A tiny Markdown parser for the memo: headings, lists, paragraphs, rules, bold,
// inline code and links. It returns a tree that React renders as elements, so model
// text never reaches innerHTML. Links that are not http(s), mailto or same-page fall
// back to plain text.

export type Inline =
  | { kind: 'text'; text: string }
  | { kind: 'strong'; children: Inline[] }
  | { kind: 'code'; text: string }
  | { kind: 'link'; href: string; children: Inline[] }

export type Block =
  | { kind: 'heading'; level: number; children: Inline[] }
  | { kind: 'paragraph'; children: Inline[] }
  | { kind: 'list'; ordered: boolean; items: Inline[][] }
  | { kind: 'rule' }

const SAFE_HREF = /^(https?:\/\/|mailto:|#)/i

export function isSafeHref(href: string): boolean {
  return SAFE_HREF.test(href.trim())
}

const INLINE = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)/g

export function parseInline(text: string): Inline[] {
  const out: Inline[] = []
  let last = 0
  for (const match of text.matchAll(INLINE)) {
    const index = match.index ?? 0
    if (index > last) out.push({ kind: 'text', text: text.slice(last, index) })
    const [whole, bold, code, linkText, href] = match
    if (bold !== undefined) out.push({ kind: 'strong', children: parseInline(bold) })
    else if (code !== undefined) out.push({ kind: 'code', text: code })
    else if (linkText !== undefined && href !== undefined && isSafeHref(href)) {
      out.push({ kind: 'link', href, children: parseInline(linkText) })
    } else out.push({ kind: 'text', text: linkText ?? whole })
    last = index + whole.length
  }
  if (last < text.length) out.push({ kind: 'text', text: text.slice(last) })
  return out
}

const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/
const BULLET = /^\s*[-*+]\s+(.*)$/
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/

export function parseMarkdown(markdown: string): Block[] {
  const blocks: Block[] = []
  let paragraph: string[] = []
  let list: { ordered: boolean; items: string[] } | null = null

  const flushParagraph = () => {
    if (paragraph.length) blocks.push({ kind: 'paragraph', children: parseInline(paragraph.join(' ')) })
    paragraph = []
  }
  const flushList = () => {
    if (list) blocks.push({ kind: 'list', ordered: list.ordered, items: list.items.map(parseInline) })
    list = null
  }

  for (const line of markdown.replace(/\r\n?/g, '\n').split('\n')) {
    if (!line.trim()) {
      flushParagraph()
      flushList()
      continue
    }
    const heading = HEADING.exec(line)
    if (heading) {
      flushParagraph()
      flushList()
      blocks.push({ kind: 'heading', level: heading[1].length, children: parseInline(heading[2]) })
      continue
    }
    if (RULE.test(line)) {
      flushParagraph()
      flushList()
      blocks.push({ kind: 'rule' })
      continue
    }
    const bullet = BULLET.exec(line)
    const numbered = bullet ? null : NUMBERED.exec(line)
    const item = bullet ?? numbered
    if (item) {
      flushParagraph()
      const ordered = numbered !== null
      if (list && list.ordered !== ordered) flushList()
      list ??= { ordered, items: [] }
      list.items.push(item[1])
      continue
    }
    if (list && /^\s+/.test(line)) {
      // An indented continuation of the last list item.
      list.items[list.items.length - 1] += ` ${line.trim()}`
      continue
    }
    flushList()
    paragraph.push(line.trim())
  }
  flushParagraph()
  flushList()
  return blocks
}
