import { Activity, BookOpen, Bot, BrainCircuit, FileSearch, FolderOpen, Brain, RadioTower, ListChecks, Settings, Shapes, Wrench } from 'lucide-react'

export function chatRunKey(user: string, sessionId: string) {
  return JSON.stringify([user, sessionId])
}

export type ConversationCommandAction = 'save' | 'clear' | 'compress' | 'retry'
export const CONVERSATION_COMMAND_EVENT = 'kemo:conversation-command'

export const slashCommands = [
  { command: '/new [名称]', description: '新建并切换会话' },
  { command: '/sessions', description: '列出全部已提交会话' },
  { command: '/use <会话ID>', description: '切换到指定会话' },
  { command: '/clear', description: '清空当前会话' },
  { command: '/history', description: '查看当前会话历史' },
  { command: '/status', description: '查看当前上下文占用状态' },
  { command: '/compress', description: '手动压缩当前上下文' },
  { command: '/memory', description: '列出当前用户记忆' },
  { command: '/remember <内容>', description: '保存一条永久记忆' },
  { command: '/forget <记忆ID或关键词>', description: '删除匹配的记忆' },
  { command: '/plans', description: '列出任务计划' },
  { command: '/plan <目标>', description: '创建任务计划' },
  { command: '/plan-show <计划ID>', description: '查看指定任务计划' },
  { command: '/plan-approve <计划ID>', description: '批准并执行任务计划' },
  { command: '/plan-pause <计划ID>', description: '暂停任务计划' },
  { command: '/plan-resume <计划ID>', description: '恢复任务计划' },
  { command: '/plan-cancel <计划ID>', description: '取消任务计划' },
  { command: '/crons', description: '列出定时任务' },
  { command: '/cron <自然语言要求>', description: '创建定时任务' },
  { command: '/cron-show <任务ID>', description: '查看指定定时任务' },
  { command: '/cron-pause <任务ID>', description: '暂停定时任务' },
  { command: '/cron-resume <任务ID>', description: '恢复定时任务' },
  { command: '/cron-cancel <任务ID>', description: '取消定时任务' },
  { command: '/cron-run <任务ID>', description: '立即执行定时任务' },
  { command: '/cron-start', description: '启动 CLI 定时调度器' },
  { command: '/cron-stop', description: '停止 CLI 定时调度器' },
  { command: '/exit 或 /quit', description: '退出 CLI 交互模式' },
]

export const navGroups = [
  {
    label: '核心工作区',
    showLabel: true,
    items: [
      { path: '/chat', label: '对话', icon: Bot },
      { path: '/tasks', label: '任务', icon: ListChecks },
      { path: '/knowledge', label: '知识库', icon: BookOpen },
      { path: '/memory', label: '记忆', icon: Brain },
    ],
  },
  {
    label: '运行能力',
    showLabel: false,
    items: [
      { path: '/agents', label: '子智能体', icon: Bot },
      { path: '/skills', label: '工具与技能', icon: Wrench },
      { path: '/sense', label: '感知', icon: BrainCircuit },
      { path: '/expand', label: '拓展', icon: Shapes },
    ],
  },
  {
    label: '资源与系统',
    showLabel: true,
    items: [
      { path: '/files', label: '文件空间', icon: FolderOpen },
      { path: '/messages', label: '外部消息', icon: RadioTower },
      { path: '/status', label: '运行状态', icon: Activity },
      { path: '/settings', label: '配置', icon: Settings },
      { path: '/profile', label: '身份与人格', icon: FileSearch },
    ],
  },
]

export const pageTitles: Record<string, string> = {
  '/chat': 'kemo-agent',
  '/tasks': '任务',
  '/knowledge': '知识库',
  '/memory': '记忆',
  '/agents': '子智能体',
  '/skills': '工具与技能',
  '/sense': '感知',
  '/expand': '拓展',
  '/files': '文件空间',
  '/messages': '外部消息',
  '/status': '运行状态',
  '/runtime': '运行模块',
  '/profile': '身份与人格',
  '/settings': '配置',
}

export const fontSizeLabels: Record<string, string> = { small: '小', medium: '中', large: '大' }

export function formatTokens(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0'
  if (value < 1000) return String(value)
  return `${(value / 1000).toFixed(value >= 100_000 ? 0 : 1)}K`
}

export function sessionLabel(sessionId: string) {
  if (sessionId.startsWith('web_') && sessionId.length > 16) return `Web 会话 · ${sessionId.slice(4, 12)}`
  return sessionId
}

type InjectionPolicyMode = 'round' | 'realtime' | 'disabled'

export function injectionPolicyPresentation(mode: InjectionPolicyMode | undefined) {
  if (mode === 'realtime') {
    return { mode: 'realtime', label: '实时注入', detail: '每次请求读取最新快照' }
  }
  if (mode === 'disabled') {
    return { mode: 'disabled', label: '不注入', detail: '未进入系统提示词' }
  }
  return { mode: 'round', label: '按轮注入', detail: '本轮使用固定快照' }
}

const LAST_ACTIVE_USER_KEY = 'kemo-last-active-user'

export function readLastActiveUser() {
  try {
    return window.localStorage.getItem(LAST_ACTIVE_USER_KEY)?.trim() || ''
  } catch {
    return ''
  }
}

export function persistLastActiveUser(user: string) {
  const normalized = user.trim()
  if (!normalized) return
  try {
    window.localStorage.setItem(LAST_ACTIVE_USER_KEY, normalized)
  } catch {
    // Browser storage can be disabled; the URL remains the in-page source of truth.
  }
}

export function resolveCurrentUser(
  requestedUser: string,
  rememberedUser: string,
  users: ReadonlyArray<{ name: string }> | undefined,
) {
  const requested = requestedUser.trim()
  if (requested) return requested
  if (!users) return ''
  const remembered = rememberedUser.trim()
  if (remembered && users.some((item) => item.name === remembered)) return remembered
  return users[0]?.name || ''
}
