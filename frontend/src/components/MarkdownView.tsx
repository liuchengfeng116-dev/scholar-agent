import React, { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import { Tag } from 'antd'
import type { CitationAnchor } from '../types'

interface Props {
  content: string
  citations?: CitationAnchor[]
  onClickCitation?: (cit: CitationAnchor) => void
}

/**
 * 渲染支持 GFM 表格 + KaTeX 公式 + 可点击 [E#] 溯源锚点的学术 Markdown。
 */
export const MarkdownView: React.FC<Props> = ({ content, citations = [], onClickCitation }) => {
  const citMap = useMemo(() => {
    const m = new Map<string, CitationAnchor>()
    for (const c of citations) m.set(c.ref, c)
    return m
  }, [citations])

  // 将文本中的 [E1] 转成可点击引用芯片，或保留原文本
  const renderTextWithRefs = (text: string) => {
    const parts = text.split(/(\[E\d+\])/g)
    if (parts.length === 1) return text
    return parts.map((part, idx) => {
      const m = part.match(/^\[(E\d+)\]$/)
      if (!m) return <React.Fragment key={idx}>{part}</React.Fragment>
      const cit = citMap.get(m[1])
      return (
        <Tag
          key={idx}
          color={cit ? 'blue' : 'default'}
          style={{
            cursor: cit && onClickCitation ? 'pointer' : 'default',
            margin: '0 2px',
            fontSize: 11,
            lineHeight: '16px',
            verticalAlign: 'super',
          }}
          title={cit ? `点击跳转原文：${cit.paper_title} p.${cit.page}` : m[1]}
          onClick={() => cit && onClickCitation?.(cit)}
        >
          {m[1]}
          {cit ? ` p.${cit.page}` : ''}
        </Tag>
      )
    })
  }

  return (
    <div className="scholar-md">
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[rehypeKatex]}
        components={{
          p: ({ children }) => (
            <p style={{ marginBottom: 8 }}>
              {React.Children.map(children, (ch) =>
                typeof ch === 'string' ? renderTextWithRefs(ch) : ch,
              )}
            </p>
          ),
          li: ({ children }) => (
            <li>
              {React.Children.map(children, (ch) =>
                typeof ch === 'string' ? renderTextWithRefs(ch) : ch,
              )}
            </li>
          ),
          td: ({ children }) => (
            <td>
              {React.Children.map(children, (ch) =>
                typeof ch === 'string' ? renderTextWithRefs(ch) : ch,
              )}
            </td>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
