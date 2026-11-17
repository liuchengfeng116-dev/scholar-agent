import React, { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  ConfigProvider,
  Form,
  Input,
  Layout,
  Menu,
  Space,
  Tabs,
  Tag,
  Typography,
  message,
} from 'antd'
import {
  ApartmentOutlined,
  BookOutlined,
  EditOutlined,
  LogoutOutlined,
  RadarChartOutlined,
  ReadOutlined,
  RobotOutlined,
  UserOutlined,
} from '@ant-design/icons'
import zhCN from 'antd/locale/zh_CN'
import { api, getToken, setToken } from './api/client'
import type { CitationAnchor, Paper, User } from './types'
import { LibraryView } from './components/LibraryView'
import { ReaderView } from './components/ReaderView'
import { AgentChatView } from './components/AgentChatView'
import { ScoutView } from './components/ScoutView'
import { GraphView } from './components/GraphView'
import { CopilotView } from './components/CopilotView'

const { Header, Sider, Content } = Layout
const { Title, Text } = Typography

type NavKey = 'library' | 'reader' | 'chat' | 'scout' | 'graph' | 'copilot'

export const App: React.FC = () => {
  const [user, setUser] = useState<User | null>(null)
  const [authMode, setAuthMode] = useState<'login' | 'register'>('login')
  const [authLoading, setAuthLoading] = useState(false)
  const [nav, setNav] = useState<NavKey>('library')
  const [papers, setPapers] = useState<Paper[]>([])
  const [readerState, setReaderState] = useState<{
    paperId: number | null
    page: number
    bbox?: number[]
  }>({ paperId: null, page: 1 })

  const loadPapers = useCallback(async () => {
    try {
      const list = await api<Paper[]>('/papers')
      setPapers(list)
    } catch {
      /* ignore */
    }
  }, [])

  useEffect(() => {
    const tok = getToken()
    if (!tok) return
    api<User>('/auth/me')
      .then((u) => {
        setUser(u)
        void loadPapers()
      })
      .catch(() => setToken(''))
  }, [loadPapers])

  const handleAuth = async (values: { username: string; password: string }) => {
    setAuthLoading(true)
    try {
      const res = await api<{ access_token: string; user: User }>(`/auth/${authMode}`, {
        method: 'POST',
        body: JSON.stringify(values),
      })
      setToken(res.access_token)
      setUser(res.user)
      await loadPapers()
      message.success(`欢迎，${res.user.username}`)
    } catch (e) {
      message.error((e as Error).message)
    } finally {
      setAuthLoading(false)
    }
  }

  const handleLogout = () => {
    setToken('')
    setUser(null)
    setPapers([])
  }

  const openReaderAt = (paperId: number, page = 1, bbox?: number[]) => {
    setReaderState({ paperId, page, bbox })
    setNav('reader')
  }

  const jumpFromCitation = (cit: CitationAnchor) => {
    if (cit.paper_id) {
      openReaderAt(cit.paper_id, cit.page || 1, cit.bbox)
    }
  }

  if (!user) {
    return (
      <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1e3a8a' } }}>
        <div
          style={{
            minHeight: '100vh',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: 'linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #312e81 100%)',
            padding: 20,
          }}
        >
          <Card style={{ width: 420, borderRadius: 12, boxShadow: '0 20px 50px rgba(0,0,0,0.35)' }}>
            <div style={{ textAlign: 'center', marginBottom: 18 }}>
              <Tag color="geekblue" style={{ marginBottom: 8 }}>
                Multi-Agent 全流程科研助手
              </Tag>
              <Title level={3} style={{ margin: 0 }}>
                ScholarAgent · 智研助手
              </Title>
              <Text type="secondary">
                文献语义去重 · 抗幻觉溯源问答 · 双语联动阅读 · 引用图谱 · 写作 Copilot
              </Text>
            </div>
            <Tabs
              centered
              activeKey={authMode}
              onChange={(k) => setAuthMode(k as 'login' | 'register')}
              items={[
                { key: 'login', label: '账号登录' },
                { key: 'register', label: '注册新账号' },
              ]}
            />
            <Form layout="vertical" onFinish={handleAuth}>
              <Form.Item name="username" label="用户名" rules={[{ required: true, message: '请输入用户名' }]}>
                <Input prefix={<UserOutlined />} placeholder="请输入用户名" />
              </Form.Item>
              <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }]}>
                <Input.Password placeholder="请输入密码" />
              </Form.Item>
              <Button type="primary" htmlType="submit" block size="large" loading={authLoading}>
                {authMode === 'login' ? '登录进入工作台' : '注册并登录'}
              </Button>
            </Form>
          </Card>
        </div>
      </ConfigProvider>
    )
  }

  return (
    <ConfigProvider locale={zhCN} theme={{ token: { colorPrimary: '#1e3a8a' } }}>
      <Layout style={{ minHeight: '100vh' }}>
        <Sider width={215} theme="dark" style={{ background: '#0f172a' }}>
          <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
            <Title level={5} style={{ color: '#fff', margin: 0 }}>
              ScholarAgent
            </Title>
            <Text style={{ color: '#94a3b8', fontSize: 12 }}>智研全流程科研助手</Text>
          </div>
          <Menu
            theme="dark"
            mode="inline"
            selectedKeys={[nav]}
            onClick={(e) => setNav(e.key as NavKey)}
            style={{ background: '#0f172a', marginTop: 8 }}
            items={[
              { key: 'library', icon: <BookOutlined />, label: '文献库与解析' },
              { key: 'reader', icon: <ReadOutlined />, label: '阅读与双向翻译' },
              { key: 'chat', icon: <RobotOutlined />, label: '抗幻觉 Agent 问答' },
              { key: 'scout', icon: <RadarChartOutlined />, label: '检索下钻与推荐' },
              { key: 'graph', icon: <ApartmentOutlined />, label: '引用图谱与综述' },
              { key: 'copilot', icon: <EditOutlined />, label: '写作与数据 Copilot' },
            ]}
          />
        </Sider>

        <Layout>
          <Header
            style={{
              background: '#fff',
              padding: '0 20px',
              height: 52,
              lineHeight: '52px',
              borderBottom: '1px solid #e2e8f0',
              display: 'flex',
              justifyContent: 'space-between',
              alignItems: 'center',
            }}
          >
            <Space>
              <Text strong style={{ fontSize: 15 }}>
                {
                  {
                    library: '模块一 · PDF 文献管理、语义去重与细粒度多模态解析',
                    reader: '模块三 · 版式感知阅读、双向引用跳转与中英同步滚动翻译',
                    chat: '模块二 · 抗幻觉知识问答与 Multi-Agent 协同推理',
                    scout: '模块四 · 引用含金量筛选下钻、Sickle arXiv 追踪与个性化推荐',
                    graph: '模块五 · 局域引用拓扑图谱挖掘、自动综述与 Future Work 探索',
                    copilot: '模块六 · 无幻觉写作框架、架构拓扑图、三维雷达图与学术英译',
                  }[nav]
                }
              </Text>
            </Space>
            <Space>
              <Tag color={user.role === 'reviewer' ? 'gold' : 'blue'}>
                {user.username} ({user.role})
              </Tag>
              <Button size="small" icon={<LogoutOutlined />} onClick={handleLogout}>
                退出
              </Button>
            </Space>
          </Header>

          <Content style={{ padding: 16, background: '#f1f5f9' }}>
            {nav === 'library' && (
              <LibraryView
                papers={papers}
                onRefresh={() => void loadPapers()}
                onOpenReader={(pid, pg) => openReaderAt(pid, pg)}
              />
            )}
            {nav === 'reader' && (
              <ReaderView
                papers={papers}
                initialPaperId={readerState.paperId}
                initialPage={readerState.page}
                highlightBbox={readerState.bbox}
              />
            )}
            {nav === 'chat' && <AgentChatView papers={papers} onJumpEvidence={jumpFromCitation} />}
            {nav === 'scout' && <ScoutView papers={papers} onLibraryChanged={() => void loadPapers()} />}
            {nav === 'graph' && <GraphView papers={papers} />}
            {nav === 'copilot' && <CopilotView papers={papers} />}
          </Content>
        </Layout>
      </Layout>
    </ConfigProvider>
  )
}

export default App
