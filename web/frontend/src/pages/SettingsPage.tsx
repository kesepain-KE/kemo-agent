import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, ChevronDown, LockKeyhole, Power, RefreshCw, Save } from 'lucide-react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import {
  ApiError,
  getGlobalConfig,
  getSettings,
  getUserConfig,
  patchGlobalConfig,
  patchPreferences,
  patchUserConfig,
  restartSystem,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { ModuleError, ModuleFrame, RefreshActionButton, StatusChip } from '../components/ModuleUi'
import { useUiStore } from '../store/ui'

type SettingsTab = 'appearance' | 'provider' | 'users' | 'memory' | 'permissions' | 'runtime'
type ProviderType = 'chat' | 'kemo'
type MultimodalKey = 'vision' | 'image_generation' | 'image_edit' | 'audio_transcription' | 'speech_generation' | 'speech_to_speech' | 'video_generation'
type AgentModelProfile = 'default' | 'cheap' | 'reasoning'
type RestartState = 'idle' | 'confirming' | 'restarting' | 'waiting' | 'failed'

interface UserConfigDraft {
  provider: { type: ProviderType; model: string; base_url: string; api_key: string; stream: boolean }
  agent_models: Record<AgentModelProfile, string>
  multimodal_models: Record<MultimodalKey, string>
  knowledge: { use_shared: boolean; use_global: boolean }
  kemo_graph: GraphDraft
  skills: { shared_whitelist: string[] }
  expand: { shared_whitelist: string[]; global_whitelist: string[] }
  perception: { global_whitelist: string[] }
  plugins: { whitelist: string[] }
  task_plan: { auto_accept: boolean }
}

interface GraphDraft {
  kemo_graph_global_knowledge: boolean
  kemo_graph_shared_knowledge: boolean
  kemo_graph_user_knowledge: boolean
  kemo_graph_temporary_memory: boolean
}

interface GlobalConfigDraft {
  agents: { token_limit: number; token_compression_ratio: number; max_rounds: number; rounds_after_compression: number }
  memory: { temporary_injection_limits: { seven_days: number; one_month: number; half_year: number } }
  kemo_graph: GraphDraft
  tools: { timeout: number; max_iterations: number }
  history: { consecutive_tool_fail_limit: number }
  task_plan: { max_steps: number }
  provider_runtime: { max_concurrent_requests: number; request_semaphore_timeout: number }
  web: { max_concurrent_chats: number; max_pending_chats: number; pending_chat_timeout: number }
  message: { max_workers: number; max_queued_messages: number }
  cron: { poll_interval: number; avoid_congestion: boolean; congestion_threshold_ratio: number }
  agent_runtime: { default_timeout: number; queue_maxsize: number }
}

interface SaveRequest {
  label: string
  userChanges?: Record<string, unknown>
  globalChanges?: Record<string, unknown>
}

const settingsTabs: Array<{ id: SettingsTab; label: string }> = [
  { id: 'appearance', label: '外观与主题' },
  { id: 'provider', label: '模型与 Provider' },
  { id: 'users', label: '用户切换' },
  { id: 'memory', label: '记忆与上下文' },
  { id: 'permissions', label: '权限边界' },
  { id: 'runtime', label: '运行限制' },
]

const settingsTabIds = new Set<SettingsTab>(settingsTabs.map((item) => item.id))

const multimodalFields: Array<{ key: MultimodalKey; label: string; description: string }> = [
  { key: 'vision', label: '图片识别', description: '图片分析、OCR 与视觉理解模型' },
  { key: 'image_generation', label: '图片生成', description: '文本生成图片的专用模型' },
  { key: 'image_edit', label: '图片编辑', description: '图片修改、局部重绘与图生图模型' },
  { key: 'audio_transcription', label: '语音识别', description: '音频转写与语音转文字模型' },
  { key: 'speech_generation', label: '语音生成', description: '文本转语音模型' },
  { key: 'speech_to_speech', label: '语音生语音', description: '语音到语音的转换模型' },
  { key: 'video_generation', label: '视频生成', description: '文本或素材生成视频的模型' },
]

const agentModelFields: Array<{ key: AgentModelProfile; label: string; description: string }> = [
  { key: 'default', label: '默认子智能体模型', description: '普通子智能体使用；留空时继承主对话模型' },
  { key: 'cheap', label: '轻量子智能体模型', description: '摘要、上下文整理等轻量任务使用；留空时继承主对话模型' },
  { key: 'reasoning', label: '推理子智能体模型', description: '任务规划和深度整理使用；留空时继承主对话模型' },
]

const graphFields: Array<{ key: keyof GraphDraft; label: string }> = [
  { key: 'kemo_graph_global_knowledge', label: '图谱—全局知识库' },
  { key: 'kemo_graph_shared_knowledge', label: '图谱—共享知识库' },
  { key: 'kemo_graph_user_knowledge', label: '图谱—用户知识库' },
  { key: 'kemo_graph_temporary_memory', label: '图谱—临时记忆' },
]

function isSettingsTab(value: string | null): value is SettingsTab {
  return value !== null && settingsTabIds.has(value as SettingsTab)
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}
}

function stringValue(value: unknown, fallback = '') {
  return typeof value === 'string' ? value : fallback
}

function numberValue(value: unknown, fallback: number) {
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback
}

function booleanValue(value: unknown, fallback: boolean) {
  return typeof value === 'boolean' ? value : fallback
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function graphDraft(value: unknown): GraphDraft {
  const item = record(value)
  return {
    kemo_graph_global_knowledge: booleanValue(item.kemo_graph_global_knowledge, false),
    kemo_graph_shared_knowledge: booleanValue(item.kemo_graph_shared_knowledge, false),
    kemo_graph_user_knowledge: booleanValue(item.kemo_graph_user_knowledge, false),
    kemo_graph_temporary_memory: booleanValue(item.kemo_graph_temporary_memory, false),
  }
}

function buildUserDraft(config: Record<string, unknown>): UserConfigDraft {
  const provider = record(config.provider)
  const agentModels = record(config.agent_models)
  const multimodal = record(config.multimodal_models)
  const knowledge = record(config.knowledge)
  const skills = record(config.skills)
  const expand = record(config.expand)
  const perception = record(config.perception)
  const plugins = record(config.plugins)
  const taskPlan = record(config.task_plan)
  return {
    provider: {
      type: provider.type === 'kemo' ? 'kemo' : 'chat',
      model: stringValue(provider.model),
      base_url: stringValue(provider.base_url),
      api_key: stringValue(provider.api_key),
      stream: booleanValue(provider.stream, true),
    },
    agent_models: {
      default: stringValue(agentModels.default),
      cheap: stringValue(agentModels.cheap),
      reasoning: stringValue(agentModels.reasoning),
    },
    multimodal_models: {
      vision: stringValue(multimodal.vision),
      image_generation: stringValue(multimodal.image_generation),
      image_edit: stringValue(multimodal.image_edit),
      audio_transcription: stringValue(multimodal.audio_transcription),
      speech_generation: stringValue(multimodal.speech_generation),
      speech_to_speech: stringValue(multimodal.speech_to_speech),
      video_generation: stringValue(multimodal.video_generation),
    },
    knowledge: {
      use_shared: booleanValue(knowledge.use_shared, true),
      use_global: booleanValue(knowledge.use_global, true),
    },
    kemo_graph: graphDraft(config.kemo_graph),
    skills: { shared_whitelist: stringList(skills.shared_whitelist) },
    expand: {
      shared_whitelist: stringList(expand.shared_whitelist),
      global_whitelist: stringList(expand.global_whitelist),
    },
    perception: { global_whitelist: stringList(perception.global_whitelist) },
    plugins: { whitelist: stringList(plugins.whitelist) },
    task_plan: { auto_accept: booleanValue(taskPlan.auto_accept, false) },
  }
}

function buildGlobalDraft(config: Record<string, unknown>): GlobalConfigDraft {
  const agents = record(config.agents)
  const memory = record(config.memory)
  const memoryLimits = record(memory.temporary_injection_limits)
  const tools = record(config.tools)
  const history = record(config.history)
  const taskPlan = record(config.task_plan)
  const providerRuntime = record(config.provider_runtime)
  const web = record(config.web)
  const message = record(config.message)
  const cron = record(config.cron)
  const agentRuntime = record(config.agent_runtime)
  return {
    agents: {
      token_limit: numberValue(agents.token_limit, 1_000_000),
      token_compression_ratio: numberValue(agents.token_compression_ratio, 0.3),
      max_rounds: numberValue(agents.max_rounds, 80),
      rounds_after_compression: numberValue(agents.rounds_after_compression, 20),
    },
    memory: { temporary_injection_limits: {
      seven_days: numberValue(memoryLimits.seven_days, 100),
      one_month: numberValue(memoryLimits.one_month, 200),
      half_year: numberValue(memoryLimits.half_year, 300),
    } },
    kemo_graph: graphDraft(config.kemo_graph),
    tools: {
      timeout: numberValue(tools.timeout, 240),
      max_iterations: numberValue(tools.max_iterations, 8),
    },
    history: { consecutive_tool_fail_limit: numberValue(history.consecutive_tool_fail_limit, 5) },
    task_plan: { max_steps: numberValue(taskPlan.max_steps, 20) },
    provider_runtime: {
      max_concurrent_requests: numberValue(providerRuntime.max_concurrent_requests, 10),
      request_semaphore_timeout: numberValue(providerRuntime.request_semaphore_timeout, 300),
    },
    web: {
      max_concurrent_chats: numberValue(web.max_concurrent_chats, 3),
      max_pending_chats: numberValue(web.max_pending_chats, 5),
      pending_chat_timeout: numberValue(web.pending_chat_timeout, 30),
    },
    message: {
      max_workers: numberValue(message.max_workers, 8),
      max_queued_messages: numberValue(message.max_queued_messages, 20),
    },
    cron: {
      poll_interval: numberValue(cron.poll_interval, 30),
      avoid_congestion: booleanValue(cron.avoid_congestion, true),
      congestion_threshold_ratio: numberValue(cron.congestion_threshold_ratio, 0.2),
    },
    agent_runtime: {
      default_timeout: numberValue(agentRuntime.default_timeout, 600),
      queue_maxsize: numberValue(agentRuntime.queue_maxsize, 50),
    },
  }
}

function SettingRow({ title, description, control, source }: { title: string; description: string; control: ReactNode; source?: 'user' | 'global' }) {
  return <div className="setting-row"><span className="setting-copy"><strong>{title}{source ? <i className={`config-source ${source}`}>{source === 'user' ? '用户' : '全局'}</i> : null}</strong><span>{description}</span></span><span className="setting-control">{control}</span></div>
}

function Toggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) {
  return <button type="button" role="switch" aria-label={label} aria-checked={checked} className={`config-switch ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)}>
    <span>{checked ? '已开启' : '已关闭'}</span><i aria-hidden="true"><b /></i>
  </button>
}

function ProviderSelect({ value, onChange }: { value: ProviderType; onChange: (value: ProviderType) => void }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const options: Array<{ value: ProviderType; label: string; description: string }> = [
    { value: 'chat', label: 'chat', description: '兼容 /v1/chat/completions' },
    { value: 'kemo', label: 'kemo', description: '完整 Kemo Provider 协议' },
  ]

  useEffect(() => {
    const closeOnPointerDown = (event: PointerEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) setOpen(false)
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', closeOnPointerDown)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      document.removeEventListener('pointerdown', closeOnPointerDown)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [])

  return <div className="config-select-wrap" ref={rootRef}>
    <button
      type="button"
      className={`config-select-trigger ${open ? 'open' : ''}`}
      role="combobox"
      aria-label="Provider 类型"
      aria-controls="provider-type-options"
      aria-expanded={open}
      aria-haspopup="listbox"
      onClick={() => setOpen((current) => !current)}
    >
      <span><strong>{value}</strong><small>{value === 'chat' ? 'OpenAI Chat 兼容' : 'Kemo 原生协议'}</small></span><ChevronDown size={16} />
    </button>
    {open && <div className="config-select-popover" id="provider-type-options" role="listbox" aria-label="Provider 类型选项">
      {options.map((option) => <button
        type="button"
        role="option"
        aria-selected={value === option.value}
        className={value === option.value ? 'active' : ''}
        key={option.value}
        onClick={() => { onChange(option.value); setOpen(false) }}
      >
        <span><strong>{option.label}</strong><small>{option.description}</small></span>{value === option.value ? <Check size={16} /> : <i />}
      </button>)}
    </div>}
  </div>
}

function NumberInput({ label, value, min = 0, max, step = 1, onChange }: { label: string; value: number; min?: number; max?: number; step?: number; onChange: (value: number) => void }) {
  return <input className="config-field config-number" type="number" aria-label={label} value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} />
}

function TagInput({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
  const [draft, setDraft] = useState('')
  const commit = () => {
    const additions = draft.split(/[,，\n]/).map((item) => item.trim()).filter(Boolean)
    if (additions.length) onChange(Array.from(new Set([...value, ...additions])))
    setDraft('')
  }
  return <div className="config-tag-input" role="group" aria-label={label}>
    {value.map((item) => <span className="config-tag" key={item}>{item}<button type="button" aria-label={`移除 ${item}`} onClick={() => onChange(value.filter((entry) => entry !== item))}>×</button></span>)}
    <input
      aria-label={`${label}输入`}
      value={draft}
      placeholder={value.length ? '继续添加…' : '留空表示全部允许'}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ',' || event.key === '，') {
          event.preventDefault()
          commit()
        } else if (event.key === 'Backspace' && !draft && value.length) {
          onChange(value.slice(0, -1))
        }
      }}
    />
  </div>
}

function ConfigSaveBar({ label, description, pending, saved, onSave }: { label: string; description: string; pending: boolean; saved: boolean; onSave: () => void }) {
  return <div className="settings-savebar"><span><strong>{saved ? `${label.replace(/^保存/, '')} 已保存` : label}</strong><small>{description}</small></span><button className="module-btn primary" disabled={pending} onClick={onSave}><Save size={14} />{pending ? '保存中…' : label}</button></div>
}

export function SettingsPage() {
  const { user, chatRunning } = useOutletContext<ShellOutletContext>()
  const client = useQueryClient()
  const [searchParams, setSearchParams] = useSearchParams()
  const ui = useUiStore()
  const requestedTab = searchParams.get('tab')
  const [tab, setTab] = useState<SettingsTab>(() => isSettingsTab(requestedTab) ? requestedTab : 'appearance')
  const settingsQuery = useQuery({ queryKey: ['settings', user], queryFn: () => getSettings(user), enabled: Boolean(user) })
  const userConfigQuery = useQuery({ queryKey: ['user-config', user], queryFn: () => getUserConfig(user), enabled: Boolean(user) })
  const globalConfigQuery = useQuery({ queryKey: ['global-config'], queryFn: getGlobalConfig, enabled: Boolean(user) })
  const [userDraft, setUserDraft] = useState<UserConfigDraft | null>(null)
  const [globalDraft, setGlobalDraft] = useState<GlobalConfigDraft | null>(null)
  const [initialApiKey, setInitialApiKey] = useState('')
  const [formError, setFormError] = useState('')
  const [savedLabel, setSavedLabel] = useState('')
  const [restartState, setRestartState] = useState<RestartState>('idle')
  const [restartMessage, setRestartMessage] = useState('')
  const restartTimerRef = useRef<number | null>(null)

  useEffect(() => () => {
    if (restartTimerRef.current !== null) window.clearTimeout(restartTimerRef.current)
  }, [])

  useEffect(() => {
    if (!userConfigQuery.data) return
    const next = buildUserDraft(userConfigQuery.data.config)
    setUserDraft(next)
    setInitialApiKey(next.provider.api_key)
  }, [userConfigQuery.data])

  useEffect(() => {
    if (globalConfigQuery.data) setGlobalDraft(buildGlobalDraft(globalConfigQuery.data.config))
  }, [globalConfigQuery.data])

  useEffect(() => {
    if (isSettingsTab(requestedTab)) setTab(requestedTab)
  }, [requestedTab])

  const saveMutation = useMutation({
    mutationFn: async ({ userChanges, globalChanges }: SaveRequest) => {
      await Promise.all([
        userChanges ? patchUserConfig(user, userChanges) : Promise.resolve(),
        globalChanges ? patchGlobalConfig(globalChanges) : Promise.resolve(),
      ])
    },
    onSuccess: async (_, request) => {
      setFormError('')
      setSavedLabel(request.label)
      await Promise.all([
        client.invalidateQueries({ queryKey: ['settings', user] }),
        client.invalidateQueries({ queryKey: ['user-config', user] }),
        client.invalidateQueries({ queryKey: ['global-config'] }),
        client.invalidateQueries({ queryKey: ['overview', user] }),
        client.invalidateQueries({ queryKey: ['runtime-status', user] }),
      ])
    },
    onError: (error) => setFormError(String(error)),
  })

  const switchTab = (nextTab: SettingsTab) => {
    setTab(nextTab)
    setFormError('')
  }

  const switchUser = (nextUser: string) => {
    if (chatRunning || nextUser === user) return
    const next = new URLSearchParams(searchParams)
    next.set('user', nextUser)
    next.delete('session')
    next.set('tab', 'users')
    setSearchParams(next)
  }

  const saveAppearance = (changes: { theme?: 'light' | 'dark'; font_size?: 'small' | 'medium' | 'large' }) => {
    void patchPreferences(user, changes)
  }

  const refreshAll = () => {
    setSavedLabel('')
    setFormError('')
    void Promise.all([settingsQuery.refetch(), userConfigQuery.refetch(), globalConfigQuery.refetch()])
  }
  const settingsRefreshing = settingsQuery.isFetching || userConfigQuery.isFetching || globalConfigQuery.isFetching

  const submit = (request: SaveRequest, validationError = '') => {
    if (validationError) {
      setFormError(validationError)
      return
    }
    setSavedLabel('')
    setFormError('')
    saveMutation.mutate(request)
  }

  const saveProvider = () => {
    if (!userDraft) return
    let validation = ''
    if (!userDraft.provider.model.trim()) validation = '模型名称不能为空。'
    else if (!userDraft.provider.base_url.trim()) validation = 'Base URL 不能为空。'
    else {
      try {
        const url = new URL(userDraft.provider.base_url)
        if (!['http:', 'https:'].includes(url.protocol)) validation = 'Base URL 只允许 http 或 https。'
      } catch { validation = 'Base URL 格式不正确。' }
    }
    const provider: Record<string, unknown> = {
      type: userDraft.provider.type,
      model: userDraft.provider.model.trim(),
      base_url: userDraft.provider.base_url.trim(),
      stream: userDraft.provider.stream,
    }
    if (userDraft.provider.api_key !== initialApiKey) provider.api_key = userDraft.provider.api_key
    submit({
      label: '保存模型与 Provider',
      userChanges: {
        provider,
        agent_models: userDraft.agent_models,
        multimodal_models: userDraft.multimodal_models,
      },
    }, validation)
  }

  const saveMemory = () => {
    if (!globalDraft) return
    const { agents } = globalDraft
    const memoryLimits = globalDraft.memory.temporary_injection_limits
    const integers = [agents.token_limit, agents.max_rounds, agents.rounds_after_compression, memoryLimits.seven_days, memoryLimits.one_month, memoryLimits.half_year]
    let validation = integers.every(Number.isInteger) ? '' : 'Token、轮次和记忆上限必须为整数。'
    if (!validation && agents.token_limit <= 0) validation = 'Token 上限必须大于 0。'
    if (!validation && (agents.token_compression_ratio <= 0 || agents.token_compression_ratio > 1)) validation = 'Token 压缩比例必须大于 0 且不超过 1。'
    if (!validation && agents.max_rounds <= 0) validation = '最大对话轮次必须大于 0。'
    if (!validation && (agents.rounds_after_compression < 0 || agents.rounds_after_compression > agents.max_rounds)) validation = '压缩后保留轮次必须介于 0 与最大对话轮次之间。'
    if (!validation && Object.values(memoryLimits).some((value) => value < 0)) validation = '记忆注入上限不能小于 0。'
    submit({ label: '保存记忆与上下文', globalChanges: { agents, memory: globalDraft.memory } }, validation)
  }

  const savePermissions = () => {
    if (!userDraft || !globalDraft) return
    submit({
      label: '保存权限边界',
      userChanges: {
        knowledge: userDraft.knowledge,
        kemo_graph: userDraft.kemo_graph,
        skills: userDraft.skills,
        expand: userDraft.expand,
        perception: userDraft.perception,
        plugins: userDraft.plugins,
      },
      globalChanges: { kemo_graph: globalDraft.kemo_graph },
    })
  }

  const saveRuntime = () => {
    if (!userDraft || !globalDraft) return
    const positiveIntegers = [globalDraft.tools.timeout, globalDraft.tools.max_iterations, globalDraft.history.consecutive_tool_fail_limit, globalDraft.task_plan.max_steps, globalDraft.cron.poll_interval, globalDraft.agent_runtime.default_timeout, globalDraft.provider_runtime.max_concurrent_requests, globalDraft.provider_runtime.request_semaphore_timeout, globalDraft.web.max_concurrent_chats, globalDraft.web.pending_chat_timeout]
    const nonnegativeIntegers = [globalDraft.web.max_pending_chats, globalDraft.message.max_queued_messages, globalDraft.agent_runtime.queue_maxsize]
    let validation = positiveIntegers.every((value) => Number.isInteger(value) && value > 0) ? '' : '超时、轮询和并发上限必须为大于 0 的整数。'
    if (!validation && !nonnegativeIntegers.every((value) => Number.isInteger(value) && value >= 0)) validation = '队列与等待槽上限必须为大于等于 0 的整数。'
    if (!validation && (globalDraft.cron.congestion_threshold_ratio <= 0 || globalDraft.cron.congestion_threshold_ratio > 1)) validation = 'Cron 退避阈值必须大于 0 且不超过 1。'
    submit({
      label: '保存运行限制',
      userChanges: { task_plan: userDraft.task_plan },
      globalChanges: {
        tools: globalDraft.tools,
        history: globalDraft.history,
        task_plan: globalDraft.task_plan,
        cron: globalDraft.cron,
        agent_runtime: globalDraft.agent_runtime,
        provider_runtime: globalDraft.provider_runtime,
        web: globalDraft.web,
        message: globalDraft.message,
      },
    }, validation)
  }

  const confirmRestart = async () => {
    if (chatRunning || restartState === 'restarting' || restartState === 'waiting') return
    const port = Number.parseInt(window.location.port || '80', 10)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setRestartState('failed')
      setRestartMessage('无法从当前网页地址识别有效端口。')
      return
    }
    setRestartState('restarting')
    setRestartMessage(`正在请求智能体使用端口 ${port} 重新启动…`)
    try {
      await restartSystem(port)
      setRestartMessage(`重启请求已提交，正在等待端口 ${port} 恢复…`)
    } catch (error) {
      if (error instanceof ApiError) {
        setRestartState('failed')
        setRestartMessage(error.message)
        return
      }
      setRestartMessage('服务连接已中断，正在等待新实例接管当前端口…')
    }
    setRestartState('waiting')
    restartTimerRef.current = window.setTimeout(() => window.location.reload(), 4000)
  }

  return <ModuleFrame
    kicker="Configuration Overview"
    title="配置"
    description="通过结构化字段管理界面、Provider、上下文、权限和运行限制；敏感凭据始终脱敏。"
    actions={<RefreshActionButton pending={settingsRefreshing} label="重新读取" pendingLabel="读取中…" onClick={refreshAll} />}
  >
    {settingsQuery.isError || userConfigQuery.isError || globalConfigQuery.isError ? <ModuleError message="配置读取失败，请检查配置文件格式或 Web API。" /> : null}
    {formError ? <ModuleError message={formError} /> : null}
    <div className="settings-layout">
      <nav className="settings-nav" aria-label="配置分类">
        {settingsTabs.map((item) => <button key={item.id} className={tab === item.id ? 'active' : ''} onClick={() => switchTab(item.id)}><span>{item.label}</span><span>›</span></button>)}
      </nav>

      <div className="settings-content">
        {tab === 'appearance' ? <>
          <article className="setting-section">
            <div className="setting-section-head"><strong>界面主题</strong><span>只影响当前用户的 Web 外观，不修改智能体运行配置。</span></div>
            <div className="setting-row theme-setting-row">
              <span className="setting-copy"><strong>明暗主题</strong><span>高级白与高级黑使用相同层级和交互语义。</span></span>
              <div className="theme-choice-group" role="radiogroup" aria-label="界面主题">
                <button className={`theme-choice ${ui.theme === 'light' ? 'active' : ''}`} role="radio" aria-checked={ui.theme === 'light'} onClick={() => { ui.setTheme('light'); saveAppearance({ theme: 'light' }) }}><span className="theme-preview light"><span className="tp-side" /><span className="tp-top" /><span className="tp-card" /><span className="tp-line" /></span><span className="theme-choice-copy"><span><strong>高级白</strong><span>统一灰白 · 低对比边界</span></span><i className="theme-choice-check">✓</i></span></button>
                <button className={`theme-choice ${ui.theme === 'dark' ? 'active' : ''}`} role="radio" aria-checked={ui.theme === 'dark'} onClick={() => { ui.setTheme('dark'); saveAppearance({ theme: 'dark' }) }}><span className="theme-preview dark"><span className="tp-side" /><span className="tp-top" /><span className="tp-card" /><span className="tp-line" /></span><span className="theme-choice-copy"><span><strong>高级黑</strong><span>中性黑灰 · 层级一致</span></span><i className="theme-choice-check">✓</i></span></button>
              </div>
            </div>
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>界面字号</strong><span>文字与顶部栏关键控件同步缩放。</span></div>
            <div className="setting-row font-setting-row"><span className="setting-copy"><strong>全局界面比例</strong><span>小、中、大三级，默认使用“中”。</span></span><div className="font-choice-group" role="radiogroup" aria-label="界面字号">{(['small', 'medium', 'large'] as const).map((size) => <button key={size} className={ui.fontSize === size ? 'active' : ''} role="radio" aria-checked={ui.fontSize === size} onClick={() => { ui.setFontSize(size); saveAppearance({ font_size: size }) }}><b>{size === 'small' ? '小' : size === 'medium' ? '中' : '大'}</b><span>{size === 'small' ? '72%' : size === 'medium' ? '88%' : '105%'}</span></button>)}</div></div>
          </article>
        </> : null}

        {tab === 'provider' && userDraft ? <>
          <ConfigSaveBar label="保存模型与 Provider" description="保存后从下一次 Run 开始使用，不对运行中的请求热切换协议。" pending={saveMutation.isPending} saved={savedLabel === '保存模型与 Provider'} onSave={saveProvider} />
          <article className="setting-section">
            <div className="setting-section-head"><strong>核心连接</strong><span>Provider 类型只允许 chat 或 kemo；运行中不会跨协议自动回退。</span></div>
            <SettingRow title="Provider 类型" description={userDraft.provider.type === 'chat' ? '标准 /v1/chat/completions，保证文本、工具和图片输入基线' : '原生 Kemo Provider，支持网关声明的完整能力'} source="user" control={<ProviderSelect value={userDraft.provider.type} onChange={(value) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, type: value } })} />} />
            <SettingRow title="模型" description="主对话模型标识，可自由填写或修改" source="user" control={<input className="config-field" aria-label="模型" value={userDraft.provider.model} onChange={(event) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, model: event.target.value } })} />} />
            <SettingRow title="Base URL" description="chat 模式自动补全 /v1；kemo 模式使用协议根地址" source="user" control={<input className="config-field" aria-label="Base URL" value={userDraft.provider.base_url} onChange={(event) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, base_url: event.target.value } })} />} />
            <SettingRow title="API Key" description="已保存的密钥只显示脱敏占位；不修改就不会覆盖" source="user" control={<input className="config-field" type="password" autoComplete="new-password" aria-label="API Key" placeholder="未配置" value={userDraft.provider.api_key} onChange={(event) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, api_key: event.target.value } })} />} />
            <SettingRow title="流式输出" description="控制 Provider 原生流式；Web 消息通道仍使用 SSE" source="user" control={<Toggle checked={userDraft.provider.stream} label="流式输出" onChange={(value) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, stream: value } })} />} />
          </article>
          <details className="setting-section settings-disclosure" open>
            <summary><span><strong>子智能体模型</strong><small>按任务档位指定专用模型；留空时使用主对话模型。</small></span><ChevronDown size={16} /></summary>
            <div>{agentModelFields.map((field) => <SettingRow key={field.key} title={field.label} description={field.description} source="user" control={<input className="config-field" aria-label={field.label} value={userDraft.agent_models[field.key]} placeholder="继承主对话模型" onChange={(event) => setUserDraft({ ...userDraft, agent_models: { ...userDraft.agent_models, [field.key]: event.target.value } })} />} />)}</div>
          </details>
          <details className="setting-section settings-disclosure" open>
            <summary><span><strong>多模态模型</strong><small>为不同能力指定专用模型；留空表示不指定专用模型。</small></span><ChevronDown size={16} /></summary>
            <div>{multimodalFields.map((field) => <SettingRow key={field.key} title={field.label} description={field.description} source="user" control={<input className="config-field" aria-label={field.label} value={userDraft.multimodal_models[field.key]} placeholder="未指定" onChange={(event) => setUserDraft({ ...userDraft, multimodal_models: { ...userDraft.multimodal_models, [field.key]: event.target.value } })} />} />)}</div>
          </details>
        </> : null}

        {tab === 'users' ? <>
          <article className="setting-section current-user-section">
            <div className="setting-section-head"><strong>当前用户</strong><span>每个 Web 窗口只激活一个 users/&lt;user_id&gt; 目录。</span></div>
            <div className="current-user-card"><span className="user-card-avatar">{user.slice(0, 1).toUpperCase()}</span><span><strong>{user}</strong><small>users/{user} · 当前配置、历史、知识与记忆空间</small></span><StatusChip status="enabled">已载入</StatusChip></div>
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>可切换用户</strong><span>逐行选择目标用户；切换后会清空当前会话选择，并重新读取目标用户的运行配置。</span></div>
            {chatRunning && <div className="settings-user-lock" role="status"><LockKeyhole size={17} /><span><strong>对话运行中，暂不可切换</strong><small>请等待本轮结束，或先在对话页停止运行。</small></span></div>}
            <div className="settings-user-list" aria-label="可切换用户列表">
              {settingsQuery.data?.users.filter((name) => name !== user).map((name) => <button
                type="button"
                className="settings-user-row"
                aria-label={`切换到用户 ${name}`}
                disabled={chatRunning}
                key={name}
                onClick={() => switchUser(name)}
              >
                <span className="user-card-avatar">{name.slice(0, 1).toUpperCase()}</span>
                <span><strong>{name}</strong><small>users/{name} · 点击切换到此用户空间</small></span>
                <span className="settings-user-action">{chatRunning ? '已锁定' : '切换'}</span>
              </button>)}
              {!settingsQuery.data?.users.some((name) => name !== user) && <div className="settings-user-empty">当前没有其他可切换用户。</div>}
            </div>
          </article>
        </> : null}

        {tab === 'memory' && globalDraft ? <>
          <ConfigSaveBar label="保存记忆与上下文" description="这些参数是全局默认值，将影响所有未覆盖对应字段的用户。" pending={saveMutation.isPending} saved={savedLabel === '保存记忆与上下文'} onSave={saveMemory} />
          <article className="setting-section">
            <div className="setting-section-head"><strong>上下文窗口</strong><span>控制 Token 预算、轮次上限和压缩后保留量。</span></div>
            <SettingRow title="Token 上限" description="估算总量越过该值或 Provider 报超限时触发压缩" source="global" control={<NumberInput label="Token 上限" value={globalDraft.agents.token_limit} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, agents: { ...globalDraft.agents, token_limit: value } })} />} />
            <SettingRow title="Token 压缩比例" description={`输入预算为 Token 上限的 ${Math.round(globalDraft.agents.token_compression_ratio * 100)}%`} source="global" control={<div className="config-range"><input type="range" aria-label="Token 压缩比例" min="0.05" max="1" step="0.05" value={globalDraft.agents.token_compression_ratio} onChange={(event) => setGlobalDraft({ ...globalDraft, agents: { ...globalDraft.agents, token_compression_ratio: Number(event.target.value) } })} /><b>{Math.round(globalDraft.agents.token_compression_ratio * 100)}%</b></div>} />
            <SettingRow title="最大对话轮次" description="限制 Provider 临时工作区，不限制用户可见完整归档" source="global" control={<NumberInput label="最大对话轮次" value={globalDraft.agents.max_rounds} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, agents: { ...globalDraft.agents, max_rounds: value } })} />} />
            <SettingRow title="压缩后保留轮次" description="压缩时保留最新的完整轮次" source="global" control={<NumberInput label="压缩后保留轮次" value={globalDraft.agents.rounds_after_compression} max={globalDraft.agents.max_rounds} onChange={(value) => setGlobalDraft({ ...globalDraft, agents: { ...globalDraft.agents, rounds_after_compression: value } })} />} />
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>记忆注入上限</strong><span>按文件数量截断单次 Prompt，不限制磁盘中的记忆库存。</span></div>
            <SettingRow title="周记忆上限" description="seven_days 层单次最多注入的文件数量" source="global" control={<NumberInput label="周记忆上限" value={globalDraft.memory.temporary_injection_limits.seven_days} onChange={(value) => setGlobalDraft({ ...globalDraft, memory: { temporary_injection_limits: { ...globalDraft.memory.temporary_injection_limits, seven_days: value } } })} />} />
            <SettingRow title="月记忆上限" description="one_month 层单次最多注入的文件数量" source="global" control={<NumberInput label="月记忆上限" value={globalDraft.memory.temporary_injection_limits.one_month} onChange={(value) => setGlobalDraft({ ...globalDraft, memory: { temporary_injection_limits: { ...globalDraft.memory.temporary_injection_limits, one_month: value } } })} />} />
            <SettingRow title="半年记忆上限" description="half_year 层单次最多注入的文件数量" source="global" control={<NumberInput label="半年记忆上限" value={globalDraft.memory.temporary_injection_limits.half_year} onChange={(value) => setGlobalDraft({ ...globalDraft, memory: { temporary_injection_limits: { ...globalDraft.memory.temporary_injection_limits, half_year: value } } })} />} />
          </article>
        </> : null}

        {tab === 'permissions' && userDraft && globalDraft ? <>
          <ConfigSaveBar label="保存权限边界" description="用户白名单与全局图谱底层开关会分别写入各自配置文件。" pending={saveMutation.isPending} saved={savedLabel === '保存权限边界'} onSave={savePermissions} />
          <article className="setting-section">
            <div className="setting-section-head"><strong>知识库开关</strong><span>用户知识库始终有效；以下开关控制额外知识层。</span></div>
            <SettingRow title="使用共享知识库" description="将 shared_knowledge 加入当前用户知识范围" source="user" control={<Toggle checked={userDraft.knowledge.use_shared} label="使用共享知识库" onChange={(value) => setUserDraft({ ...userDraft, knowledge: { ...userDraft.knowledge, use_shared: value } })} />} />
            <SettingRow title="使用全局知识库" description="将 global_knowledge 加入当前用户知识范围" source="user" control={<Toggle checked={userDraft.knowledge.use_global} label="使用全局知识库" onChange={(value) => setUserDraft({ ...userDraft, knowledge: { ...userDraft.knowledge, use_global: value } })} />} />
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>知识图谱—用户级选择</strong><span>控制当前用户希望由图谱替换的知识和临时记忆来源。</span></div>
            {graphFields.map((field) => <SettingRow key={field.key} title={field.label} description="启用后不再回退注入对应的原始来源" source="user" control={<Toggle checked={userDraft.kemo_graph[field.key]} label={`${field.label}用户级`} onChange={(value) => setUserDraft({ ...userDraft, kemo_graph: { ...userDraft.kemo_graph, [field.key]: value } })} />} />)}
          </article>
          <article className="setting-section global-boundary-section">
            <div className="setting-section-head"><strong>知识图谱—全局底层</strong><span>决定用户目录是否实际生成图谱数据；关闭后，即使用户级开关打开也不生效。</span></div>
            {graphFields.map((field) => <SettingRow key={field.key} title={field.label} description="系统级图谱生成与连接闸门" source="global" control={<Toggle checked={globalDraft.kemo_graph[field.key]} label={`${field.label}全局底层`} onChange={(value) => setGlobalDraft({ ...globalDraft, kemo_graph: { ...globalDraft.kemo_graph, [field.key]: value } })} />} />)}
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>来源白名单</strong><span>输入资源 ID 后按 Enter 或逗号添加；空白名单表示全部允许。</span></div>
            <SettingRow title="共享技能白名单" description="shared_skills 中允许进入主智能体 Prompt 的技能" source="user" control={<TagInput label="共享技能白名单" value={userDraft.skills.shared_whitelist} onChange={(value) => setUserDraft({ ...userDraft, skills: { shared_whitelist: value } })} />} />
            <SettingRow title="共享拓展白名单" description="shared_expand 中允许加载的模块" source="user" control={<TagInput label="共享拓展白名单" value={userDraft.expand.shared_whitelist} onChange={(value) => setUserDraft({ ...userDraft, expand: { ...userDraft.expand, shared_whitelist: value } })} />} />
            <SettingRow title="全局拓展白名单" description="global_expand 中允许加载的模块" source="user" control={<TagInput label="全局拓展白名单" value={userDraft.expand.global_whitelist} onChange={(value) => setUserDraft({ ...userDraft, expand: { ...userDraft.expand, global_whitelist: value } })} />} />
            <SettingRow title="全局感知白名单" description="global_sense 中允许注入的模块" source="user" control={<TagInput label="全局感知白名单" value={userDraft.perception.global_whitelist} onChange={(value) => setUserDraft({ ...userDraft, perception: { global_whitelist: value } })} />} />
            <SettingRow title="插件白名单" description="控制 Provider 工具 Schema 与插件 Prompt 清单" source="user" control={<TagInput label="插件白名单" value={userDraft.plugins.whitelist} onChange={(value) => setUserDraft({ ...userDraft, plugins: { whitelist: value } })} />} />
          </article>
        </> : null}

        {tab === 'runtime' && userDraft && globalDraft ? <>
          <ConfigSaveBar label="保存运行限制" description="运行限制属于全局默认；任务计划自动接受为当前用户偏好。" pending={saveMutation.isPending} saved={savedLabel === '保存运行限制'} onSave={saveRuntime} />
          <article className="setting-section">
            <div className="setting-section-head"><strong>工具执行</strong><span>约束单轮工具循环的时间、次数与连续失败行为。</span></div>
            <SettingRow title="工具调用超时（秒）" description="单个工具执行的最长等待时间" source="global" control={<NumberInput label="工具调用超时" value={globalDraft.tools.timeout} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, tools: { ...globalDraft.tools, timeout: value } })} />} />
            <SettingRow title="每轮最大工具调用" description="单轮 Provider 工具循环的最大迭代次数" source="global" control={<NumberInput label="每轮最大工具调用" value={globalDraft.tools.max_iterations} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, tools: { ...globalDraft.tools, max_iterations: value } })} />} />
            <SettingRow title="连续工具失败上限" description="达到上限后，本轮临时移除该工具" source="global" control={<NumberInput label="连续工具失败上限" value={globalDraft.history.consecutive_tool_fail_limit} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, history: { consecutive_tool_fail_limit: value } })} />} />
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>任务计划</strong><span>控制计划是否需要批准，以及单个计划的最大步骤数。</span></div>
            <SettingRow title="自动接受任务计划" description="开启后跳过人工批准；只影响当前用户" source="user" control={<Toggle checked={userDraft.task_plan.auto_accept} label="自动接受任务计划" onChange={(value) => setUserDraft({ ...userDraft, task_plan: { auto_accept: value } })} />} />
            <SettingRow title="任务计划最大步骤" description="单个计划允许生成的最大步骤数" source="global" control={<NumberInput label="任务计划最大步骤" value={globalDraft.task_plan.max_steps} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, task_plan: { max_steps: value } })} />} />
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>调度与超时</strong><span>控制 Cron 扫描频率和子代理默认执行期限。</span></div>
            <SettingRow title="Cron 轮询间隔（秒）" description="统一后台调度器检查到期任务的频率" source="global" control={<NumberInput label="Cron 轮询间隔" value={globalDraft.cron.poll_interval} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, cron: { ...globalDraft.cron, poll_interval: value } })} />} />
            <SettingRow title="代理默认超时（秒）" description="未单独声明超时时，子代理使用的默认期限" source="global" control={<NumberInput label="代理默认超时" value={globalDraft.agent_runtime.default_timeout} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, agent_runtime: { ...globalDraft.agent_runtime, default_timeout: value } })} />} />
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>Provider 并发控制</strong><span>所有来源共享 Provider 总闸，工具执行期间不会占用槽位。</span></div>
            <SettingRow title="最大并发请求数" description="同时访问 LLM API 的请求上限；超出后等待空闲槽位" source="global" control={<NumberInput label="最大并发请求数" value={globalDraft.provider_runtime.max_concurrent_requests} min={1} max={50} onChange={(value) => setGlobalDraft({ ...globalDraft, provider_runtime: { ...globalDraft.provider_runtime, max_concurrent_requests: value } })} />} />
            <SettingRow title="信号量等待超时（秒）" description="等待 Provider 空闲的最长时间；超时后明确返回拥塞错误" source="global" control={<NumberInput label="信号量等待超时" value={globalDraft.provider_runtime.request_semaphore_timeout} min={1} max={600} onChange={(value) => setGlobalDraft({ ...globalDraft, provider_runtime: { ...globalDraft.provider_runtime, request_semaphore_timeout: value } })} />} />
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>Web 与消息并发</strong><span>限制单用户跨会话聊天数量和外部消息积压深度。</span></div>
            <SettingRow title="单用户最大并发聊天" description="同一用户跨 session 的并发上限；超出后进入等待区" source="global" control={<NumberInput label="单用户最大并发聊天" value={globalDraft.web.max_concurrent_chats} min={1} max={20} onChange={(value) => setGlobalDraft({ ...globalDraft, web: { ...globalDraft.web, max_concurrent_chats: value } })} />} />
            <SettingRow title="Web 排队槽位上限" description="并发已满后允许等待的请求数；再超出返回 503" source="global" control={<NumberInput label="Web 排队槽位上限" value={globalDraft.web.max_pending_chats} min={0} max={50} onChange={(value) => setGlobalDraft({ ...globalDraft, web: { ...globalDraft.web, max_pending_chats: value } })} />} />
            <SettingRow title="Web 排队超时（秒）" description="排队等待的最长时间；超时返回 503 与 Retry-After" source="global" control={<NumberInput label="Web 排队超时" value={globalDraft.web.pending_chat_timeout} min={1} max={120} onChange={(value) => setGlobalDraft({ ...globalDraft, web: { ...globalDraft.web, pending_chat_timeout: value } })} />} />
            <SettingRow title="消息路由队列上限" description="外部消息工作线程之外允许积压的数量；0 表示无界" source="global" control={<NumberInput label="消息路由队列上限" value={globalDraft.message.max_queued_messages} min={0} max={200} onChange={(value) => setGlobalDraft({ ...globalDraft, message: { ...globalDraft.message, max_queued_messages: value } })} />} />
          </article>
          <article className="setting-section">
            <div className="setting-section-head"><strong>子代理队列与 Cron 退避</strong><span>隔离用户级后台代理，并在 Provider 高负载时推迟重型定时任务。</span></div>
            <SettingRow title="子代理队列上限" description="单用户 background_serial 子代理的最大等待数；0 表示无界" source="global" control={<NumberInput label="子代理队列上限" value={globalDraft.agent_runtime.queue_maxsize} min={0} max={200} onChange={(value) => setGlobalDraft({ ...globalDraft, agent_runtime: { ...globalDraft.agent_runtime, queue_maxsize: value } })} />} />
            <SettingRow title="Cron 自动退避" description="Provider 繁忙时跳过本轮重型 Cron；感知和拓展采集仍执行" source="global" control={<Toggle checked={globalDraft.cron.avoid_congestion} label="Cron 自动退避" onChange={(value) => setGlobalDraft({ ...globalDraft, cron: { ...globalDraft.cron, avoid_congestion: value } })} />} />
            <SettingRow title="退避触发阈值" description={`Provider 可用槽位低于 ${Math.round(globalDraft.cron.congestion_threshold_ratio * 100)}% 时触发`} source="global" control={<div className="config-range"><input type="range" aria-label="退避触发阈值" min="0.05" max="1" step="0.05" value={globalDraft.cron.congestion_threshold_ratio} onChange={(event) => setGlobalDraft({ ...globalDraft, cron: { ...globalDraft.cron, congestion_threshold_ratio: Number(event.target.value) } })} /><b>{Math.round(globalDraft.cron.congestion_threshold_ratio * 100)}%</b></div>} />
          </article>
          <article className={`settings-restart-card ${restartState === 'waiting' || restartState === 'restarting' ? 'is-restarting' : ''}`}>
            <span className="settings-restart-icon"><Power size={20} /></span>
            <span className="settings-restart-copy">
              <strong>重启智能体</strong>
              <small>{chatRunning ? '当前对话正在运行，请结束或停止后再重启。' : '关闭当前 Web 与 RuntimeHost，并使用当前网页端口重新启动。'}</small>
            </span>
            <button
              type="button"
              className="settings-restart-button"
              disabled={chatRunning || restartState === 'restarting' || restartState === 'waiting'}
              onClick={() => { setRestartMessage(''); setRestartState('confirming') }}
            ><Power size={15} />{restartState === 'failed' ? '重新尝试' : '重启智能体'}</button>
            {restartMessage ? <div className={`settings-restart-status ${restartState === 'failed' ? 'error' : ''}`} role="status">{restartState === 'restarting' || restartState === 'waiting' ? <RefreshCw className="spin" size={14} /> : null}{restartMessage}</div> : null}
          </article>
          {restartState === 'confirming' ? createPortal(<div className="settings-restart-confirm-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) setRestartState('idle') }}>
            <section className="settings-restart-confirm" role="alertdialog" aria-modal="true" aria-label="确认重启智能体">
              <span className="settings-restart-confirm-icon"><AlertTriangle size={23} /></span>
              <span className="settings-restart-confirm-copy">
                <strong>您确定要重启吗？</strong>
                <small>智能体在执行任务时重启可能会出现故障。请先确认当前任务已经结束，并保存尚未提交的配置。</small>
                <small>重启期间网页会短暂断开，服务恢复后将自动刷新。</small>
              </span>
              <span className="settings-restart-confirm-actions"><button type="button" onClick={() => setRestartState('idle')}>取消</button><button type="button" className="confirm" onClick={() => void confirmRestart()}>确认重启</button></span>
            </section>
          </div>, document.body) : null}
        </> : null}

        {(userConfigQuery.isLoading || globalConfigQuery.isLoading) && tab !== 'appearance' && tab !== 'users' ? <div className="settings-loading">正在读取结构化配置…</div> : null}
      </div>
    </div>
  </ModuleFrame>
}
