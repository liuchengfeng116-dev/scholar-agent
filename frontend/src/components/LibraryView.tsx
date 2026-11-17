import React, { useEffect, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Card,
  Descriptions,
  Drawer,
  Input,
  Modal,
  Progress,
  Space,
  Table,
  Tabs,
  Tag,
  Tree,
  Typography,
  Upload,
  message,
} from 'antd'
import {
  DeleteOutlined,
  ExclamationCircleOutlined,
  EyeOutlined,
  FilePdfOutlined,
  InboxOutlined,
  ReloadOutlined,
} from '@ant-design/icons'
import { api, withAuthToken } from '../api/client'
import type { BlockItem, Paper, ReferenceItem, SectionItem } from '../types'
import { MarkdownView } from './MarkdownView'

const { Dragger } = Upload
const { Text, Paragraph } = Typography

interface Props {
  papers: Paper[]
  onRefresh: () => void
  onOpenReader: (paperId: number, page?: number) => void
}

export const LibraryView: React.FC<Props> = ({ papers, onRefresh, onOpenReader }) => {
  const [search, setSearch] = useState('')
  const [uploading, setUploading] = useState(false)
  const [dupPaper, setDupPaper] = useState<Paper | null>(null)
  const [detailPaperId, setDetailPaperId] = useState<number | null>(null)
  const [structure, setStructure] = useState<{
    paper: Paper
    sections: SectionItem[]
    blocks: BlockItem[]
    references: ReferenceItem[]
  } | null>(null)
  const [loadingStruct, setLoadingStruct] = useState(false)

  // 有正在解析的论文时自动轮询刷新进度
  useEffect(() => {
    const parsing = papers.some((p) => p.status === 'uploaded' || p.status === 'parsing')
    if (!parsing) return
    const timer = setInterval(onRefresh, 2500)
    return () => clearInterval(timer)
  }, [papers, onRefresh])

  // 若列表中有待处理去重记录且弹窗未打开，提示用户
  const pendingDups = papers.filter((p) => p.status === 'duplicate_pending' && p.duplicate)

  const handleUpload = async (fileList: File[]) => {
    if (!fileList.length) return
    setUploading(true)
    try {
      const fd = new FormData()
      for (const f of fileList) fd.append('files', f)
      const res = await api<{ items: Array<{ paper: Paper; task_id: number | null }> }>('/papers/upload', {
        method: 'POST',
        body: fd,
      })
      const dupItem = res.items.find((it) => it.paper.status === 'duplicate_pending')
      if (dupItem) {
        setDupPaper(dupItem.paper)
        message.warning('检测到内容高度重合的论文，请确认版本处理策略')
      } else {
        message.success(`已上传 ${res.items.length} 篇文献并启动后台解析`)
      }
      onRefresh()
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setUploading(false)
    }
  }

  const handleDedupDecision = async (paperId: number, decision: 'keep_existing' | 'overwrite' | 'keep_both') => {
    try {
      await api(`/papers/${paperId}/dedup-decision`, {
        method: 'POST',
        body: JSON.stringify({ decision }),
      })
      message.success('已完成去重处理')
      setDupPaper(null)
      onRefresh()
    } catch (e) {
      message.error((e as Error).message)
    }
  }

  const openStructure = async (paperId: number) => {
    setDetailPaperId(paperId)
    setLoadingStruct(true)
    try {
      const data = await api<NonNullable<typeof structure>>(`/papers/${paperId}/structure`)
      setStructure(data)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setLoadingStruct(false)
    }
  }

  const handleDelete = (paperId: number) => {
    Modal.confirm({
      title: '确认删除该文献？',
      icon: <ExclamationCircleOutlined />,
      content: '将同时清理本地 PDF、解析结构与向量索引。',
      onOk: async () => {
        await api(`/papers/${paperId}`, { method: 'DELETE' })
        message.success('已删除')
        onRefresh()
      },
    })
  }

  const handleReparse = async (paperId: number) => {
    await api(`/papers/${paperId}/reparse`, { method: 'POST' })
    message.info('已重新提交解析任务')
    onRefresh()
  }

  const filtered = papers.filter((p) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    return (
      p.title.toLowerCase().includes(q) ||
      p.filename.toLowerCase().includes(q) ||
      p.arxiv_id.toLowerCase().includes(q)
    )
  })

  // 将扁平 Section 列表转为 Antd Tree 结构
  const buildTreeData = (secs: SectionItem[]) => {
    type Node = { key: number; title: string; children: Node[] }
    const map = new Map<number, Node>()
    const roots: Node[] = []
    for (const s of secs) {
      map.set(s.id, {
        key: s.id,
        title: `${s.title}  (p.${s.page})`,
        children: [],
      })
    }
    for (const s of secs) {
      const node = map.get(s.id)!
      if (s.parent_id && map.has(s.parent_id)) {
        map.get(s.parent_id)!.children.push(node)
      } else {
        roots.push(node)
      }
    }
    return roots
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <Card size="small">
        <Dragger
          multiple
          accept=".pdf"
          showUploadList={false}
          disabled={uploading}
          beforeUpload={(_, files) => {
            // 只在最后一个文件回调中批量发送，避免重复提交
            if (files[files.length - 1] === _) {
              void handleUpload(files as unknown as File[])
            }
            return false
          }}
          style={{ padding: '10px 0' }}
        >
          <p className="ant-upload-drag-icon">
            <InboxOutlined />
          </p>
          <p className="ant-upload-text">点击或拖拽单篇 / 批量 PDF 文献到此处上传</p>
          <p className="ant-upload-hint">
            自动执行基于内容语义的去重预警（识别 v1/v7 等演进版本）、扫描件多模态 OCR 及百页书籍分批解析
          </p>
        </Dragger>
      </Card>

      {pendingDups.length > 0 && (
        <Alert
          type="warning"
          showIcon
          message={`有 ${pendingDups.length} 篇新上传文献检测到与库内现有文献内容高度重合`}
          action={
            <Button size="small" type="primary" onClick={() => setDupPaper(pendingDups[0])}>
              立即比对并决策
            </Button>
          }
        />
      )}

      <Card
        title={<Space><FilePdfOutlined /> <span>我的科研文献库 ({filtered.length})</span></Space>}
        extra={
          <Space>
            <Input.Search
              placeholder="搜索标题 / arXiv ID / 文件名"
              allowClear
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              style={{ width: 260 }}
            />
            <Button icon={<ReloadOutlined />} onClick={onRefresh}>
              刷新
            </Button>
          </Space>
        }
      >
        <Table
          dataSource={filtered}
          rowKey="id"
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: '文献标题与元数据',
              key: 'title',
              render: (_, p) => (
                <div>
                  <Space wrap>
                    <Text strong style={{ fontSize: 14 }}>
                      {p.title || p.filename}
                    </Text>
                    {p.arxiv_id && (
                      <Tag color="geekblue">
                        arXiv:{p.arxiv_id}
                        {p.version}
                      </Tag>
                    )}
                    {p.doc_type === 'book' && <Tag color="purple">长篇书籍</Tag>}
                    {!p.has_text_layer && <Tag color="orange">扫描件 OCR</Tag>}
                  </Space>
                  <div style={{ fontSize: 12, color: '#64748b', marginTop: 2 }}>
                    {p.filename} · {p.page_count} 页
                    {p.year ? ` · ${p.year}` : ''}
                    {p.venue ? ` · ${p.venue}` : ''}
                    {p.stats?.sections ? ` · ${p.stats.sections} 章节 / ${p.stats.tables || 0} 表格 / ${p.stats.formulas || 0} 公式 / ${p.stats.references || 0} 文献` : ''}
                  </div>
                </div>
              ),
            },
            {
              title: '解析状态',
              width: 220,
              render: (_, p) => {
                if (p.status === 'ready') return <Badge status="success" text="已就绪" />
                if (p.status === 'duplicate_pending') {
                  return (
                    <Button size="small" danger onClick={() => setDupPaper(p)}>
                      去重待决策
                    </Button>
                  )
                }
                if (p.status === 'failed') return <Badge status="error" text={p.status_msg || '失败'} />
                return (
                  <div>
                    <Progress percent={Math.round(p.progress || 5)} size="small" />
                    <Text type="secondary" style={{ fontSize: 11 }}>
                      {p.status_msg}
                    </Text>
                  </div>
                )
              },
            },
            {
              title: '操作',
              width: 260,
              render: (_, p) => (
                <Space>
                  <Button
                    size="small"
                    type="primary"
                    ghost
                    icon={<EyeOutlined />}
                    disabled={p.status !== 'ready'}
                    onClick={() => openStructure(p.id)}
                  >
                    结构化详情
                  </Button>
                  <Button
                    size="small"
                    disabled={p.status !== 'ready'}
                    onClick={() => onOpenReader(p.id, 1)}
                  >
                    阅读/翻译
                  </Button>
                  <Button size="small" icon={<ReloadOutlined />} onClick={() => handleReparse(p.id)} title="重新解析" />
                  <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(p.id)} />
                </Space>
              ),
            },
          ]}
        />
      </Card>

      {/* 语义去重与版本修订对比弹窗 */}
      <Modal
        open={Boolean(dupPaper && dupPaper.duplicate)}
        title="语义级文献去重预警与版本比对"
        width={760}
        onCancel={() => setDupPaper(null)}
        footer={
          dupPaper ? (
            <Space>
              <Button
                type={dupPaper.duplicate?.suggestion === 'keep_existing' ? 'primary' : 'default'}
                onClick={() => handleDedupDecision(dupPaper.id, 'keep_existing')}
              >
                保留库中现有版本（推荐）
              </Button>
              <Button
                type={dupPaper.duplicate?.suggestion === 'overwrite' ? 'primary' : 'default'}
                onClick={() => handleDedupDecision(dupPaper.id, 'overwrite')}
              >
                用新版本覆盖替换
              </Button>
              <Button onClick={() => handleDedupDecision(dupPaper.id, 'keep_both')}>
                两者都保留
              </Button>
            </Space>
          ) : null
        }
      >
        {dupPaper?.duplicate && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <Alert
              type="info"
              showIcon
              message="智能版本建议"
              description={dupPaper.duplicate.suggestion_reason}
            />
            <Descriptions bordered size="small" column={2}>
              <Descriptions.Item label="库中已有版本">
                {dupPaper.duplicate.existing_title} ({dupPaper.duplicate.existing_version || dupPaper.duplicate.existing_filename})
              </Descriptions.Item>
              <Descriptions.Item label="本次上传版本">
                {dupPaper.filename} ({dupPaper.version || '未知版本'})
              </Descriptions.Item>
              <Descriptions.Item label="文本指纹重合度">
                {(dupPaper.duplicate.text_similarity * 100).toFixed(1)}%
              </Descriptions.Item>
              <Descriptions.Item label="向量语义相似度">
                {(dupPaper.duplicate.semantic_similarity * 100).toFixed(1)}%
              </Descriptions.Item>
            </Descriptions>
            <Card size="small" title="Agent 自动总结两版本主要修订差异">
              <MarkdownView content={dupPaper.duplicate.diff_summary} />
            </Card>
          </div>
        )}
      </Modal>

      {/* 细粒度结构化解析详情抽屉 */}
      <Drawer
        open={detailPaperId !== null}
        onClose={() => setDetailPaperId(null)}
        width={920}
        title={structure?.paper.title || '细粒度结构化解析结果'}
      >
        {loadingStruct || !structure ? (
          <div style={{ padding: 40, textAlign: 'center' }}>加载结构化解析结果中...</div>
        ) : (
          <Tabs
            items={[
              {
                key: 'meta',
                label: '元数据与标题树',
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size={16}>
                    <Descriptions bordered size="small" column={2}>
                      <Descriptions.Item label="标题" span={2}>
                        {structure.paper.title}
                      </Descriptions.Item>
                      <Descriptions.Item label="作者与机构" span={2}>
                        {structure.paper.authors.map((a, i) => (
                          <Tag key={i} style={{ marginBottom: 4 }}>
                            {typeof a === 'string'
                              ? a
                              : `${a.name}${a.affiliations?.length ? ` (${a.affiliations.join(', ')})` : ''}`}
                          </Tag>
                        ))}
                      </Descriptions.Item>
                      <Descriptions.Item label="摘要" span={2}>
                        <Paragraph style={{ margin: 0 }}>{structure.paper.abstract || '（无）'}</Paragraph>
                      </Descriptions.Item>
                    </Descriptions>
                    <Card size="small" title={`1-3级章节目录树 (${structure.sections.length})`}>
                      <Tree treeData={buildTreeData(structure.sections)} defaultExpandAll />
                    </Card>
                  </Space>
                ),
              },
              {
                key: 'tables',
                label: `表格 (${structure.blocks.filter((b) => b.type === 'table').length})`,
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size={12}>
                    {structure.blocks
                      .filter((b) => b.type === 'table')
                      .map((b) => (
                        <Card key={b.id} size="small" title={`Page ${b.page}`}>
                          {b.html ? (
                            <div
                              className="scholar-html-table"
                              dangerouslySetInnerHTML={{ __html: b.html }}
                            />
                          ) : (
                            <MarkdownView content={b.content} />
                          )}
                        </Card>
                      ))}
                  </Space>
                ),
              },
              {
                key: 'formulas',
                label: `公式 (${structure.blocks.filter((b) => b.type === 'formula').length})`,
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size={12}>
                    {structure.blocks
                      .filter((b) => b.type === 'formula')
                      .map((b) => (
                        <Card key={b.id} size="small" title={`公式 · Page ${b.page}`}>
                          <MarkdownView content={b.latex || b.content} />
                          <Text type="secondary" code style={{ fontSize: 12 }}>
                            {b.latex || b.content}
                          </Text>
                        </Card>
                      ))}
                  </Space>
                ),
              },
              {
                key: 'figures',
                label: `图表 (${structure.blocks.filter((b) => b.type === 'figure').length})`,
                children: (
                  <Space direction="vertical" style={{ width: '100%' }} size={12}>
                    {structure.blocks
                      .filter((b) => b.type === 'figure')
                      .map((b) => (
                        <Card key={b.id} size="small" title={`图表 · Page ${b.page}`}>
                          {b.image_url && (
                            <img
                              src={withAuthToken(b.image_url)}
                              alt={b.content}
                              style={{ maxWidth: '100%', maxHeight: 340, display: 'block', marginBottom: 8 }}
                            />
                          )}
                          <Text>{b.content}</Text>
                        </Card>
                      ))}
                  </Space>
                ),
              },
              {
                key: 'refs',
                label: `参考文献 (${structure.references.length})`,
                children: (
                  <Table
                    size="small"
                    dataSource={structure.references}
                    rowKey="id"
                    pagination={{ pageSize: 15 }}
                    columns={[
                      { title: '#', dataIndex: 'idx', width: 55 },
                      {
                        title: '文献条目',
                        render: (_, r) => (
                          <div>
                            <Text>{r.raw_text}</Text>
                            {r.cite_contexts.length > 0 && (
                              <div style={{ marginTop: 4 }}>
                                {r.cite_contexts.map((c, idx) => (
                                  <Tag
                                    key={idx}
                                    color="blue"
                                    style={{ cursor: 'pointer' }}
                                    onClick={() => {
                                      setDetailPaperId(null)
                                      onOpenReader(structure.paper.id, c.page)
                                    }}
                                  >
                                    正文引用 p.{c.page}
                                  </Tag>
                                ))}
                              </div>
                            )}
                          </div>
                        ),
                      },
                    ]}
                  />
                ),
              },
            ]}
          />
        )}
      </Drawer>
    </div>
  )
}
