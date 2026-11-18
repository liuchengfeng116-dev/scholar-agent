import React, { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  List,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApartmentOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  FileTextOutlined,
} from '@ant-design/icons'
import * as echarts from 'echarts'
import { api } from '../api/client'
import type { Paper } from '../types'
import { MarkdownView } from './MarkdownView'

const { Text } = Typography

interface GraphNode {
  id: number
  title: string
  year: number | null
  venue: string
  in_degree: number
  out_degree: number
  pagerank: number
  betweenness: number
  community: number
  role: 'keystone' | 'bridge' | 'derivative' | 'peripheral'
}

interface GraphEdge {
  source: number
  target: number
  kind: string
}

interface GraphObj {
  id: number
  name: string
  paper_ids: number[]
  data: {
    nodes: GraphNode[]
    edges: GraphEdge[]
    metrics: {
      node_count: number
      edge_count: number
      density: number
      keystone_ids: number[]
      derivative_ids: number[]
    }
  }
  survey: string
  future_work: {
    resolved_ideas?: Array<{ idea: string; origin_paper: string; resolved_by: string }>
    open_directions?: Array<{ direction: string; rationale: string; supporting_papers: string[] }>
  }
}

interface Props {
  papers: Paper[]
}

const ROLE_COLOR: Record<string, string> = {
  keystone: '#f59e0b',
  bridge: '#8b5cf6',
  derivative: '#2563eb',
  peripheral: '#64748b',
}

const ROLE_LABEL: Record<string, string> = {
  keystone: '基石论文',
  bridge: '桥梁枢纽',
  derivative: '衍生论文',
  peripheral: '边缘节点',
}

export const GraphView: React.FC<Props> = ({ papers }) => {
  const readyPapers = papers.filter((p) => p.status === 'ready')
  const [selectedPids, setSelectedPids] = useState<number[]>([])
  const [graphName, setGraphName] = useState('局域文献引用图谱')
  const [graph, setGraph] = useState<GraphObj | null>(null)
  const [building, setBuilding] = useState(false)
  const [surveying, setSurveying] = useState(false)
  const [exploring, setExploring] = useState(false)

  const chartRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const loadLatest = async () => {
      try {
        const list = await api<GraphObj[]>('/graph')
        if (list.length) setGraph(list[0])
      } catch {
        /* ignore */
      }
    }
    void loadLatest()
  }, [])

  // 渲染 ECharts 交互式引用拓扑网络图
  useEffect(() => {
    if (!graph || !chartRef.current) return
    const chart = echarts.init(chartRef.current)
    const nodes = graph.data.nodes || []
    const edges = graph.data.edges || []

    chart.setOption({
      tooltip: {
        trigger: 'item',
        formatter: (params: { dataType: string; data: GraphNode }) => {
          if (params.dataType !== 'node') return ''
          const d = params.data
          return (
            `<b>${d.title}</b><br/>` +
            `角色：${ROLE_LABEL[d.role] || d.role} (${d.year || '?'})<br/>` +
            `PageRank：${d.pagerank} · 入度：${d.in_degree} · 出度：${d.out_degree}<br/>` +
            `介数中心性：${d.betweenness} · 社区 #${d.community}`
          )
        },
      },
      series: [
        {
          type: 'graph',
          layout: 'force',
          roam: true,
          draggable: true,
          edgeSymbol: ['none', 'arrow'],
          edgeSymbolSize: 8,
          force: { repulsion: 420, edgeLength: [100, 190], gravity: 0.1 },
          label: { show: true, position: 'bottom', fontSize: 11, formatter: (p: { data: GraphNode }) => p.data.title.slice(0, 22) },
          data: nodes.map((n) => ({
            ...n,
            id: String(n.id),
            name: n.title,
            symbolSize: Math.max(24, Math.min(58, 22 + n.pagerank * 160 + n.in_degree * 6)),
            itemStyle: { color: ROLE_COLOR[n.role] || '#2563eb' },
          })),
          links: edges.map((e) => ({
            source: String(e.source),
            target: String(e.target),
            lineStyle: { color: '#94a3b8', curveness: 0.15, width: 1.8 },
          })),
        },
      ],
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [graph])

  const handleBuild = async () => {
    setBuilding(true)
    try {
      const g = await api<GraphObj>('/graph/build', {
        method: 'POST',
        body: JSON.stringify({ name: graphName, paper_ids: selectedPids }),
      })
      setGraph(g)
      message.success('引用图谱与图论中心性计算完成')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setBuilding(false)
    }
  }

  const handleSurvey = async () => {
    if (!graph) return
    setSurveying(true)
    try {
      const res = await api<{ survey: string }>(`/graph/${graph.id}/survey`, { method: 'POST' })
      setGraph({ ...graph, survey: res.survey })
      message.success('已生成带引用出处的领域综述')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSurveying(false)
    }
  }

  const handleFutureWork = async () => {
    if (!graph) return
    setExploring(true)
    try {
      const res = await api<{ future_work: GraphObj['future_work'] }>(`/graph/${graph.id}/future-work`, {
        method: 'POST',
      })
      setGraph({ ...graph, future_work: res.future_work })
      message.success('已完成 Future Work 去伪与前沿方向提炼')
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setExploring(false)
    }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Card size="small">
        <Space wrap style={{ width: '100%', justifyContent: 'space-between' }}>
          <Space wrap>
            <Input
              value={graphName}
              onChange={(e) => setGraphName(e.target.value)}
              style={{ width: 180 }}
              placeholder="图谱名称"
            />
            <Select
              mode="multiple"
              allowClear
              placeholder="选择批量论文（留空=全库已就绪论文）"
              style={{ minWidth: 320 }}
              value={selectedPids}
              onChange={setSelectedPids}
              options={readyPapers.map((p) => ({ value: p.id, label: p.title || p.filename }))}
            />
            <Button type="primary" icon={<ApartmentOutlined />} loading={building} onClick={handleBuild}>
              构建引用拓扑图谱
            </Button>
          </Space>

          {graph && (
            <Space>
              <Button icon={<FileTextOutlined />} loading={surveying} onClick={handleSurvey}>
                生成领域综述
              </Button>
              <Button type="primary" ghost icon={<BulbOutlined />} loading={exploring} onClick={handleFutureWork}>
                Future Work 去伪探索
              </Button>
            </Space>
          )}
        </Space>
      </Card>

      {!graph ? (
        <Empty description="选择多篇文献后点击「构建引用拓扑图谱」，自动识别基石论文与衍生脉络" />
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: '1.15fr 1fr', gap: 12 }}>
          <Card
            size="small"
            title={
              <Space>
                <span>{graph.name}</span>
                <Tag color="gold">基石论文 {graph.data.metrics?.keystone_ids?.length || 0}</Tag>
                <Tag color="blue">引用边 {graph.data.metrics?.edge_count || 0}</Tag>
                <Tag>密度 {graph.data.metrics?.density || 0}</Tag>
              </Space>
            }
          >
            <div style={{ marginBottom: 8 }}>
              {Object.entries(ROLE_LABEL).map(([k, v]) => (
                <Tag key={k} color={ROLE_COLOR[k]}>
                  {v}
                </Tag>
              ))}
            </div>
            <div ref={chartRef} style={{ width: '100%', height: 470 }} />
          </Card>

          <Card size="small" bodyStyle={{ padding: '0 12px 12px', maxHeight: 540, overflowY: 'auto' }}>
            <Tabs
              items={[
                {
                  key: 'metrics',
                  label: '图论挖掘指标',
                  children: (
                    <Table
                      size="small"
                      dataSource={graph.data.nodes || []}
                      rowKey="id"
                      pagination={{ pageSize: 8 }}
                      columns={[
                        { title: '论文', dataIndex: 'title', ellipsis: true },
                        {
                          title: '角色',
                          width: 90,
                          render: (_, n) => <Tag color={ROLE_COLOR[n.role]}>{ROLE_LABEL[n.role] || n.role}</Tag>,
                        },
                        { title: '入度', dataIndex: 'in_degree', width: 55 },
                        { title: 'PageRank', dataIndex: 'pagerank', width: 85 },
                      ]}
                    />
                  ),
                },
                {
                  key: 'survey',
                  label: '自动领域综述',
                  children: graph.survey ? (
                    <MarkdownView content={graph.survey} />
                  ) : (
                    <Empty description="点击右上角「生成领域综述」" />
                  ),
                },
                {
                  key: 'fw',
                  label: 'Future Work 前沿去伪',
                  children:
                    graph.future_work?.open_directions || graph.future_work?.resolved_ideas ? (
                      <Space direction="vertical" style={{ width: '100%' }} size={12}>
                        <Alert
                          type="success"
                          showIcon
                          icon={<CheckCircleOutlined />}
                          message="提炼出的高价值开放未来方向"
                        />
                        <List
                          size="small"
                          dataSource={graph.future_work.open_directions || []}
                          renderItem={(d) => (
                            <List.Item>
                              <div>
                                <Text strong>{d.direction}</Text>
                                <div style={{ fontSize: 12, color: '#475569' }}>{d.rationale}</div>
                                <div>
                                  {(Array.isArray(d.supporting_papers)
                                    ? d.supporting_papers
                                    : d.supporting_papers
                                      ? [String(d.supporting_papers)]
                                      : []
                                  ).map((sp, i) => (
                                    <Tag key={i} color="blue" style={{ marginTop: 4 }}>
                                      {sp}
                                    </Tag>
                                  ))}
                                </div>
                              </div>
                            </List.Item>
                          )}
                        />
                        <Alert
                          type="warning"
                          showIcon
                          icon={<CloseCircleOutlined />}
                          message="已结合时间线与最新进展自动排除的旧 Idea"
                        />
                        <List
                          size="small"
                          dataSource={graph.future_work.resolved_ideas || []}
                          renderItem={(r) => (
                            <List.Item>
                              <div>
                                <Text delete type="secondary">
                                  {r.idea}
                                </Text>
                                <div style={{ fontSize: 12 }}>
                                  原提出：{r.origin_paper} → <Tag color="green">已被解决：{r.resolved_by}</Tag>
                                </div>
                              </div>
                            </List.Item>
                          )}
                        />
                      </Space>
                    ) : (
                      <Empty description="点击右上角「Future Work 去伪探索」" />
                    ),
                },
              ]}
            />
          </Card>
        </div>
      )}
    </div>
  )
}
