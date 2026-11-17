import React, { useEffect, useRef, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  List,
  Progress,
  Select,
  Space,
  Tag,
  Timeline,
  Typography,
  message,
} from 'antd'
import {
  DeleteOutlined,
  PlusOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SendOutlined,
  UserOutlined,
} from '@ant-design/icons'
import { api, streamChat } from '../api/client'
import type { ChatMessage, CitationAnchor, Paper, TraceEvent } from '../types'
import { MarkdownView } from './MarkdownView'

const { Text } = Typography

interface Conv {
  id: number
  title: string
  paper_ids: number[]
}

interface Props {
  papers: Paper[]
  onJumpEvidence: (cit: CitationAnchor) => void
}

const PRESET_QUESTIONS = [
  '这篇论文的作者及所属机构有哪些？Adam 的 β1、β2 和训练使用的 GPU 型号与数量是多少？',
  'WMT 2014 英德与英法翻译任务的 BLEU 分数分别是多少？请列出自注意力复杂度表。',
  '请给出参考文献 [11] 的完整引用信息。',
  '对比 BERT 与原始 Transformer 在架构上的承袭与抛弃关系。',
]

export const AgentChatView: React.FC<Props> = ({ papers, onJumpEvidence }) => {
  const readyPapers = papers.filter((p) => p.status === 'ready')
  const [convs, setConvs] = useState<Conv[]>([])
  const [convId, setConvId] = useState<number | null>(null)
  const [selectedPids, setSelectedPids] = useState<number[]>([])
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [liveTrace, setLiveTrace] = useState<TraceEvent[]>([])
  const [memories, setMemories] = useState<Array<{ id: number; kind: string; content: string; weight: number }>>([])
  const [query, setQuery] = useState('')
  const [sending, setSending] = useState(false)
  const bottomRef = useRef<HTMLDivElement | null>(null)

  const loadSidebar = async () => {
    try {
      const [cList, memList] = await Promise.all([
        api<Conv[]>('/conversations'),
        api<typeof memories>('/auth/memories'),
      ])
      setConvs(cList)
      setMemories(memList)
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    void loadSidebar()
  }, [])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, liveTrace])

  const openConv = async (c: Conv) => {
    setConvId(c.id)
    setSelectedPids(c.paper_ids || [])
    const rows = await api<ChatMessage[]>(`/conversations/${c.id}/messages`)
    setMessages(rows)
    const lastAssist = [...rows].reverse().find((m) => m.role === 'assistant' && m.trace?.length)
    setLiveTrace(lastAssist?.trace || [])
  }

  const startNewConv = () => {
    setConvId(null)
    setMessages([])
    setLiveTrace([])
  }

  const deleteConv = async (id: number) => {
    await api(`/conversations/${id}`, { method: 'DELETE' })
    if (convId === id) startNewConv()
    void loadSidebar()
  }

  const handleSend = async (customText?: string) => {
    const q = (customText ?? query).trim()
    if (!q || sending) return
    if (!customText) setQuery('')
    setSending(true)
    setLiveTrace([])
    setMessages((prev) => [...prev, { role: 'user', content: q }])

    try {
      await streamChat(
        { query: q, conversation_id: convId, paper_ids: selectedPids },
        (ev) => {
          const type = String(ev.type || '')
          if (type === 'done') {
            if (typeof ev.conversation_id === 'number') setConvId(ev.conversation_id)
            setMessages((prev) => [
              ...prev,
              {
                role: 'assistant',
                content: String(ev.answer || ''),
                citations: (ev.citations as CitationAnchor[]) || [],
                trace: (ev.trace as TraceEvent[]) || [],
                critic: ev.critic as ChatMessage['critic'],
              },
            ])
            void loadSidebar()
          } else if (type === 'error') {
            message.error(String(ev.detail || 'Agent 执行失败'))
          } else {
            setLiveTrace((prev) => [...prev, ev as TraceEvent])
          }
        },
      )
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setSending(false)
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '240px 1fr 330px', gap: 12, height: 'calc(100vh - 110px)' }}>
      {/* 左栏：会话列表与长期 Memory 画像 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 10, minHeight: 0 }}>
        <Card
          size="small"
          title="研究会话"
          extra={
            <Button size="small" type="primary" icon={<PlusOutlined />} onClick={startNewConv}>
              新会话
            </Button>
          }
          bodyStyle={{ padding: 8, overflowY: 'auto', maxHeight: 320 }}
        >
          <List
            size="small"
            dataSource={convs}
            locale={{ emptyText: '暂无历史会话' }}
            renderItem={(c) => (
              <List.Item
                style={{
                  cursor: 'pointer',
                  padding: '6px 8px',
                  borderRadius: 6,
                  background: c.id === convId ? '#eff6ff' : undefined,
                }}
                onClick={() => openConv(c)}
                actions={[
                  <DeleteOutlined
                    key="del"
                    onClick={(e) => {
                      e.stopPropagation()
                      void deleteConv(c.id)
                    }}
                  />,
                ]}
              >
                <Text ellipsis style={{ maxWidth: 145 }}>
                  {c.title}
                </Text>
              </List.Item>
            )}
          />
        </Card>

        <Card size="small" title="长期 Memory 画像" style={{ flex: 1, minHeight: 0 }} bodyStyle={{ overflowY: 'auto', maxHeight: '100%' }}>
          {memories.length === 0 ? (
            <Text type="secondary" style={{ fontSize: 12 }}>
              对话与点赞反馈将自动沉淀您的研究兴趣与偏好
            </Text>
          ) : (
            memories.map((m) => (
              <Tag key={m.id} color="purple" style={{ marginBottom: 6, whiteSpace: 'normal' }}>
                [{m.kind}] {m.content}
              </Tag>
            ))
          )}
        </Card>
      </div>

      {/* 中栏：抗幻觉问答主对话区 */}
      <Card
        size="small"
        style={{ display: 'flex', flexDirection: 'column', minHeight: 0 }}
        bodyStyle={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, padding: 12 }}
        title={
          <Space wrap>
            <RobotOutlined />
            <span>抗幻觉知识问答</span>
            <Select
              mode="multiple"
              allowClear
              placeholder="限定检索文献范围（留空则检索全库）"
              style={{ minWidth: 300 }}
              value={selectedPids}
              onChange={setSelectedPids}
              options={readyPapers.map((p) => ({ value: p.id, label: p.title || p.filename }))}
            />
          </Space>
        }
      >
        <div style={{ flex: 1, overflowY: 'auto', paddingRight: 6 }}>
          {messages.length === 0 ? (
            <div style={{ padding: '24px 12px' }}>
              <Empty description="从下方示例问题开始，体验多文推理与源头溯源锚点" />
              <div style={{ marginTop: 16, display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
                {PRESET_QUESTIONS.map((q, idx) => (
                  <Button key={idx} size="small" onClick={() => handleSend(q)}>
                    {q}
                  </Button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((m, idx) => (
              <div
                key={idx}
                style={{
                  marginBottom: 14,
                  display: 'flex',
                  justifyContent: m.role === 'user' ? 'flex-end' : 'flex-start',
                }}
              >
                <div
                  style={{
                    maxWidth: '88%',
                    padding: '10px 14px',
                    borderRadius: 10,
                    background: m.role === 'user' ? '#1e3a8a' : '#f8fafc',
                    color: m.role === 'user' ? '#fff' : '#0f172a',
                    border: m.role === 'user' ? 'none' : '1px solid #e2e8f0',
                  }}
                >
                  <div style={{ fontSize: 12, opacity: 0.75, marginBottom: 4 }}>
                    {m.role === 'user' ? <><UserOutlined /> 研究者</> : <><RobotOutlined /> ScholarAgent</>}
                  </div>
                  {m.role === 'user' ? (
                    <div style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
                  ) : (
                    <>
                      <MarkdownView
                        content={m.content}
                        citations={m.citations || []}
                        onClickCitation={onJumpEvidence}
                      />
                      {m.citations && m.citations.length > 0 && (
                        <div style={{ marginTop: 10, paddingTop: 8, borderTop: '1px dashed #cbd5e1' }}>
                          <Text type="secondary" style={{ fontSize: 12, display: 'block', marginBottom: 4 }}>
                            原文出处锚点（点击直接跳页并高亮定位）：
                          </Text>
                          <Space wrap size={[6, 6]}>
                            {m.citations.map((c) => (
                              <Tag
                                key={c.ref}
                                color="blue"
                                style={{ cursor: 'pointer' }}
                                title={c.snippet}
                                onClick={() => onJumpEvidence(c)}
                              >
                                [{c.ref}] {c.paper_title.slice(0, 22)} · p.{c.page}
                              </Tag>
                            ))}
                          </Space>
                        </div>
                      )}
                      {m.critic && (
                        <div style={{ marginTop: 8, display: 'flex', alignItems: 'center', gap: 10 }}>
                          <Tag icon={<SafetyCertificateOutlined />} color={m.critic.grounded_ratio >= 0.7 ? 'green' : 'orange'}>
                            Critic 溯源可信度 {Math.round(m.critic.grounded_ratio * 100)}%
                          </Tag>
                          {m.critic.warnings?.length > 0 && (
                            <Text type="warning" style={{ fontSize: 12 }}>
                              {m.critic.warnings[0]}
                            </Text>
                          )}
                        </div>
                      )}
                    </>
                  )}
                </div>
              </div>
            ))
          )}
          <div ref={bottomRef} />
        </div>

        <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
          <Input.TextArea
            rows={2}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入研究问题（支持单篇细节问答、多篇演进对比、跨文献推理、自动检索文献等）..."
            onPressEnter={(e) => {
              if (!e.shiftKey) {
                e.preventDefault()
                void handleSend()
              }
            }}
          />
          <Button
            type="primary"
            icon={<SendOutlined />}
            loading={sending}
            style={{ height: 'auto' }}
            onClick={() => handleSend()}
          >
            发送
          </Button>
        </div>
      </Card>

      {/* 右栏：Multi-Agent 执行轨迹面板（Planner / Function Calling / Memory / Critic） */}
      <Card
        size="small"
        title="Agent 协同执行轨迹"
        bodyStyle={{ overflowY: 'auto', height: 'calc(100vh - 165px)', padding: 12 }}
      >
        {liveTrace.length === 0 ? (
          <Alert
            type="info"
            showIcon
            message="可观测 Agent 架构"
            description="发送问题后，这里将实时呈现 Memory 加载、Task Planner 规划、专职 Agent 工具调用及 Critic 抗幻觉核查全过程。"
          />
        ) : (
          <Timeline
            items={liveTrace.map((ev, i) => {
              if (ev.type === 'memory_loaded') {
                return {
                  color: 'purple',
                  children: (
                    <div>
                      <Text strong>Memory 加载</Text>
                      <div style={{ fontSize: 12, color: '#64748b' }}>
                        短期 {ev.short_turns} 轮 · 长期画像 {ev.long_items} 条
                      </div>
                    </div>
                  ),
                }
              }
              if (ev.type === 'plan') {
                return {
                  color: 'blue',
                  children: (
                    <div>
                      <Text strong>Task Planner 规划</Text> <Tag>{ev.intent}</Tag>
                      <div style={{ fontSize: 12, color: '#475569', margin: '4px 0' }}>{ev.reasoning}</div>
                      {ev.steps?.map((s) => (
                        <div key={s.step} style={{ fontSize: 12 }}>
                          {s.step}. <Tag color="geekblue">{s.agent}</Tag> {s.goal}
                        </div>
                      ))}
                    </div>
                  ),
                }
              }
              if (ev.type === 'tool_call') {
                return {
                  color: 'cyan',
                  children: (
                    <div key={i}>
                      <Text strong>Function Calling · {ev.tool}</Text> <Tag>{ev.agent}</Tag>
                      <pre style={{ fontSize: 11, background: '#f1f5f9', padding: 4, borderRadius: 4, overflow: 'auto', margin: '4px 0' }}>
                        {JSON.stringify(ev.args)}
                      </pre>
                    </div>
                  ),
                }
              }
              if (ev.type === 'tool_result') {
                return {
                  color: ev.ok ? 'green' : 'red',
                  children: (
                    <div style={{ fontSize: 12 }}>
                      <Text strong>{ev.tool} 返回</Text> ({ev.elapsed}s)
                      <div style={{ color: '#64748b', wordBreak: 'break-all' }}>{String(ev.preview || '').slice(0, 140)}</div>
                    </div>
                  ),
                }
              }
              if (ev.type === 'critic') {
                const ratio = Math.round(Number(ev.grounded_ratio || 0) * 100)
                return {
                  color: ratio >= 70 ? 'green' : 'orange',
                  children: (
                    <div>
                      <Text strong>Critic 抗幻觉核查 & Guardrails</Text>
                      <Progress percent={ratio} size="small" status={ratio >= 70 ? 'success' : 'normal'} />
                      {(ev.warnings || []).map((w, wi) => (
                        <div key={wi} style={{ fontSize: 11, color: '#d97706' }}>
                          {w}
                        </div>
                      ))}
                    </div>
                  ),
                }
              }
              if (ev.type === 'citation') {
                return {
                  color: 'blue',
                  children: (
                    <div style={{ fontSize: 12 }}>
                      锚点 [{String(ev.ref)}] p.{String(ev.page)}
                    </div>
                  ),
                }
              }
              return { color: 'gray', children: <Text style={{ fontSize: 12 }}>{ev.type}</Text> }
            })}
          />
        )}
      </Card>
    </div>
  )
}
