import React, { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  InputNumber,
  Popover,
  Radio,
  Select,
  Space,
  Tabs,
  Tag,
  Tree,
  Typography,
  message,
} from 'antd'
import {
  DownloadOutlined,
  LeftOutlined,
  RightOutlined,
  SwapOutlined,
  TranslationOutlined,
} from '@ant-design/icons'
import * as pdfjsLib from 'pdfjs-dist'
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.min.mjs?url'
import { api, getToken } from '../api/client'
import type { Paper, ReferenceItem, SectionItem } from '../types'
import { MarkdownView } from './MarkdownView'

pdfjsLib.GlobalWorkerOptions.workerSrc = pdfjsWorker

const { Text } = Typography

interface TransItem {
  block_id: number
  type: string
  page: number
  bbox: number[]
  section: string
  source: string
  translation: string
}

interface Props {
  papers: Paper[]
  initialPaperId: number | null
  initialPage: number
  highlightBbox?: number[]
}

export const ReaderView: React.FC<Props> = ({
  papers,
  initialPaperId,
  initialPage,
  highlightBbox,
}) => {
  const readyPapers = papers.filter((p) => p.status === 'ready')
  const [paperId, setPaperId] = useState<number | null>(initialPaperId || readyPapers[0]?.id || null)
  const [page, setPage] = useState<number>(initialPage || 1)
  const [numPages, setNumPages] = useState<number>(1)
  const [scale, setScale] = useState<number>(1.25)
  const [sections, setSections] = useState<SectionItem[]>([])
  const [references, setReferences] = useState<ReferenceItem[]>([])
  const [transItems, setTransItems] = useState<TransItem[]>([])
  const [translating, setTranslating] = useState(false)
  const [activeRightTab, setActiveRightTab] = useState<'bilingual' | 'citations'>('bilingual')
  const [activeRefIdx, setActiveRefIdx] = useState<number | null>(null)
  const [activeBbox, setActiveBbox] = useState<number[] | undefined>(highlightBbox)

  // 划词翻译
  const [selectedText, setSelectedText] = useState('')
  const [snippetResult, setSnippetResult] = useState<{ translation: string; terms: Array<{ en: string; zh: string }> } | null>(null)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const textLayerRef = useRef<HTMLDivElement | null>(null)
  const pdfDocRef = useRef<pdfjsLib.PDFDocumentProxy | null>(null)
  const [viewportDims, setViewportDims] = useState<{ width: number; height: number; pdfW: number; pdfH: number }>({
    width: 600,
    height: 800,
    pdfW: 612,
    pdfH: 792,
  })

  // 同步滚动容器引用
  const leftPaneRef = useRef<HTMLDivElement | null>(null)
  const rightPaneRef = useRef<HTMLDivElement | null>(null)
  const syncingRef = useRef(false)

  useEffect(() => {
    if (initialPaperId) setPaperId(initialPaperId)
  }, [initialPaperId])

  useEffect(() => {
    if (initialPage) setPage(initialPage)
  }, [initialPage])

  useEffect(() => {
    if (highlightBbox?.length === 4) setActiveBbox(highlightBbox)
  }, [highlightBbox])

  // 加载 PDF.js 文档 + 标题树 + 参考文献映射 + 双语块
  useEffect(() => {
    if (!paperId) return
    let cancelled = false

    const loadAll = async () => {
      try {
        const [cmap, st, tr] = await Promise.all([
          api<{ references: ReferenceItem[] }>(`/papers/${paperId}/citations-map`),
          api<{ sections: SectionItem[]; paper: Paper }>(`/papers/${paperId}/structure?page_from=1&page_to=2`),
          api<{ items: TransItem[] }>(`/papers/${paperId}/translation?page_from=1&page_to=30`),
        ])
        if (cancelled) return
        setReferences(cmap.references)
        setSections(st.sections)
        setTransItems(tr.items)

        const task = pdfjsLib.getDocument({
          url: `/api/papers/${paperId}/file`,
          httpHeaders: { Authorization: `Bearer ${getToken()}` },
        })
        const doc = await task.promise
        if (cancelled) return
        pdfDocRef.current = doc
        setNumPages(doc.numPages)
      } catch (e) {
        if (!cancelled) message.error((e as Error).message)
      }
    }
    void loadAll()
    return () => {
      cancelled = true
    }
  }, [paperId])

  // 渲染当前 PDF 页面 + 原生文本层
  useEffect(() => {
    const doc = pdfDocRef.current
    const canvas = canvasRef.current
    const textLayer = textLayerRef.current
    if (!doc || !canvas || !textLayer) return
    let cancelled = false

    const renderPage = async () => {
      const pno = Math.max(1, Math.min(page, doc.numPages))
      const pg = await doc.getPage(pno)
      if (cancelled) return
      const unscaled = pg.getViewport({ scale: 1.0 })
      const vp = pg.getViewport({ scale })
      canvas.width = vp.width
      canvas.height = vp.height
      setViewportDims({ width: vp.width, height: vp.height, pdfW: unscaled.width, pdfH: unscaled.height })

      const ctx = canvas.getContext('2d')
      if (!ctx) return
      await pg.render({ canvasContext: ctx, viewport: vp }).promise

      // 渲染透明可选中文本层（用于划词翻译 & 识别正文 [11] 点击跳转）
      textLayer.innerHTML = ''
      textLayer.style.width = `${vp.width}px`
      textLayer.style.height = `${vp.height}px`
      const textContent = await pg.getTextContent()
      const tl = new pdfjsLib.TextLayer({
        textContentSource: textContent,
        container: textLayer,
        viewport: vp,
      })
      await tl.render()
    }
    void renderPage()
    return () => {
      cancelled = true
    }
  }, [paperId, page, scale, numPages])

  // 双向同步滚动（左右双栏对比阅读模式）
  const handleSyncScroll = (source: 'left' | 'right') => {
    if (syncingRef.current) return
    const srcEl = source === 'left' ? leftPaneRef.current : rightPaneRef.current
    const dstEl = source === 'left' ? rightPaneRef.current : leftPaneRef.current
    if (!srcEl || !dstEl) return
    const maxSrc = srcEl.scrollHeight - srcEl.clientHeight
    const maxDst = dstEl.scrollHeight - dstEl.clientHeight
    if (maxSrc <= 0 || maxDst <= 0) return
    syncingRef.current = true
    dstEl.scrollTop = (srcEl.scrollTop / maxSrc) * maxDst
    requestAnimationFrame(() => {
      syncingRef.current = false
    })
  }

  // 鼠标划词翻译
  const handleTextSelect = () => {
    const sel = window.getSelection()?.toString().trim() || ''
    if (sel.length >= 4 && sel.length <= 1500) {
      setSelectedText(sel)
      setSnippetResult(null)
    }
  }

  const runSnippetTranslate = async () => {
    if (!selectedText) return
    try {
      const res = await api<{ translation: string; terms: Array<{ en: string; zh: string }> }>('/translate/snippet', {
        method: 'POST',
        body: JSON.stringify({ text: selectedText }),
      })
      setSnippetResult(res)
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const runFullTranslate = async () => {
    if (!paperId) return
    setTranslating(true)
    try {
      const res = await api<{ task_id: number }>(`/papers/${paperId}/translate`, {
        method: 'POST',
        body: JSON.stringify({ page_from: 1 }),
      })
      message.info('已启动整篇学术翻译（代码与公式保留英文）')
      const poll = setInterval(async () => {
        const t = await api<{ status: string; progress: number }>(`/tasks/${res.task_id}`)
        if (t.status === 'done' || t.status === 'failed') {
          clearInterval(poll)
          setTranslating(false)
          const tr = await api<{ items: TransItem[] }>(`/papers/${paperId}/translation?page_from=1`)
          setTransItems(tr.items)
          message.success('翻译已更新')
        }
      }, 2000)
    } catch (e) {
      setTranslating(false)
      message.error((e as Error).message)
    }
  }

  const jumpToPage = (targetPage: number, bbox?: number[]) => {
    setPage(targetPage)
    setActiveBbox(bbox && bbox.length === 4 ? bbox : undefined)
  }

  // 将正文文本中的 [11] 渲染成可点击跳转参考文献的交互按钮
  const renderSourceWithCiteJumps = (text: string) => {
    const parts = text.split(/(\[\d+(?:\s*,\s*\d+)*\])/g)
    if (parts.length === 1) return <MarkdownView content={text} />
    return (
      <div style={{ lineHeight: 1.7 }}>
        {parts.map((part, idx) => {
          const m = part.match(/^\[(\d+)(?:\s*,.*)?\]$/)
          if (!m) return <span key={idx}>{part}</span>
          const refIdx = Number(m[1])
          const targetRef = references.find((r) => r.idx === refIdx)
          return (
            <Tag
              key={idx}
              color="geekblue"
              style={{ cursor: targetRef ? 'pointer' : 'default', margin: '0 2px' }}
              title={targetRef ? `点击跳转至参考文献 [${refIdx}]: ${targetRef.title}` : part}
              onClick={() => {
                if (targetRef) {
                  setActiveRightTab('citations')
                  setActiveRefIdx(refIdx)
                  if (targetRef.page) jumpToPage(targetRef.page, targetRef.bbox)
                }
              }}
            >
              {part}
            </Tag>
          )
        })}
      </div>
    )
  }

  // 构建章节树
  const treeData = (() => {
    type N = { key: string; title: string; page: number; children: N[] }
    const map = new Map<number, N>()
    const roots: N[] = []
    for (const s of sections) {
      map.set(s.id, { key: `${s.id}:${s.page}`, title: s.title, page: s.page, children: [] })
    }
    for (const s of sections) {
      const node = map.get(s.id)!
      if (s.parent_id && map.has(s.parent_id)) map.get(s.parent_id)!.children.push(node)
      else roots.push(node)
    }
    return roots
  })()

  // 计算 PDF 页面内高光框坐标（PDF 原点左下 -> Canvas 原点左上）
  const highlightStyle = (() => {
    if (!activeBbox || activeBbox.length !== 4) return null
    const [x0, y0, x1, y1] = activeBbox
    const sx = viewportDims.width / Math.max(1, viewportDims.pdfW)
    const sy = viewportDims.height / Math.max(1, viewportDims.pdfH)
    return {
      left: x0 * sx,
      top: y0 * sy,
      width: Math.max(20, (x1 - x0) * sx),
      height: Math.max(14, (y1 - y0) * sy),
    }
  })()

  if (!readyPapers.length) {
    return <Empty description="请先在「文献库」上传并解析至少一篇 PDF 论文" />
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, height: 'calc(100vh - 110px)' }}>
      {/* 顶部工具栏 */}
      <Card size="small" bodyStyle={{ padding: '8px 14px' }}>
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Select
              style={{ width: 280 }}
              value={paperId}
              onChange={(v) => {
                setPaperId(v)
                setPage(1)
                setActiveBbox(undefined)
              }}
              options={readyPapers.map((p) => ({ value: p.id, label: p.title || p.filename }))}
            />
            <Button icon={<LeftOutlined />} disabled={page <= 1} onClick={() => setPage((p) => p - 1)} />
            <Space size={4}>
              <InputNumber min={1} max={numPages} value={page} onChange={(v) => v && setPage(v)} size="small" style={{ width: 64 }} />
              <Text type="secondary">/ {numPages}</Text>
            </Space>
            <Button icon={<RightOutlined />} disabled={page >= numPages} onClick={() => setPage((p) => p + 1)} />
            <Radio.Group value={scale} onChange={(e) => setScale(e.target.value)} size="small">
              <Radio.Button value={1.0}>100%</Radio.Button>
              <Radio.Button value={1.25}>125%</Radio.Button>
              <Radio.Button value={1.5}>150%</Radio.Button>
            </Radio.Group>
          </Space>

          <Space wrap>
            {selectedText && (
              <Popover
                open={Boolean(snippetResult)}
                onOpenChange={(o) => !o && setSnippetResult(null)}
                title="划词学术翻译"
                content={
                  snippetResult && (
                    <div style={{ maxWidth: 380 }}>
                      <p>{snippetResult.translation}</p>
                      {snippetResult.terms.length > 0 && (
                        <div>
                          {snippetResult.terms.map((t, i) => (
                            <Tag key={i} color="cyan">
                              {t.en} → {t.zh}
                            </Tag>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                }
              >
                <Button type="primary" size="small" icon={<TranslationOutlined />} onClick={runSnippetTranslate}>
                  划词翻译 ({selectedText.slice(0, 14)}…)
                </Button>
              </Popover>
            )}
            <Button
              type="primary"
              ghost
              icon={<TranslationOutlined />}
              loading={translating}
              onClick={runFullTranslate}
            >
              整篇翻译（保留公式/代码）
            </Button>
            {paperId && (
              <Button
                icon={<DownloadOutlined />}
                href={`/api/papers/${paperId}/translation/export?fmt=md&token=${encodeURIComponent(getToken())}`}
                target="_blank"
              >
                导出译文 MD
              </Button>
            )}
          </Space>
        </Space>
      </Card>

      {/* 三栏主阅读区：标题树 + 原生 PDF.js + 中英联动/双向引用面板 */}
      <div style={{ display: 'grid', gridTemplateColumns: '230px 1.15fr 1fr', gap: 12, flex: 1, minHeight: 0 }}>
        {/* 左：标题树导航 */}
        <Card size="small" title="标题树导航" bodyStyle={{ overflow: 'auto', height: 'calc(100% - 40px)', padding: 8 }}>
          <Tree
            treeData={treeData}
            defaultExpandAll
            onSelect={(keys) => {
              const k = String(keys[0] || '')
              const pg = Number(k.split(':')[1])
              if (pg) jumpToPage(pg)
            }}
          />
        </Card>

        {/* 中：原生 PDF.js 版式渲染（单双栏/公式/表格保真） */}
        <div
          style={{
            overflow: 'auto',
            background: '#e2e8f0',
            borderRadius: 8,
            padding: 16,
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'flex-start',
          }}
          onMouseUp={handleTextSelect}
        >
          <div style={{ position: 'relative', boxShadow: '0 4px 20px rgba(0,0,0,0.15)', background: '#fff' }}>
            <canvas ref={canvasRef} style={{ display: 'block' }} />
            <div ref={textLayerRef} className="textLayer" />
            {highlightStyle && (
              <div
                style={{
                  position: 'absolute',
                  ...highlightStyle,
                  background: 'rgba(250, 204, 21, 0.35)',
                  border: '2px solid #eab308',
                  borderRadius: 3,
                  pointerEvents: 'none',
                }}
              />
            )}
          </div>
        </div>

        {/* 右：中英同步滚动对比 & 双向引用跳转 */}
        <Card
          size="small"
          bodyStyle={{ padding: '0 12px 12px', height: '100%', display: 'flex', flexDirection: 'column' }}
          style={{ minHeight: 0 }}
        >
          <Tabs
            activeKey={activeRightTab}
            onChange={(k) => setActiveRightTab(k as 'bilingual' | 'citations')}
            items={[
              {
                key: 'bilingual',
                label: (
                  <Space>
                    <SwapOutlined />
                    <span>中英对比联动滚动</span>
                  </Space>
                ),
                children: (
                  <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, height: 'calc(100vh - 235px)' }}>
                    <div
                      ref={leftPaneRef}
                      onScroll={() => handleSyncScroll('left')}
                      onMouseUp={handleTextSelect}
                      style={{ overflowY: 'auto', paddingRight: 6, borderRight: '1px solid #e2e8f0' }}
                    >
                      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
                        原文（点击蓝色 [n] 跳转至参考文献）
                      </Text>
                      {transItems.map((it) => (
                        <div
                          key={it.block_id}
                          style={{
                            marginBottom: 12,
                            padding: 8,
                            borderRadius: 6,
                            background: it.page === page ? '#eff6ff' : '#f8fafc',
                            borderLeft: it.page === page ? '3px solid #2563eb' : '3px solid transparent',
                          }}
                          onClick={() => jumpToPage(it.page, it.bbox)}
                        >
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            p.{it.page} · {it.section || it.type}
                          </Text>
                          {it.type === 'formula' ? (
                            <MarkdownView content={it.source} />
                          ) : (
                            renderSourceWithCiteJumps(it.source)
                          )}
                        </div>
                      ))}
                    </div>

                    <div
                      ref={rightPaneRef}
                      onScroll={() => handleSyncScroll('right')}
                      style={{ overflowY: 'auto', paddingRight: 4 }}
                    >
                      <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 8 }}>
                        学术中文译文（公式与代码保留英文）
                      </Text>
                      {transItems.map((it) => (
                        <div
                          key={it.block_id}
                          style={{
                            marginBottom: 12,
                            padding: 8,
                            borderRadius: 6,
                            background: it.page === page ? '#f0fdf4' : '#fafafa',
                          }}
                        >
                          <Text type="secondary" style={{ fontSize: 11 }}>
                            p.{it.page} 译文
                          </Text>
                          {it.translation ? (
                            <MarkdownView content={it.translation} />
                          ) : (
                            <Text type="secondary" italic>
                              尚未翻译（点击上方「整篇翻译」）
                            </Text>
                          )}
                        </div>
                      ))}
                    </div>
                  </div>
                ),
              },
              {
                key: 'citations',
                label: `双向引用跳转 (${references.length})`,
                children: (
                  <div style={{ overflowY: 'auto', height: 'calc(100vh - 235px)' }}>
                    <Alert
                      type="info"
                      showIcon
                      style={{ marginBottom: 10 }}
                      message="点击左侧原文 [n] 可直达对应条目；点击下方「↑ 跳回正文引用处」可反向定位正文中所有引用该文献的段落。"
                    />
                    {references.map((r) => (
                      <Card
                        key={r.id}
                        size="small"
                        style={{
                          marginBottom: 8,
                          borderColor: activeRefIdx === r.idx ? '#2563eb' : undefined,
                          background: activeRefIdx === r.idx ? '#eff6ff' : undefined,
                        }}
                      >
                        <Text strong>[{r.idx}]</Text> <Text>{r.raw_text}</Text>
                        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                          {r.page > 0 && (
                            <Tag style={{ cursor: 'pointer' }} onClick={() => jumpToPage(r.page, r.bbox)}>
                              References p.{r.page}
                            </Tag>
                          )}
                          {r.cite_contexts.map((ctx, i) => (
                            <Tag
                              key={i}
                              color="green"
                              style={{ cursor: 'pointer' }}
                              title={ctx.snippet}
                              onClick={() => jumpToPage(ctx.page, ctx.bbox)}
                            >
                              ↑ 跳回正文引用处 p.{ctx.page}
                            </Tag>
                          ))}
                        </div>
                      </Card>
                    ))}
                  </div>
                ),
              },
            ]}
          />
        </Card>
      </div>
    </div>
  )
}
