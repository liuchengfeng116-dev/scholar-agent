import React, { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Input,
  List,
  Radio,
  Select,
  Space,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  BarChartOutlined,
  CheckCircleOutlined,
  CopyOutlined,
  EditOutlined,
  NodeIndexOutlined,
  TranslationOutlined,
  UploadOutlined,
} from '@ant-design/icons'
import mermaid from 'mermaid'
import { api, withAuthToken } from '../api/client'
import type { Paper } from '../types'
import { MarkdownView } from './MarkdownView'

mermaid.initialize({ startOnLoad: false, theme: 'default' })

const { Text, Paragraph } = Typography

const SAMPLE_CSV = `Model,BLEU,ROUGE-L,Speed(x),Memory(GB),Accuracy(%)
Transformer-Base,27.3,41.2,1.0,8.2,84.1
Transformer-Big,28.4,43.5,0.6,15.8,86.7
Sparse-Attn,28.1,42.9,1.4,6.5,85.9
ScholarRAG-Net,29.8,45.6,1.1,9.1,89.4`

interface Props {
  papers: Paper[]
}

export const CopilotView: React.FC<Props> = ({ papers }) => {
  const readyPapers = papers.filter((p) => p.status === 'ready')

  // 1. 无幻觉框架
  const [idea, setIdea] = useState('')
  const [outlinePids, setOutlinePids] = useState<number[]>([])
  const [outlineLang, setOutlineLang] = useState<'en' | 'zh'>('en')
  const [outlining, setOutlining] = useState(false)
  const [outline, setOutline] = useState<{
    title: string
    abstract: string
    introduction: string
    related_work: string
    method_outline: string
    references: Array<{
      key: string
      title: string
      authors: string
      year: number | null
      venue: string
      doi: string
      arxiv_id: string
      url: string
      verification_source: string
    }>
  } | null>(null)

  // 2. 架构拓扑图
  const [diagIdea, setDiagIdea] = useState('')
  const [diagFmt, setDiagFmt] = useState<'mermaid' | 'tikz' | 'graphviz' | 'matplotlib'>('mermaid')
  const [diagLoading, setDiagLoading] = useState(false)
  const [diagRes, setDiagRes] = useState<{
    format: string
    code: string
    caption: string
    preview_url: string
    render_error: string
  } | null>(null)
  const [mermaidSvg, setMermaidSvg] = useState('')

  // 3. CSV 数据可视化
  const [csvText, setCsvText] = useState(SAMPLE_CSV)
  const [chartType, setChartType] = useState<'bar' | 'line' | 'radar3d' | 'table'>('radar3d')
  const [chartTitle, setChartTitle] = useState('Multi-Model Performance Comparison')
  const [chartLoading, setChartLoading] = useState(false)
  const [chartRes, setChartRes] = useState<{
    image_url: string
    code: string
    caption_en: string
    caption_zh: string
    insights: string[]
    latex_table: string
  } | null>(null)

  // 4. 学术中译英
  const [zhText, setZhText] = useState('')
  const [trStyle, setTrStyle] = useState('IEEE / NeurIPS')
  const [trLoading, setTrLoading] = useState(false)
  const [trRes, setTrRes] = useState<{ english: string; notes: string[] } | null>(null)

  // 渲染 Mermaid SVG
  useEffect(() => {
    if (diagRes?.format !== 'mermaid' || !diagRes.code) {
      setMermaidSvg('')
      return
    }
    mermaid
      .render(`mmd_${Date.now()}`, diagRes.code)
      .then(({ svg }) => setMermaidSvg(svg))
      .catch(() => setMermaidSvg(''))
  }, [diagRes])

  const runOutline = async () => {
    if (!idea.trim()) return
    setOutlining(true)
    try {
      const res = await api<NonNullable<typeof outline>>('/copilot/outline', {
        method: 'POST',
        body: JSON.stringify({ idea, paper_ids: outlinePids, lang: outlineLang }),
      })
      setOutline(res)
      message.success('已生成经过真实参考文献校验的论文框架')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setOutlining(false)
    }
  }

  const runDiagram = async () => {
    if (!diagIdea.trim()) return
    setDiagLoading(true)
    try {
      const res = await api<NonNullable<typeof diagRes>>('/copilot/diagram', {
        method: 'POST',
        body: JSON.stringify({ idea: diagIdea, format: diagFmt }),
      })
      setDiagRes(res)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setDiagLoading(false)
    }
  }

  const runChart = async (file?: File) => {
    setChartLoading(true)
    try {
      const fd = new FormData()
      fd.append('chart_type', chartType)
      fd.append('title', chartTitle)
      if (file) fd.append('file', file)
      else fd.append('csv_text', csvText)
      const res = await api<NonNullable<typeof chartRes>>('/copilot/chart', {
        method: 'POST',
        body: fd,
      })
      setChartRes(res)
      message.success('图表与学术图注生成成功')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setChartLoading(false)
    }
  }

  const runAcademicTranslate = async () => {
    if (!zhText.trim()) return
    setTrLoading(true)
    try {
      const res = await api<NonNullable<typeof trRes>>('/copilot/translate', {
        method: 'POST',
        body: JSON.stringify({ text: zhText, style: trStyle }),
      })
      setTrRes(res)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setTrLoading(false)
    }
  }

  const copyText = async (txt: string) => {
    await navigator.clipboard.writeText(txt)
    message.success('已复制到剪贴板')
  }

  return (
    <Tabs
      items={[
        {
          key: 'outline',
          label: (
            <Space>
              <EditOutlined />
              <span>无幻觉论文框架</span>
            </Space>
          ),
          children: (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Card size="small">
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Input.TextArea
                    rows={3}
                    placeholder="输入您的科研 Idea（例如：设计一种结合图拓扑检索与 Critic 逐句验证的抗幻觉科研多智能体架构）"
                    value={idea}
                    onChange={(e) => setIdea(e.target.value)}
                  />
                  <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Space wrap>
                      <Select
                        mode="multiple"
                        allowClear
                        placeholder="优先引用的库内文献（可选）"
                        style={{ minWidth: 280 }}
                        value={outlinePids}
                        onChange={setOutlinePids}
                        options={readyPapers.map((p) => ({ value: p.id, label: p.title || p.filename }))}
                      />
                      <Radio.Group value={outlineLang} onChange={(e) => setOutlineLang(e.target.value)}>
                        <Radio.Button value="en">英文顶会风格</Radio.Button>
                        <Radio.Button value="zh">中文学术风格</Radio.Button>
                      </Radio.Group>
                    </Space>
                    <Button type="primary" loading={outlining} onClick={runOutline}>
                      生成无幻觉论文框架
                    </Button>
                  </Space>
                </Space>
              </Card>

              {outline && (
                <Card size="small" title={outline.title}>
                  <Space direction="vertical" style={{ width: '100%' }} size={14}>
                    <div>
                      <Tag color="blue">Abstract</Tag>
                      <Paragraph style={{ marginTop: 6 }}>{outline.abstract}</Paragraph>
                    </div>
                    <div>
                      <Tag color="blue">Introduction</Tag>
                      <MarkdownView content={outline.introduction} />
                    </div>
                    <div>
                      <Tag color="blue">Related Work</Tag>
                      <MarkdownView content={outline.related_work} />
                    </div>
                    <div>
                      <Tag color="blue">Method Outline</Tag>
                      <MarkdownView content={outline.method_outline} />
                    </div>
                    <Card
                      size="small"
                      title={
                        <Space>
                          <CheckCircleOutlined style={{ color: '#16a34a' }} />
                          <span>100% 实名校验无幻觉 References ({outline.references.length})</span>
                        </Space>
                      }
                    >
                      <List
                        size="small"
                        dataSource={outline.references}
                        renderItem={(r) => (
                          <List.Item>
                            <div>
                              <Text strong>[{r.key}]</Text> {r.authors} ({r.year || '?'}).{' '}
                              <Text strong>{r.title}</Text>. {r.venue}.{' '}
                              <Tag color="green">已校验: {r.verification_source}</Tag>
                              {r.url && (
                                <a href={r.url} target="_blank" rel="noreferrer">
                                  原文链接
                                </a>
                              )}
                            </div>
                          </List.Item>
                        )}
                      />
                    </Card>
                  </Space>
                </Card>
              )}
            </div>
          ),
        },
        {
          key: 'diagram',
          label: (
            <Space>
              <NodeIndexOutlined />
              <span>架构拓扑图脚本</span>
            </Space>
          ),
          children: (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Card size="small">
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Input.TextArea
                    rows={2}
                    placeholder="描述需要绘制的模型/系统架构（如：输入PDF -> 版面解析 -> 混合检索 -> Planner调度 -> Critic核查 -> 输出带溯源回答）"
                    value={diagIdea}
                    onChange={(e) => setDiagIdea(e.target.value)}
                  />
                  <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Radio.Group value={diagFmt} onChange={(e) => setDiagFmt(e.target.value)}>
                      <Radio.Button value="mermaid">Mermaid（实时矢量预览）</Radio.Button>
                      <Radio.Button value="matplotlib">Matplotlib（沙箱直接渲染 PNG）</Radio.Button>
                      <Radio.Button value="tikz">LaTeX TikZ</Radio.Button>
                      <Radio.Button value="graphviz">Graphviz DOT</Radio.Button>
                    </Radio.Group>
                    <Button type="primary" loading={diagLoading} onClick={runDiagram}>
                      生成架构拓扑图
                    </Button>
                  </Space>
                </Space>
              </Card>

              {diagRes && (
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
                  <Card
                    size="small"
                    title={`${diagRes.format.toUpperCase()} 源码`}
                    extra={
                      <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(diagRes.code)}>
                        复制源码
                      </Button>
                    }
                  >
                    <pre style={{ maxHeight: 420, overflow: 'auto', background: '#0f172a', color: '#e2e8f0', padding: 12, borderRadius: 6, fontSize: 12 }}>
                      {diagRes.code}
                    </pre>
                    {diagRes.caption && <Text type="secondary">图注：{diagRes.caption}</Text>}
                  </Card>

                  <Card size="small" title="可视化预览">
                    {mermaidSvg ? (
                      <div dangerouslySetInnerHTML={{ __html: mermaidSvg }} style={{ overflow: 'auto' }} />
                    ) : diagRes.preview_url ? (
                      <img src={withAuthToken(diagRes.preview_url)} alt="架构图" style={{ maxWidth: '100%' }} />
                    ) : (
                      <Alert
                        type="info"
                        showIcon
                        message="该脚本格式可直接复制到 Overleaf (TikZ) 或 Graphviz 渲染；切换 Mermaid / Matplotlib 可在平台内直接预览。"
                        description={diagRes.render_error}
                      />
                    )}
                  </Card>
                </div>
              )}
            </div>
          ),
        },
        {
          key: 'chart',
          label: (
            <Space>
              <BarChartOutlined />
              <span>CSV 数据可视化与三维雷达图</span>
            </Space>
          ),
          children: (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Card size="small">
                <Space direction="vertical" style={{ width: '100%' }}>
                  <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
                    <Space wrap>
                      <Input
                        value={chartTitle}
                        onChange={(e) => setChartTitle(e.target.value)}
                        style={{ width: 260 }}
                        placeholder="图表标题"
                      />
                      <Radio.Group value={chartType} onChange={(e) => setChartType(e.target.value)}>
                        <Radio.Button value="radar3d">三维雷达图</Radio.Button>
                        <Radio.Button value="bar">柱状图</Radio.Button>
                        <Radio.Button value="line">折线图</Radio.Button>
                        <Radio.Button value="table">学术表格</Radio.Button>
                      </Radio.Group>
                    </Space>
                    <Space>
                      <Upload
                        accept=".csv"
                        showUploadList={false}
                        beforeUpload={(file) => {
                          void runChart(file)
                          return false
                        }}
                      >
                        <Button icon={<UploadOutlined />}>上传 CSV 文件</Button>
                      </Upload>
                      <Button type="primary" loading={chartLoading} onClick={() => runChart()}>
                        从下方 CSV 生成图表与图注
                      </Button>
                    </Space>
                  </Space>
                  <Input.TextArea
                    rows={5}
                    value={csvText}
                    onChange={(e) => setCsvText(e.target.value)}
                    style={{ fontFamily: 'monospace', fontSize: 12 }}
                  />
                </Space>
              </Card>

              {chartRes && (
                <div style={{ display: 'grid', gridTemplateColumns: '1.1fr 1fr', gap: 12 }}>
                  <Card size="small" title="安全沙箱渲染结果">
                    <img
                      src={withAuthToken(chartRes.image_url)}
                      alt={chartTitle}
                      style={{ maxWidth: '100%', display: 'block', margin: 'auto' }}
                    />
                  </Card>
                  <Card size="small" title="自动提炼学术 Figure Caption 与 LaTeX 表格">
                    <Space direction="vertical" style={{ width: '100%' }} size={10}>
                      <Alert type="success" message="英文图注" description={chartRes.caption_en} />
                      <Alert type="info" message="中文图注" description={chartRes.caption_zh} />
                      {chartRes.insights.length > 0 && (
                        <div>
                          <Text strong>核心数据趋势洞察：</Text>
                          <ul style={{ margin: '4px 0 0 18px' }}>
                            {chartRes.insights.map((ins, i) => (
                              <li key={i}>{ins}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                      <div>
                        <Space style={{ justifyContent: 'space-between', width: '100%' }}>
                          <Text strong>LaTeX Booktabs 表格源码：</Text>
                          <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(chartRes.latex_table)}>
                            复制 LaTeX
                          </Button>
                        </Space>
                        <pre style={{ fontSize: 11, background: '#f1f5f9', padding: 8, borderRadius: 6, overflow: 'auto', marginTop: 4 }}>
                          {chartRes.latex_table}
                        </pre>
                      </div>
                    </Space>
                  </Card>
                </div>
              )}
            </div>
          ),
        },
        {
          key: 'translate',
          label: (
            <Space>
              <TranslationOutlined />
              <span>学术中译英润色</span>
            </Space>
          ),
          children: (
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Card
                size="small"
                title="中文论文摘要 / 正文输入"
                extra={
                  <Space>
                    <Select
                      value={trStyle}
                      onChange={setTrStyle}
                      style={{ width: 150 }}
                      options={[
                        { value: 'IEEE / NeurIPS', label: 'IEEE / NeurIPS' },
                        { value: 'ACL / EMNLP', label: 'ACL / EMNLP' },
                        { value: 'Nature / Science', label: 'Nature / Science' },
                      ]}
                    />
                    <Button type="primary" loading={trLoading} onClick={runAcademicTranslate}>
                      一键学术英译
                    </Button>
                  </Space>
                }
              >
                <Input.TextArea
                  rows={12}
                  placeholder="粘贴中文摘要或论文章节（支持保留 $...$ LaTeX 公式）..."
                  value={zhText}
                  onChange={(e) => setZhText(e.target.value)}
                />
              </Card>

              <Card
                size="small"
                title="顶会规范学术英文输出"
                extra={
                  trRes && (
                    <Button size="small" icon={<CopyOutlined />} onClick={() => copyText(trRes.english)}>
                      复制英文
                    </Button>
                  )
                }
              >
                {trRes ? (
                  <Space direction="vertical" style={{ width: '100%' }} size={12}>
                    <Paragraph style={{ fontSize: 14, lineHeight: 1.75 }}>{trRes.english}</Paragraph>
                    {trRes.notes.length > 0 && (
                      <Card size="small" title="学术表达润色说明">
                        <ul style={{ margin: 0, paddingLeft: 18 }}>
                          {trRes.notes.map((n, i) => (
                            <li key={i}>{n}</li>
                          ))}
                        </ul>
                      </Card>
                    )}
                  </Space>
                ) : (
                  <Text type="secondary">译文与学术表达优化说明将在此显示</Text>
                )}
              </Card>
            </div>
          ),
        },
      ]}
    />
  )
}
