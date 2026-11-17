import React, { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  List,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  CloudDownloadOutlined,
  CodeOutlined,
  DislikeFilled,
  DislikeOutlined,
  LikeFilled,
  LikeOutlined,
  RadarChartOutlined,
  SearchOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { api } from '../api/client'
import type { Paper } from '../types'

const { Text, Paragraph } = Typography

interface SearchHit {
  title: string
  authors: string
  year: number | null
  venue: string
  arxiv_id: string
  citations: number
  pdf_url: string
  source: string
  abstract: string
}

interface RefQuality {
  id: number
  idx: number
  title: string
  authors: string
  year: number | null
  raw_text: string
  score: number
  label: 'core' | 'normal' | 'perfunctory'
  reason: string
  citations: number
}

interface RecItem {
  id: number
  arxiv_id: string
  title: string
  authors: string
  abstract: string
  categories: string
  published: string
  github_url: string
  has_code: boolean
  score: number
  score_detail: Record<string, number>
  reason: string
  feedback: number
}

interface Props {
  papers: Paper[]
  onLibraryChanged: () => void
}

export const ScoutView: React.FC<Props> = ({ papers, onLibraryChanged }) => {
  const readyPapers = papers.filter((p) => p.status === 'ready')
  const [searchQ, setSearchQ] = useState('')
  const [searching, setSearching] = useState(false)
  const [hits, setHits] = useState<SearchHit[]>([])

  const [selectedPid, setSelectedPid] = useState<number | null>(readyPapers[0]?.id || null)
  const [refQuals, setRefQuals] = useState<RefQuality[]>([])
  const [loadingQual, setLoadingQual] = useState(false)
  const [onlyHighQuality, setOnlyHighQuality] = useState(true)

  const [recs, setRecs] = useState<RecItem[]>([])
  const [tracking, setTracking] = useState(false)

  const loadRecs = async () => {
    try {
      const data = await api<RecItem[]>('/recommend')
      setRecs(data)
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    void loadRecs()
  }, [])

  const handleSearch = async () => {
    if (!searchQ.trim()) return
    setSearching(true)
    try {
      const data = await api<SearchHit[]>(`/scout/search?q=${encodeURIComponent(searchQ)}&limit=8`)
      setHits(data)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSearching(false)
    }
  }

  const handleFetchOne = async (titleOrId: string) => {
    try {
      const res = await api<{ imported: unknown[]; failed: Array<{ reason: string }> }>('/scout/fetch', {
        method: 'POST',
        body: JSON.stringify({ queries: [titleOrId] }),
      })
      if (res.imported.length) {
        message.success('已抓取 PDF 并提交入库解析')
        onLibraryChanged()
      } else {
        message.warning(res.failed[0]?.reason || '未找到可开放下载的 PDF')
      }
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const handleEvalRefs = async () => {
    if (!selectedPid) return
    setLoadingQual(true)
    try {
      const res = await api<{ items: RefQuality[] }>(`/scout/references/${selectedPid}/quality?refresh=true`)
      setRefQuals(res.items)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoadingQual(false)
    }
  }

  const handleDrillDown = async () => {
    if (!selectedPid) return
    try {
      const res = await api<{ selected_count: number; filtered_perfunctory_count: number }>(
        '/scout/fetch-references',
        {
          method: 'POST',
          body: JSON.stringify({ paper_id: selectedPid, only_high_quality: onlyHighQuality, limit: 5 }),
        },
      )
      message.success(
        `已自动剔除 ${res.filtered_perfunctory_count} 条应付式引用，开始抓取 ${res.selected_count} 篇高含金量参考文献`,
      )
      onLibraryChanged()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const handleTrack = async () => {
    setTracking(true)
    try {
      const res = await api<{ fetched_new: number; recommendations: RecItem[] }>('/scout/track', {
        method: 'POST',
        body: JSON.stringify({ days: 5 }),
      })
      setRecs(res.recommendations)
      message.success(`Sickle arXiv 动态追踪完成，新增 ${res.fetched_new} 篇候选`)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setTracking(false)
    }
  }

  const handleFeedback = async (recId: number, fb: number) => {
    await api(`/recommend/${recId}/feedback`, {
      method: 'POST',
      body: JSON.stringify({ feedback: fb }),
    })
    setRecs((prev) => prev.map((r) => (r.id === recId ? { ...r, feedback: fb } : r)))
    message.success(fb === 1 ? '已点赞，将优先推荐同类易复现研究' : '已踩，后续降低同类推荐权重')
  }

  return (
    <Tabs
      items={[
        {
          key: 'recommend',
          label: (
            <Space>
              <RadarChartOutlined />
              <span>arXiv 动态追踪与个性化推荐</span>
            </Space>
          ),
          children: (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
              <Alert
                type="info"
                showIcon
                message="Sickle (OAI-PMH) 追踪 + 本地文献库相关性 + 开源 GitHub 代码加权 + 多用户协同过滤"
                action={
                  <Button type="primary" icon={<SyncOutlined />} loading={tracking} onClick={handleTrack}>
                    立即追踪最新 arXiv
                  </Button>
                }
              />
              {recs.length === 0 ? (
                <Empty description="点击右上角「立即追踪最新 arXiv」获取推荐论文" />
              ) : (
                <List
                  dataSource={recs}
                  renderItem={(r) => (
                    <Card size="small" style={{ marginBottom: 10 }}>
                      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                        <div style={{ flex: 1 }}>
                          <Space wrap>
                            <Text strong style={{ fontSize: 15 }}>
                              {r.title}
                            </Text>
                            <Tag color="blue">arXiv:{r.arxiv_id}</Tag>
                            <Tag color="purple">匹配度 {(r.score * 100).toFixed(0)}%</Tag>
                            {r.has_code && (
                              <Tag icon={<CodeOutlined />} color="green">
                                含开源 GitHub 代码（易复现优先）
                              </Tag>
                            )}
                          </Space>
                          <div style={{ fontSize: 12, color: '#64748b', margin: '4px 0' }}>
                            {r.authors} · {r.published} · {r.categories}
                          </div>
                          <Alert
                            type="success"
                            style={{ margin: '6px 0', padding: '4px 10px' }}
                            message={<Text style={{ fontSize: 13 }}>推荐理由：{r.reason}</Text>}
                          />
                          <Paragraph ellipsis={{ rows: 2, expandable: true }} style={{ margin: 0, fontSize: 13 }}>
                            {r.abstract}
                          </Paragraph>
                        </div>
                        <Space direction="vertical" align="end">
                          <Space>
                            <Button
                              size="small"
                              type={r.feedback === 1 ? 'primary' : 'default'}
                              icon={r.feedback === 1 ? <LikeFilled /> : <LikeOutlined />}
                              onClick={() => handleFeedback(r.id, r.feedback === 1 ? 0 : 1)}
                            >
                              赞
                            </Button>
                            <Button
                              size="small"
                              danger={r.feedback === -1}
                              icon={r.feedback === -1 ? <DislikeFilled /> : <DislikeOutlined />}
                              onClick={() => handleFeedback(r.id, r.feedback === -1 ? 0 : -1)}
                            >
                              踩
                            </Button>
                          </Space>
                          <Button
                            size="small"
                            type="primary"
                            ghost
                            icon={<CloudDownloadOutlined />}
                            onClick={() => handleFetchOne(r.arxiv_id)}
                          >
                            一键入库
                          </Button>
                          {r.github_url && (
                            <Button size="small" type="link" href={r.github_url} target="_blank">
                              GitHub 代码
                            </Button>
                          )}
                        </Space>
                      </div>
                    </Card>
                  )}
                />
              )}
            </div>
          ),
        },
        {
          key: 'drilldown',
          label: '智能下钻与引用含金量筛选',
          children: (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
              <Card size="small" title="1. 按论文名称 / arXiv ID 全网自动抓取入库">
                <Space.Compact style={{ width: '100%' }}>
                  <Input
                    placeholder="输入论文题目、主题词或 arXiv ID（如 BERT Pre-training of Deep Bidirectional Transformers）"
                    value={searchQ}
                    onChange={(e) => setSearchQ(e.target.value)}
                    onPressEnter={handleSearch}
                  />
                  <Button type="primary" icon={<SearchOutlined />} loading={searching} onClick={handleSearch}>
                    全网检索
                  </Button>
                </Space.Compact>
                {hits.length > 0 && (
                  <List
                    size="small"
                    style={{ marginTop: 10 }}
                    dataSource={hits}
                    renderItem={(h) => (
                      <List.Item
                        actions={[
                          <Button
                            key="imp"
                            size="small"
                            type="primary"
                            ghost
                            icon={<CloudDownloadOutlined />}
                            disabled={!h.pdf_url && !h.arxiv_id}
                            onClick={() => handleFetchOne(h.arxiv_id || h.title)}
                          >
                            自动抓取入库
                          </Button>,
                        ]}
                      >
                        <List.Item.Meta
                          title={
                            <Space wrap>
                              <Text strong>{h.title}</Text>
                              <Tag>{h.source}</Tag>
                              {h.year && <Tag>{h.year}</Tag>}
                              {h.citations > 0 && <Tag color="gold">被引 {h.citations}</Tag>}
                            </Space>
                          }
                          description={`${h.authors} · ${h.venue}`}
                        />
                      </List.Item>
                    )}
                  />
                )}
              </Card>

              <Card
                size="small"
                title="2. 参考文献「引用含金量」筛选（自动剔除应付式引用）"
                extra={
                  <Space wrap>
                    <Select
                      style={{ width: 260 }}
                      placeholder="选择已解析论文"
                      value={selectedPid}
                      onChange={setSelectedPid}
                      options={readyPapers.map((p) => ({ value: p.id, label: p.title || p.filename }))}
                    />
                    <Button onClick={handleEvalRefs} loading={loadingQual} disabled={!selectedPid}>
                      评估含金量
                    </Button>
                    <Space>
                      <Text style={{ fontSize: 12 }}>剔除应付式引用</Text>
                      <Switch checked={onlyHighQuality} onChange={setOnlyHighQuality} size="small" />
                    </Space>
                    <Button type="primary" icon={<CloudDownloadOutlined />} onClick={handleDrillDown} disabled={!selectedPid}>
                      下钻下载 Top5
                    </Button>
                  </Space>
                }
              >
                <Table
                  size="small"
                  dataSource={refQuals}
                  rowKey="id"
                  pagination={{ pageSize: 10 }}
                  columns={[
                    { title: '#', dataIndex: 'idx', width: 50 },
                    {
                      title: '参考文献',
                      render: (_, r) => (
                        <Text delete={r.label === 'perfunctory'} type={r.label === 'perfunctory' ? 'secondary' : undefined}>
                          {r.raw_text}
                        </Text>
                      ),
                    },
                    {
                      title: '含金量评分',
                      width: 110,
                      sorter: (a, b) => a.score - b.score,
                      render: (_, r) => <Text strong>{r.score}</Text>,
                    },
                    {
                      title: '引用类型判定',
                      width: 220,
                      render: (_, r) => (
                        <div>
                          <Tag color={r.label === 'core' ? 'green' : r.label === 'perfunctory' ? 'default' : 'blue'}>
                            {r.label === 'core' ? '核心基石引用' : r.label === 'perfunctory' ? '应付式引用（剔除）' : '常规参考'}
                          </Tag>
                          <div style={{ fontSize: 11, color: '#64748b' }}>{r.reason}</div>
                        </div>
                      ),
                    },
                  ]}
                />
              </Card>
            </div>
          ),
        },
      ]}
    />
  )
}
