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
          <code key={i} className="rounded bg-slate-100 px-1 py-0.5 text-[0.9em]">
            {node.text}
          </code>
        )
      case 'link': {
        const external = /^https?:/i.test(node.href)
        return (
          <a
            key={i}
            href={node.href}
            className="font-medium text-indigo-800 underline underline-offset-2 hover:text-indigo-950"
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

const headingClass = ['', 'text-xl font-semibold', 'text-lg font-semibold', 'text-base font-semibold']

/** Renders memo Markdown. `headingOffset` shifts `#` down so the memo fits the page outline. */
export function Markdown({ source, headingOffset = 2 }: { source: string; headingOffset?: number }) {
  return (
    <div className="space-y-3 leading-relaxed text-slate-800">
      {parseMarkdown(source).map((block, i) => {
        switch (block.kind) {
          case 'heading': {
            const level = Math.min(6, block.level + headingOffset)
            const Tag = `h${level}` as 'h3'
            return (
              <Tag key={i} className={`${headingClass[Math.min(block.level, 3)]} mt-5 text-slate-900 first:mt-0`}>
                {renderInline(block.children)}
              </Tag>
            )
          }
          case 'paragraph':
            return <p key={i}>{renderInline(block.children)}</p>
          case 'list': {
            const ListTag = block.ordered ? 'ol' : 'ul'
            return (
              <ListTag key={i} className={`space-y-1.5 pl-6 ${block.ordered ? 'list-decimal' : 'list-disc'}`}>
                {block.items.map((item, j) => (
                  <li key={j}>{renderInline(item)}</li>
                ))}
              </ListTag>
            )
          }
          case 'rule':
            return <hr key={i} className="border-slate-200" />
        }
      })}
    </div>
  )
}
