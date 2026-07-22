import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { AppShell } from '../components/AppShell'
import { FilesPage } from './FilesPage'
import { AgentsPage } from './AgentsPage'
import { KnowledgePage } from './KnowledgePage'
import { MemoryPage } from './MemoryPage'
import { ProfilePage } from './ProfilePage'
import { RuntimeModulesPage } from './RuntimeModulesPage'
import { MessagesPage } from './MessagesPage'
import { ExpandPage } from './ExpandPage'
import { RuntimeStatusPage } from './RuntimeStatusPage'
import { SensePage } from './SensePage'
import { SettingsPage } from './SettingsPage'
import { SkillsPage } from './SkillsPage'
import { TasksPage } from './TasksPage'
import { server } from '../test/server'

const pages = [
  { path: 'tasks', element: <TasksPage /> },
  { path: 'knowledge', element: <KnowledgePage /> },
  { path: 'memory', element: <MemoryPage /> },
  { path: 'agents', element: <AgentsPage /> },
  { path: 'skills', element: <SkillsPage /> },
  { path: 'sense', element: <SensePage /> },
  { path: 'files', element: <FilesPage /> },
  { path: 'messages', element: <MessagesPage /> },
  { path: 'expand', element: <ExpandPage /> },
  { path: 'status', element: <RuntimeStatusPage /> },
  { path: 'runtime', element: <RuntimeModulesPage /> },
  { path: 'profile', element: <ProfilePage /> },
  { path: 'settings', element: <SettingsPage /> },
]

function renderPage(path: string) {
  const router = createMemoryRouter([{ path: '/', element: <AppShell />, children: pages }], { initialEntries: [`/${path}?user=kesepain&session=s1`] })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
}

describe('V16 module pages', () => {
  it('任务页展示真实空态与任务分类', async () => {
    renderPage('tasks')
    expect(await screen.findByRole('heading', { name: '任务中枢' })).toBeInTheDocument()
    expect(await screen.findByText('暂无任务计划')).toBeInTheDocument()
    expect(screen.getAllByText('定时任务').length).toBeGreaterThan(0)
    expect(screen.getByText('选择一个计划')).toBeInTheDocument()
    expect(screen.queryByText('提示：选择未运行或已完成的计划查看详情')).not.toBeInTheDocument()
  })

  it('定时任务和执行记录使用统一双栏卡片并按状态限制操作', async () => {
    server.use(http.get('/api/users/kesepain/tasks', () => HttpResponse.json({
      user: 'kesepain',
      summary: { active_plans: 0, waiting_plans: 0, enabled_crons: 1, completed_plans: 1 },
      plans: [{
        plan_id: 'plan_completed', title: '较旧的任务计划', description: '已完成计划描述', status: 'completed', auto_accept: false,
        reminder: '', source: 'web', session_id: 's1', current_step: '', revision: 2,
        created_at: '2026-07-20T08:00:00+08:00', updated_at: '2026-07-20T09:00:00+08:00',
        progress: { completed: 1, total: 1, percent: 100 },
        steps: [{ step_id: 'step_1', title: '完成步骤', description: '', status: 'completed', depends_on: [], critical: false, tool_name: '', started_at: '', finished_at: '2026-07-20T09:00:00+08:00' }],
      }, {
        plan_id: 'plan_cancelled', title: '已取消的任务计划', description: '取消后允许删除', status: 'cancelled', auto_accept: false,
        reminder: '', source: 'web', session_id: 's1', current_step: '', revision: 2,
        created_at: '2026-07-20T08:00:00+08:00', updated_at: '2026-07-20T08:30:00+08:00',
        progress: { completed: 0, total: 1, percent: 0 },
        steps: [{ step_id: 'step_1', title: '未执行步骤', description: '', status: 'cancelled', depends_on: [], critical: false, tool_name: '', started_at: '', finished_at: '' }],
      }],
      cron_tasks: [
        { task_id: 'cron_enabled', title: '待执行定时任务', user_defined: true, status: 'enabled', type: 'daily', time: '08:00', next_run_at: '2026-07-22T08:00:00+08:00', latest_run_at: '', created_at: '2026-07-20T08:00:00+08:00', last_state: 'never' },
        { task_id: 'cron_running', title: '运行中定时任务', user_defined: true, status: 'running', type: 'recurring', interval_seconds: 3600, next_run_at: '2026-07-21T12:00:00+08:00', latest_run_at: '', created_at: '2026-07-20T08:00:00+08:00', last_state: 'never' },
        { task_id: 'cron_completed', title: '较新的定时任务', user_defined: true, status: 'completed', type: 'once', next_run_at: '', latest_run_at: '2026-07-20T10:00:00+08:00', created_at: '2026-07-20T08:00:00+08:00', last_state: 'completed' },
      ],
      executions: [],
    })))
    renderPage('tasks')
    await screen.findByText('较旧的任务计划')
    const cancelledPlanCard = screen.getByText('已取消的任务计划').closest('article')!
    expect(within(cancelledPlanCard).getByRole('button', { name: '删除' })).toBeInTheDocument()
    expect(within(cancelledPlanCard).queryByRole('button', { name: '修改' })).not.toBeInTheDocument()
    fireEvent.click(cancelledPlanCard)
    expect(await screen.findByText('取消后允许删除')).toBeInTheDocument()
    expect(screen.getByText('plan_cancelled')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '定时任务' }))

    expect(await screen.findByRole('heading', { name: '定时任务查看' })).toBeInTheDocument()
    const enabledCard = screen.getAllByText('待执行定时任务').find((node) => node.closest('article'))!.closest('article')!
    expect(within(enabledCard).getByRole('button', { name: '暂停' })).toBeInTheDocument()
    expect(within(enabledCard).getByRole('button', { name: '删除' })).toBeInTheDocument()
    const runningCard = screen.getAllByText('运行中定时任务').find((node) => node.closest('article'))!.closest('article')!
    expect(within(runningCard).getByRole('button', { name: '暂停' })).toBeInTheDocument()
    expect(within(runningCard).queryByRole('button', { name: '删除' })).not.toBeInTheDocument()
    const completedCard = screen.getAllByText('较新的定时任务').find((node) => node.closest('article'))!.closest('article')!
    expect(within(completedCard).getByRole('button', { name: '删除' })).toBeInTheDocument()
    expect(within(completedCard).queryByRole('button', { name: '暂停' })).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '执行记录' }))
    expect(await screen.findByRole('heading', { name: '执行记录查看' })).toBeInTheDocument()
    const newer = screen.getAllByText('较新的定时任务').find((node) => node.closest('article'))!.closest('article')!
    const older = screen.getAllByText('较旧的任务计划').find((node) => node.closest('article'))!.closest('article')!
    expect(newer.compareDocumentPosition(older) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(within(newer).getAllByRole('button')).toHaveLength(1)
    expect(within(newer).getByRole('button', { name: '删除' })).toBeInTheDocument()
    expect(screen.getByText('只读记录，不提供编辑操作')).toBeInTheDocument()
  })

  it('知识页展示文件索引元数据', async () => {
    renderPage('knowledge')
    expect(await screen.findByRole('heading', { name: '知识库' })).toBeInTheDocument()
    expect(await screen.findByText('个人笔记')).toBeInTheDocument()
    expect(await screen.findByText('共享笔记')).toBeInTheDocument()
    expect(screen.getAllByText('共享层').length).toBeGreaterThan(0)
    expect(screen.getByText('编辑查看')).toBeInTheDocument()
    expect(screen.queryByText(/当前显示\s+\d+\s*\/\s*\d+/)).not.toBeInTheDocument()
    expect(screen.queryByText('外接项目 · kemo-graph')).not.toBeInTheDocument()
    expect(screen.queryByText('知识集合')).not.toBeInTheDocument()
  })

  it('知识页按层级搜索并在保存后提示刷新索引', async () => {
    renderPage('knowledge')
    await screen.findByText('个人笔记')
    fireEvent.click(screen.getByRole('button', { name: '用户层' }))
    const search = screen.getByPlaceholderText('用户层：搜索文件名或标题…')
    fireEvent.change(search, { target: { value: '共享笔记' } })
    expect(await screen.findByText('没有匹配的知识文件')).toBeInTheDocument()
    fireEvent.change(search, { target: { value: '' } })
    fireEvent.click(await screen.findByText('个人笔记'))
    expect(await screen.findByRole('button', { name: '预览' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    expect(await screen.findByDisplayValue(/知识正文/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '保存编辑' }))
    expect(await screen.findByText('当前知识文件已更新，请提醒智能体刷新索引')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '我知道了，不用刷新' })).toBeInTheDocument()
  })

  it('临时重要记忆直接展示单文件编辑器而不展示记忆列表', async () => {
    server.use(http.get('/api/users/kesepain/memory/important', () => HttpResponse.json({
      user: 'kesepain',
      path: 'users/kesepain/memory_temporary_important.md',
      content: '# 临时重要记忆\n\n需要直接编辑的内容。',
      size: 24,
      updated_at: '2026-07-22T16:46:02+08:00',
    })))
    renderPage('memory')
    await screen.findByRole('heading', { name: '记忆' })
    fireEvent.click(await screen.findByRole('button', { name: '临时重要记忆' }))

    await waitFor(() => expect(screen.getByPlaceholderText('输入记忆内容……')).toHaveValue('# 临时重要记忆\n\n需要直接编辑的内容。'))
    expect(screen.queryByPlaceholderText('搜索当前记忆栏……')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '关闭编辑查看' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /Markdown 预览/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /保存编辑/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /删除此记忆/ })).not.toBeInTheDocument()
  })

  it('技能页展示五类库存、查看面板和按归属区分的操作', async () => {
    renderPage('skills')
    expect(await screen.findByRole('heading', { name: '工具与技能' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: 'clock' })).toBeInTheDocument()
    expect(screen.getAllByText('基础插件').length).toBeGreaterThan(0)
    expect(screen.getByRole('tab', { name: '共享技能' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '智能体生成技能' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '用户自建技能' })).toBeInTheDocument()
    expect(await screen.findByText('技能正文')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '下载' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '禁用' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '禁用' }))
    expect(await screen.findByText('clock 已禁用')).toBeInTheDocument()
    expect(screen.queryByText('用户技能策略')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '用户自建技能' }))
    expect(await screen.findByRole('heading', { name: '用户自建技能' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '编辑' }))
    expect(await screen.findByRole('textbox', { name: '技能 Markdown 编辑器' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除' })).toBeInTheDocument()
    expect(screen.queryByText('项目层')).not.toBeInTheDocument()
  })

  it('感知页默认展示全局注入，选择模块后展示采集与注入详情', async () => {
    renderPage('sense')
    expect(await screen.findByRole('heading', { name: '感知' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '运行时感知' })).toBeInTheDocument()
    expect(screen.getByText('全局感知注入预览')).toBeInTheDocument()
    expect(screen.getByText('全部注入 Markdown')).toBeInTheDocument()
    expect(screen.getByText('System Prompt / Global Sense')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /运行时感知/ }))
    expect(await screen.findByText('模块采集信息')).toBeInTheDocument()
    expect(screen.getByText('系统提示词注入片段')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '信息更新' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '删除模块' })).toBeInTheDocument()
    expect(screen.getByRole('switch', { name: '运行时感知 白名单' })).toBeChecked()
    expect(screen.queryByText('项目层')).not.toBeInTheDocument()
  })

  it('运行状态页使用五个栏目并支持顶部摘要卡快捷切换', async () => {
    renderPage('status')
    expect(await screen.findByRole('heading', { name: '运行状态' })).toBeInTheDocument()

    const sectionTabs = within(await screen.findByRole('tablist', { name: '运行状态栏目' }))
    expect(sectionTabs.getByRole('tab', { name: '系统提示词上下文预览' })).toHaveAttribute('aria-selected', 'true')
    expect(sectionTabs.getByRole('tab', { name: '今日 Token 情况' })).toBeInTheDocument()
    expect(sectionTabs.getByRole('tab', { name: '用户 API 配置' })).toBeInTheDocument()
    expect(sectionTabs.getByRole('tab', { name: '外部组件与消息路由' })).toBeInTheDocument()
    expect(sectionTabs.getByRole('tab', { name: '调度与维护' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: '拼接组件状态' }))
    expect(await screen.findByText('用户人格')).toBeInTheDocument()
    expect(screen.queryByText('users/kesepain/user_soul.md')).not.toBeInTheDocument()

    fireEvent.click(sectionTabs.getByRole('tab', { name: '今日 Token 情况' }))
    expect(await screen.findByText('327,845')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'API 配置，打开用户 API 配置栏目' }))
    expect(sectionTabs.getByRole('tab', { name: '用户 API 配置' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('heading', { name: '用户 API 配置' })).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '外部消息路由，打开外部组件与消息路由栏目' }))
    expect(sectionTabs.getByRole('tab', { name: '外部组件与消息路由' })).toHaveAttribute('aria-selected', 'true')
    expect(await screen.findByText('OneBot 正向 WebSocket')).toBeInTheDocument()

    fireEvent.click(sectionTabs.getByRole('tab', { name: '调度与维护' }))
    expect(screen.getByText('今日记忆更新与升级')).toBeInTheDocument()
    expect(screen.getByText('系统及定时任务执行记录')).toBeInTheDocument()
    expect(screen.getByText('记忆碎片到期晋升检查')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '对话轮次，打开系统提示词上下文预览栏目' }))
    expect(sectionTabs.getByRole('tab', { name: '系统提示词上下文预览' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('button', { name: '对话轮次，打开系统提示词上下文预览栏目' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.getByRole('button', { name: '刷新运行状态' })).toBeInTheDocument()
  })

  it('子智能体页按全局与用户层查看详情并只允许删除用户子代理', async () => {
    let deleted = false
    const response = () => ({
      user: 'kesepain',
      summary: { total: deleted ? 1 : 2, enabled: deleted ? 1 : 2, global: 1, user: deleted ? 0 : 1 },
      agents: [
        { name: 'context_manage', version: '1.2.0', description: '上下文管理子代理', enabled: true, source: 'global', trigger: '上下文接近上限时', rules: '# context_manage\n\n压缩并保留关键事实。\n\n| 场景 | 动作 |\n| --- | --- |\n| Token 超限 | 压缩旧轮次 |', executor: 'executor.py:execute', execution: 'sync', model_profile: 'cheap', exposure: 'tool', root: 'agents/context_manage', files: [] },
        ...(deleted ? [] : [{ name: 'custom_agent', version: '1.0.0', description: '用户自定义子代理', enabled: true, source: 'user', trigger: '用户明确指定时', rules: '# custom_agent\n\n按用户规则处理输入。', executor: 'builtin:llm', execution: 'sync', model_profile: 'default', exposure: 'tool', root: 'users/kesepain/agents/custom_agent', files: [] }]),
      ],
    })
    server.use(
      http.get('/api/users/kesepain/agents', () => HttpResponse.json(response())),
      http.delete('/api/users/kesepain/agents/custom_agent', () => {
        deleted = true
        return HttpResponse.json({ user: 'kesepain', name: 'custom_agent', path: 'users/kesepain/agents/custom_agent', deleted: true })
      }),
    )
    renderPage('agents')

    expect(await screen.findByRole('heading', { name: '子智能体' })).toBeInTheDocument()
    expect(await screen.findByText('上下文接近上限时')).toBeInTheDocument()
    expect(screen.getAllByText('v1.2.0').length).toBeGreaterThan(0)
    expect(screen.getByText(/压缩并保留关键事实/)).toBeInTheDocument()
    expect(screen.getAllByRole('heading', { name: 'context_manage' }).some((heading) => heading.tagName === 'H1')).toBe(true)
    expect(screen.getByRole('table')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /复制子智能体名称 context_manage/ })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '删除' })).not.toBeInTheDocument()
    const summary = screen.getByRole('region', { name: '子智能体统计' })
    expect(within(within(summary).getByText('已发现').closest('article')!).getByText('1')).toBeInTheDocument()
    expect(within(within(summary).getByText('已启用').closest('article')!).getByText('1')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /用户层/ }))
    expect((await screen.findAllByText('用户自定义子代理')).length).toBeGreaterThan(0)
    expect(within(within(summary).getByText('已发现').closest('article')!).getByText('1')).toBeInTheDocument()
    expect(within(summary).getByText('当前层级子智能体总数')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    expect(screen.getByRole('alertdialog', { name: '确认删除用户子智能体' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(await screen.findByText('已删除用户子智能体 custom_agent')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('用户层暂无子智能体')).toBeInTheDocument())
    expect(within(within(summary).getByText('已发现').closest('article')!).getByText('0')).toBeInTheDocument()
    expect(within(within(summary).getByText('已启用').closest('article')!).getByText('0')).toBeInTheDocument()
  })

  it('配置页使用六个纵向栏目并提供结构化字段编辑', async () => {
    renderPage('settings')
    expect(await screen.findByRole('heading', { name: '配置' })).toBeInTheDocument()
    expect(screen.queryByText('结构化配置与敏感字段保护')).not.toBeInTheDocument()
    expect(screen.getByText('界面主题')).toBeInTheDocument()
    expect(screen.getByText('72%')).toBeInTheDocument()
    expect(screen.getByText('88%')).toBeInTheDocument()
    expect(screen.getByText('105%')).toBeInTheDocument()
    expect(screen.queryByText('Prompt 与 Expand')).not.toBeInTheDocument()
    expect(screen.queryByText('配置文件 JSON')).not.toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '模型与 Provider ›' }))
    const providerSelect = await screen.findByRole('combobox', { name: 'Provider 类型' })
    expect(providerSelect).toHaveTextContent('kemo')
    expect(providerSelect).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(providerSelect)
    expect(screen.getByRole('listbox', { name: 'Provider 类型选项' })).toBeInTheDocument()
    expect(screen.getByRole('option', { name: /chat/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('option', { name: /kemo/ }))
    expect(screen.getByLabelText('模型')).toHaveValue('test-model')
    expect(screen.getByLabelText('Base URL')).toHaveValue('http://127.0.0.1:8741')
    expect(screen.getByLabelText('API Key')).toHaveAttribute('type', 'password')
    expect(screen.getByRole('switch', { name: '流式输出' })).toHaveAttribute('aria-checked', 'false')
    expect(screen.getByRole('switch', { name: '流式输出' })).toHaveTextContent('已关闭')
    expect(screen.getByLabelText('图片识别')).toHaveValue('vision-model')
    fireEvent.change(screen.getByLabelText('模型'), { target: { value: 'next-model' } })
    fireEvent.click(screen.getByRole('button', { name: '保存模型与 Provider' }))
    expect(await screen.findByText('模型与 Provider 已保存')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '用户切换 ›' }))
    expect(screen.queryByRole('combobox', { name: '可切换用户' })).not.toBeInTheDocument()
    expect(await screen.findByRole('button', { name: '切换到用户 reviewer' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '记忆与上下文 ›' }))
    expect(await screen.findByLabelText('Token 上限')).toHaveValue(1000000)
    expect(screen.getByLabelText('Token 压缩比例')).toHaveValue('0.3')
    expect(screen.getByLabelText('周记忆上限')).toHaveValue(100)

    fireEvent.click(screen.getByRole('button', { name: '权限边界 ›' }))
    expect(await screen.findByRole('switch', { name: '使用共享知识库' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByLabelText('插件白名单输入')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: '运行限制 ›' }))
    expect(await screen.findByLabelText('工具调用超时')).toHaveValue(240)
    expect(screen.getByRole('switch', { name: '自动接受任务计划' })).toHaveAttribute('aria-checked', 'false')
    fireEvent.click(screen.getByRole('button', { name: '重启智能体' }))
    const restartDialog = screen.getByRole('alertdialog', { name: '确认重启智能体' })
    expect(restartDialog).toBeInTheDocument()
    expect(restartDialog.parentElement?.parentElement).toBe(document.body)
    expect(screen.getByText('您确定要重启吗？')).toBeInTheDocument()
    expect(screen.getByText(/智能体在执行任务时重启可能会出现故障/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认重启' }))
    expect(await screen.findByText(/重启请求已提交/)).toBeInTheDocument()
  })

  it('文件空间支持逐层目录、分区搜索与严格的区域操作权限', async () => {
    renderPage('files')
    expect(await screen.findByRole('heading', { name: '文件空间' })).toBeInTheDocument()
    expect((await screen.findAllByText('readme.txt')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '上传到【用户上传】' })).toBeEnabled()

    fireEvent.click(screen.getByRole('button', { name: '打开目录 screenshots' }))
    expect((await screen.findAllByText('shot.png')).length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('button', { name: '打开目录 release' }))
    expect((await screen.findAllByText('final-shot.png')).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('button', { name: '在当前区域搜索' }))
    fireEvent.change(screen.getByRole('textbox', { name: '搜索用户上传' }), { target: { value: 'readme' } })
    expect((await screen.findAllByText('readme.txt')).length).toBeGreaterThan(0)
    expect(screen.getByText('全区域搜索结果')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('tab', { name: /智能体产物/ }))
    expect(screen.getByRole('button', { name: '上传到【用户上传】' })).toBeDisabled()
    expect((await screen.findAllByRole('button', { name: /重命名 readme.txt/ })).length).toBeGreaterThan(0)
    expect((await screen.findAllByRole('link', { name: /下载 readme.txt/ })).length).toBeGreaterThan(0)

    fireEvent.click(screen.getByRole('tab', { name: /全局临时/ }))
    expect((await screen.findAllByText('cache.tmp')).length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '上传到【用户上传】' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: /重命名 cache.tmp/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /下载 cache.tmp/ })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('checkbox', { name: '选择 cache.tmp' }))
    fireEvent.click(screen.getByRole('button', { name: '删除已选' }))
    expect(screen.getByRole('alertdialog', { name: '确认删除临时文件' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '确认删除' }))
    expect(await screen.findByText('已删除 1 个全局临时文件')).toBeInTheDocument()
  })

  it('运行模块展示子代理、消息传输和三层 Expand', async () => {
    renderPage('runtime')
    expect(await screen.findByRole('heading', { name: '运行模块' })).toBeInTheDocument()
    expect(await screen.findByText('context_manage')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /外部消息/ }))
    expect(await screen.findByText('OneBot 正向 WebSocket')).toBeInTheDocument()
    expect(screen.getByText('123456')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: /Expand/ }))
    expect(await screen.findByText('example')).toBeInTheDocument()
    expect(screen.getByText('已注册')).toBeInTheDocument()
  })

  it('外部消息页展示绑定模块详情并支持日志筛选和连接检测', async () => {
    renderPage('messages')
    expect(await screen.findByRole('heading', { name: '外部消息' })).toBeInTheDocument()
    expect((await screen.findAllByText('OneBot 正向 WebSocket')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('message/out/onebot').length).toBeGreaterThan(0)
    expect(screen.getByText('模块详情')).toBeInTheDocument()
    expect(screen.getByText('message/out/onebot/files')).toBeInTheDocument()
    expect(screen.getByText('消息日志')).toBeInTheDocument()
    expect(screen.getByText('请查看今日任务')).toBeInTheDocument()
    expect(screen.getByText('1 / 2')).toBeInTheDocument()
    expect(screen.getByText('历史日志 10')).toBeInTheDocument()
    expect(screen.queryByText('历史日志 11')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '下一页日志' }))
    expect(await screen.findByText('历史日志 11')).toBeInTheDocument()
    expect(screen.queryByText('请查看今日任务')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '文件' }))
    expect(await screen.findByText('meeting_notes.docx')).toBeInTheDocument()
    expect(screen.queryByText('请查看今日任务')).not.toBeInTheDocument()
    expect(screen.getByText('1 / 1')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '上一页日志' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '下一页日志' })).toBeDisabled()
    const connectionButtons = screen.getAllByRole('button', { name: '检测连接' })
    expect(connectionButtons).toHaveLength(2)
    expect(screen.queryByRole('button', { name: '查看日志' })).not.toBeInTheDocument()
    fireEvent.click(connectionButtons[0])
    expect(await screen.findByText('onebot 连接状态已更新')).toBeInTheDocument()
  })

  it('拓展页面展示真实模块详情、操作文档与白名单控制', async () => {
    renderPage('expand')
    expect(await screen.findByRole('heading', { name: '拓展' })).toBeInTheDocument()
    expect(await screen.findByRole('heading', { name: '智能灯光控制', level: 2 })).toBeInTheDocument()
    expect(screen.getAllByText('控制客厅与卧室的智能灯组。').length).toBeGreaterThan(0)
    expect(screen.getAllByText(/客厅已开启/).length).toBe(2)
    expect(screen.getByRole('switch', { name: '智能灯光控制 白名单' })).toBeChecked()
    fireEvent.click(screen.getByRole('button', { name: /查看操作文档/ }))
    expect((await screen.findAllByText('用户要求开灯时调用 start_expand.py')).length).toBe(2)
    fireEvent.click(screen.getByRole('button', { name: /增加拓展模块/ }))
    expect(screen.getByRole('button', { name: /增加到用户层/ })).toBeInTheDocument()
  })

  it('新增独立栏目拥有各自页面', async () => {
    renderPage('memory')
    expect(await screen.findByRole('heading', { name: '记忆' })).toBeInTheDocument()
    expect(screen.queryByText(/^共\s*\d+\s*条$/)).not.toBeInTheDocument()
  })

  it('用户资料读取并原子保存人格 Markdown', async () => {
    renderPage('profile')
    expect(await screen.findByRole('heading', { name: '身份与人格' })).toBeInTheDocument()
    const editor = await screen.findByLabelText('用户人格 Markdown')
    expect(editor).toHaveValue('# 用户人格')
    fireEvent.change(editor, { target: { value: '# 更新后的用户人格' } })
    fireEvent.click(screen.getByRole('button', { name: '保存用户人格' }))
    expect(await screen.findByText('用户人格已原子写入。')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '编辑用户人格' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '下载用户人格' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '预览用户人格' }))
    expect(screen.getByLabelText('用户人格 Markdown 预览')).toHaveTextContent('更新后的用户人格')
    expect(screen.getByLabelText('全局人格 Markdown 预览')).toHaveTextContent('全局人格')
    expect(screen.getByRole('button', { name: '下载全局人格' })).toBeInTheDocument()
    expect(screen.queryByLabelText('全局人格 Markdown')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /保存全局人格/ })).not.toBeInTheDocument()
    expect(screen.queryByText('全局安全边界')).not.toBeInTheDocument()
  })
})
