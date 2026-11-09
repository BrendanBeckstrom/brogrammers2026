import type { ReactNode } from 'react'
import { parseMarkdown, type Inline } from '../markdown'

function renderInline(nodes: Inline[]): ReactNode[] {
  return nodes.map((node, i) => {
    switch (node.kind) {
      case 'text':
        return node.text
      case 'strong':
        return <strong key={i}>{renderInline(node.children)}</strong>
      case 'code':
        return (
          <code key={i} className="rounded bg-surface-2 px-1 py-0.5 font-mono text-[0.9em]">
            {node.text}
          </code>
        )
      case 'link': {
        const external = /^https?:/i.test(node.href)
        return (
          <a
            key={i}
            href={node.href}
            className="font-medium text-brand-text underline decoration-1 underline-offset-2 hover:text-ink"
            {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
          >
            {renderInline(node.children)}
            {external ? <span className="sr-only"> (opens in a new tab)</span> : null}
          </a>
        )
      }
    }
  })
}

const headingClass = [
  '',
  'font-display text-2xl font-semibold sm:text-3xl',
  'font-display text-xl font-semibold border-b border-line pb-1',
  'text-base font-semibold',
]

/** Renders memo Markdown. `headingOffset` shifts `#` down so the memo fits the page outline. */
export function Markdown({ source, headingOffset = 2 }: { source: string; headingOffset?: number }) {
  return (
    <div className="space-y-3 leading-relaxed text-ink">
      {parseMarkdown(source).map((block, i) => {
        switch (block.kind) {
          case 'heading': {
            const level = Math.min(6, block.level + headingOffset)
            const Tag = `h${level}` as 'h3'
            return (
              <Tag key={i} className={`${headingClass[Math.min(block.level, 3)]} mt-6 text-ink first:mt-0`}>
                {renderInline(block.children)}
              </Tag>
            )
          }
          case 'paragraph':
            return <p key={i}>{renderInline(block.children)}</p>
          case 'list': {
            const ListTag = block.ordered ? 'ol' : 'ul'
            return (
              <ListTag key={i} className={`space-y-2 pl-6 marker:text-accent ${block.ordered ? 'list-decimal marker:font-semibold' : 'list-disc'}`}>
                {block.items.map((item, j) => (
                  <li key={j}>{renderInline(item)}</li>
                ))}
              </ListTag>
            )
          }
          case 'rule':
            return <hr key={i} className="border-line" />
        }
      })}
    </div>
  )
}
