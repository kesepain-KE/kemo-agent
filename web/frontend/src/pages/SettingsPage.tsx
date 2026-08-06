import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Check, ChevronDown, Cloud, Copy, LockKeyhole, Power, RefreshCw, Save } from 'lucide-react'
import { useOutletContext, useSearchParams } from 'react-router-dom'
import {
  ApiError,
  getGlobalConfig,
  getKemoModelCapabilities,
  getKemoProviderModels,
  getSettings,
  getUserConfig,
  getVersion,
  getVersionCheck,
  patchGlobalConfig,
  patchPreferences,
  patchUserConfig,
  restartSystem,
} from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import type { KemoModelCapabilitiesResponse, KemoModelCatalogItem } from '../types/api'
import { ReasoningEffortSelect } from '../components/ReasoningEffortSelect'
import {
  normalizeKemoReasoningEffort,
  normalizeReasoningEffort,
  reasoningEffortOptionsFor,
  selectReasoningEffort,
  type ReasoningEffort,
} from '../reasoningEffort'
import { ModuleError, ModuleFrame, RefreshActionButton, StatusChip } from '../components/ModuleUi'
import { useUiStore } from '../store/ui'
import { copyText } from '../utils/clipboard'

type SettingsTab = 'appearance' | 'provider' | 'users' | 'memory' | 'permissions' | 'runtime' | 'version'
type ProviderType = 'chat' | 'kemo'
type VisionRoutingMode = 'auto' | 'main' | 'dedicated'
type MultimodalKey = 'vision' | 'image_generation' | 'image_edit' | 'audio_transcription' | 'speech_generation' | 'speech_to_speech' | 'video_understanding' | 'video_generation'
type AgentModelProfile = 'default' | 'cheap' | 'reasoning'
type RestartState = 'idle' | 'confirming' | 'confirming-force' | 'restarting' | 'waiting' | 'failed'

interface UserConfigDraft {
  provider: { type: ProviderType; model: string; base_url: string; api_key: string; stream: boolean; reasoning_effort: ReasoningEffort; supports_image_input: boolean; supports_audio_input: boolean; supports_video_input: boolean; supports_file_input: boolean }
  agent_models: Record<AgentModelProfile, string>
  multimodal_models: Record<MultimodalKey, string>
  multimodal_routing: { vision: VisionRoutingMode }
  knowledge: { use_shared: boolean; use_global: boolean }
  skills: { shared_whitelist: string[] }
  expand: { shared_whitelist: string[]; global_whitelist: string[] }
  perception: { global_whitelist: string[] }
  plugins: { whitelist: string[] }
  task_plan: { auto_accept: boolean }
}

interface GlobalConfigDraft {
  agents: { token_limit: number; token_compression_ratio: number; max_rounds: number; rounds_after_compression: number }
  memory: { temporary_injection_limits: { seven_days: number; one_month: number; half_year: number } }
  tools: { timeout: number; max_iterations: number; consecutive_identical_call_limit: number }
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
  providerDiscovery?: ProviderType
}

interface SaveResult {
  providerModels?: KemoModelCatalogItem[]
  providerDiscoveryError?: string
}

const settingsTabs: Array<{ id: SettingsTab; label: string }> = [
  { id: 'appearance', label: '外观与主题' },
  { id: 'provider', label: '模型与 Provider' },
  { id: 'users', label: '用户切换' },
  { id: 'memory', label: '记忆与上下文' },
  { id: 'permissions', label: '权限边界' },
  { id: 'runtime', label: '运行限制' },
  { id: 'version', label: '版本查看' },
]

const settingsTabIds = new Set<SettingsTab>(settingsTabs.map((item) => item.id))

const multimodalFields: Array<{ key: MultimodalKey; label: string; description: string }> = [
  { key: 'vision', label: '图片识别', description: '图片分析、OCR 与视觉理解模型' },
  { key: 'image_generation', label: '图片生成', description: '文本生成图片的专用模型' },
  { key: 'image_edit', label: '图片编辑', description: '图片修改、局部重绘与图生图模型' },
  { key: 'audio_transcription', label: '语音识别', description: '音频转写与语音转文字模型' },
  { key: 'speech_generation', label: '语音生成', description: '文本转语音模型' },
  { key: 'speech_to_speech', label: '语音生语音', description: '语音到语音的转换模型' },
  { key: 'video_understanding', label: '视频理解', description: '视频分析、时间轴摘要与内容理解模型' },
  { key: 'video_generation', label: '视频生成', description: '文本或素材生成视频的模型' },
]

const agentModelFields: Array<{ key: AgentModelProfile; label: string; description: string }> = [
  { key: 'default', label: '默认子智能体模型', description: '普通子智能体使用；留空时继承主对话模型' },
  { key: 'cheap', label: '轻量子智能体模型', description: '摘要、上下文整理等轻量任务使用；留空时继承主对话模型' },
  { key: 'reasoning', label: '推理子智能体模型', description: '任务规划和深度整理使用；留空时继承主对话模型' },
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

function versionLabel(value: string) {
  const normalized = String(value || '').trim().replace(/^v/i, '')
  return normalized ? `v${normalized}` : '未声明'
}

function versionCheckTime(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value || '未知'
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

function reasoningPolicyDescription(response: KemoModelCapabilitiesResponse | undefined) {
  if (!response) return ''
  const policy = response.capabilities.extensions.reasoning_policy
  const mode = policy?.mode
  const details = mode === 'native'
    ? '网关声明为厂商原生档位'
    : mode === 'mapped'
      ? 'Kemo 逻辑档位由网关映射到厂商档位'
      : mode === 'provider_default'
        ? '网关将使用厂商默认推理策略'
        : '档位由 Kemo 网关能力声明提供'
  return policy?.collapsed
    ? `${details}；部分档位会映射到相同的上游强度`
    : details
}

function stringList(value: unknown) {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function buildUserDraft(config: Record<string, unknown>): UserConfigDraft {
  const provider = record(config.provider)
  const providerType: ProviderType = provider.type === 'kemo' ? 'kemo' : 'chat'
  const agentModels = record(config.agent_models)
  const multimodal = record(config.multimodal_models)
  const multimodalRouting = record(config.multimodal_routing)
  const knowledge = record(config.knowledge)
  const skills = record(config.skills)
  const expand = record(config.expand)
  const perception = record(config.perception)
  const plugins = record(config.plugins)
  const taskPlan = record(config.task_plan)
  return {
    provider: {
      type: providerType,
      model: stringValue(provider.model),
      base_url: stringValue(provider.base_url),
      api_key: stringValue(provider.api_key),
      stream: booleanValue(provider.stream, true),
      reasoning_effort: providerType === 'kemo'
        ? normalizeKemoReasoningEffort(provider.reasoning_effort)
        : normalizeReasoningEffort(provider.reasoning_effort),
      supports_image_input: stringList(provider.input_modalities).includes('image'),
      supports_audio_input: stringList(provider.input_modalities).includes('audio'),
      supports_video_input: stringList(provider.input_modalities).includes('video'),
      supports_file_input: stringList(provider.input_modalities).includes('file'),
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
      video_understanding: stringValue(multimodal.video_understanding),
      video_generation: stringValue(multimodal.video_generation),
    },
    multimodal_routing: {
      vision: ['main', 'dedicated'].includes(stringValue(multimodalRouting.vision))
        ? stringValue(multimodalRouting.vision) as VisionRoutingMode
        : 'auto',
    },
    knowledge: {
      use_shared: booleanValue(knowledge.use_shared, true),
      use_global: booleanValue(knowledge.use_global, true),
    },
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
    tools: {
      timeout: numberValue(tools.timeout, 240),
      max_iterations: numberValue(tools.max_iterations, 80),
      consecutive_identical_call_limit: numberValue(tools.consecutive_identical_call_limit, 8),
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

const visionRoutingOptions: Array<{ value: VisionRoutingMode; label: string; description: string }> = [
  { value: 'auto', label: '自动 — 主模型优先', description: '主模型支持图片时直接使用，否则切换专用视觉模型' },
  { value: 'main', label: '仅主模型', description: '始终交给主模型处理；需要确认主模型支持图片输入' },
  { value: 'dedicated', label: '仅专用视觉模型', description: '始终使用“图片识别”中配置的专用视觉模型' },
]

function VisionRoutingSelect({ value, onChange }: { value: VisionRoutingMode; onChange: (value: VisionRoutingMode) => void }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()
  const selected = visionRoutingOptions.find((option) => option.value === value) ?? visionRoutingOptions[0]

  useEffect(() => {
    if (!open) return
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
  }, [open])

  return <div className={`config-select-wrap vision-routing-select ${open ? 'open' : ''}`} ref={rootRef}>
    <button
      type="button"
      className={`config-select-trigger ${open ? 'open' : ''}`}
      role="combobox"
      aria-label="图片路由"
      aria-controls={listboxId}
      aria-expanded={open}
      aria-haspopup="listbox"
      onClick={() => setOpen((current) => !current)}
      onKeyDown={(event) => {
        if (!open && ['ArrowDown', 'ArrowUp'].includes(event.key)) {
          event.preventDefault()
          setOpen(true)
        }
      }}
    >
      <span><strong>{selected.label}</strong><small>{selected.description}</small></span><ChevronDown size={16} />
    </button>
    {open ? <div className="config-select-popover vision-routing-popover" id={listboxId} role="listbox" aria-label="图片路由选项">
      {visionRoutingOptions.map((option) => <button
        type="button"
        role="option"
        aria-selected={value === option.value}
        className={value === option.value ? 'active' : ''}
        key={option.value}
        onClick={() => {
          onChange(option.value)
          setOpen(false)
        }}
      >
        <span><strong>{option.label}</strong><small>{option.description}</small></span>
        {value === option.value ? <Check size={16} /> : <i aria-hidden="true" />}
      </button>)}
    </div> : null}
  </div>
}

function ModelSelectField({
  label,
  value,
  placeholder,
  models,
  enabled,
  onChange,
}: {
  label: string
  value: string
  placeholder: string
  models: KemoModelCatalogItem[]
  enabled: boolean
  onChange: (value: string) => void
}) {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const rootRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()
  const pickerLabel = label === '模型' ? '主模型' : label
  const availableModels = models.filter((item) => item.task === 'llm' || item.task === 'unknown')
  const normalizedQuery = query.trim().toLowerCase()
  const filteredModels = availableModels.filter((item) => {
    if (!normalizedQuery) return true
    return `${item.id} ${item.provider_id} ${item.provider_model}`.toLowerCase().includes(normalizedQuery)
  })

  useEffect(() => {
    if (!open) return
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
  }, [open])

  useEffect(() => {
    if (!enabled) setOpen(false)
  }, [enabled])

  if (!enabled) {
    return <input className="config-field" aria-label={label} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
  }

  return <div className={`model-select-wrap ${open ? 'open' : ''}`} ref={rootRef}>
    <div className="model-field-shell">
      <input
        className="config-field model-field-input"
        aria-label={label}
        value={value}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'ArrowDown' || event.key === 'Enter') {
            event.preventDefault()
            setOpen(true)
          }
          if (event.key === 'Escape') setOpen(false)
        }}
      />
      <button
        type="button"
        className={`model-picker-trigger ${open ? 'open' : ''}`}
        aria-label={`${pickerLabel}选择模型`}
        aria-controls={listboxId}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => { setQuery(''); setOpen((current) => !current) }}
      >
        <span>选择模型</span><em>可选择</em><ChevronDown size={14} aria-hidden="true" />
      </button>
    </div>
    {open ? <div className="model-select-popover config-select-popover" id={listboxId} role="listbox" aria-label={`${pickerLabel}选项`}>
      <div className="model-select-search">
        <input aria-label={`${pickerLabel}筛选`} value={query} placeholder="筛选模型…" onChange={(event) => setQuery(event.target.value)} autoFocus />
      </div>
      <div className="model-select-options">
        {filteredModels.length ? filteredModels.map((item) => <button
          type="button"
          role="option"
          aria-selected={value === item.id}
          className={value === item.id ? 'active' : ''}
          key={`${item.provider_id}:${item.id}`}
          onClick={() => { onChange(item.id); setOpen(false); setQuery('') }}
        >
          <span><strong>{item.id}</strong><small>{item.provider_id} · {item.provider_model}</small></span>
          {value === item.id ? <Check size={15} /> : <i aria-hidden="true" />}
        </button>) : <div className="model-select-empty">没有匹配的模型；仍可直接输入模型名。</div>}
      </div>
    </div> : null}
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
  const storedProviderType = record(userConfigQuery.data?.config.provider).type === 'kemo' ? 'kemo' : 'chat'
  const providerModelsQuery = useQuery({
    queryKey: ['provider-models', user],
    queryFn: async () => (await getKemoProviderModels(user)).data,
    enabled: Boolean(user) && tab === 'provider' && storedProviderType === 'kemo',
    staleTime: 300_000,
    retry: false,
  })
  const versionQuery = useQuery({ queryKey: ['version'], queryFn: getVersion })
  const versionCheckQuery = useQuery({
    queryKey: ['version-check'],
    queryFn: () => getVersionCheck(),
    enabled: tab === 'version',
    staleTime: 180_000,
  })
  const [userDraft, setUserDraft] = useState<UserConfigDraft | null>(null)
  const [globalDraft, setGlobalDraft] = useState<GlobalConfigDraft | null>(null)
  const [initialApiKey, setInitialApiKey] = useState('')
  const [providerModels, setProviderModels] = useState<KemoModelCatalogItem[]>([])
  const [providerDiscovery, setProviderDiscovery] = useState<{ status: 'idle' | 'valid' | 'failed'; message: string }>({ status: 'idle', message: '' })
  const [formError, setFormError] = useState('')
  const [savedLabel, setSavedLabel] = useState('')
  const [restartState, setRestartState] = useState<RestartState>('idle')
  const [restartMessage, setRestartMessage] = useState('')
  const [restartCanForce, setRestartCanForce] = useState(false)
  const [versionCopyState, setVersionCopyState] = useState<'idle' | 'copied' | 'failed'>('idle')
  const restartTimerRef = useRef<number | null>(null)
  const selectedProviderModel = userDraft?.provider.model.trim() || ''
  const selectedCatalogModel = providerModels.find((item) => item.id === selectedProviderModel)
  const providerCapabilitiesEnabled = Boolean(
    user
      && tab === 'provider'
      && storedProviderType === 'kemo'
      && userDraft?.provider.type === 'kemo'
      && providerDiscovery.status === 'valid'
      && selectedProviderModel,
  )
  const providerCapabilitiesQuery = useQuery({
    queryKey: ['provider-model-capabilities', user, selectedProviderModel],
    queryFn: () => getKemoModelCapabilities(user, selectedProviderModel),
    enabled: providerCapabilitiesEnabled,
    staleTime: 300_000,
    retry: false,
  })

  const versionCheckMutation = useMutation({
    mutationFn: () => getVersionCheck(true),
    onSuccess: (data) => client.setQueryData(['version-check'], data),
  })

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
    setProviderModels([])
    setProviderDiscovery({ status: 'idle', message: '' })
  }, [user])

  useEffect(() => {
    if (storedProviderType !== 'kemo') {
      setProviderModels([])
      setProviderDiscovery({ status: 'idle', message: '' })
      return
    }
    if (providerModelsQuery.isError) {
      setProviderModels([])
      setProviderDiscovery({
        status: 'failed',
        message: providerModelsQuery.error instanceof Error
          ? providerModelsQuery.error.message
          : 'Kemo API 验证失败，未拉取模型',
      })
      return
    }
    if (!providerModelsQuery.data) return
    setProviderModels(providerModelsQuery.data)
    setProviderDiscovery({
      status: 'valid',
      message: providerModelsQuery.data.length
        ? `Kemo API 已验证，已获取 ${providerModelsQuery.data.length} 个可用模型`
        : 'Kemo API 已验证，但当前密钥没有可用的 LLM 模型',
    })
  }, [providerModelsQuery.data, providerModelsQuery.error, providerModelsQuery.isError, storedProviderType])

  useEffect(() => {
    if (globalConfigQuery.data) setGlobalDraft(buildGlobalDraft(globalConfigQuery.data.config))
  }, [globalConfigQuery.data])

  useEffect(() => {
    const capabilities = providerCapabilitiesQuery.data?.capabilities
    if (!capabilities?.reasoning.supported || !capabilities.reasoning.efforts.length) return
    const fallback = selectReasoningEffort(
      userDraft?.provider.reasoning_effort,
      capabilities.reasoning.efforts,
    )
    if (!fallback || fallback === userDraft?.provider.reasoning_effort) return
    setUserDraft((current) => {
      if (
        !current
        || current.provider.type !== 'kemo'
        || current.provider.model.trim() !== capabilities.model
      ) return current
      return {
        ...current,
        provider: { ...current.provider, reasoning_effort: fallback },
      }
    })
  }, [providerCapabilitiesQuery.data, userDraft?.provider.reasoning_effort])

  useEffect(() => {
    if (isSettingsTab(requestedTab)) setTab(requestedTab)
  }, [requestedTab])

  const saveMutation = useMutation({
    mutationFn: async ({ userChanges, globalChanges, providerDiscovery }: SaveRequest): Promise<SaveResult> => {
      await Promise.all([
        userChanges ? patchUserConfig(user, userChanges) : Promise.resolve(),
        globalChanges ? patchGlobalConfig(globalChanges) : Promise.resolve(),
      ])
      if (providerDiscovery !== 'kemo') return {}
      try {
        const catalog = await getKemoProviderModels(user, true)
        return { providerModels: catalog.data }
      } catch (error) {
        return {
          providerDiscoveryError: error instanceof Error
            ? error.message
            : 'Kemo API 验证失败，未拉取模型',
        }
      }
    },
    onSuccess: async (result, request) => {
      setFormError('')
      setSavedLabel(request.label)
      if (request.providerDiscovery === 'chat') {
        client.removeQueries({ queryKey: ['provider-models', user] })
        client.removeQueries({ queryKey: ['provider-model-capabilities', user] })
        setProviderModels([])
        setProviderDiscovery({ status: 'idle', message: '' })
      } else if (request.providerDiscovery === 'kemo') {
        client.removeQueries({ queryKey: ['provider-model-capabilities', user] })
        if (result.providerModels) {
          client.setQueryData(['provider-models', user], result.providerModels)
          setProviderModels(result.providerModels)
          setProviderDiscovery({
            status: 'valid',
            message: result.providerModels.length
              ? `Kemo API 已验证，已获取 ${result.providerModels.length} 个可用模型`
              : 'Kemo API 已验证，但当前密钥没有可用的 LLM 模型',
          })
        } else {
          setProviderModels([])
          setProviderDiscovery({
            status: 'failed',
            message: result.providerDiscoveryError || 'Kemo API 验证失败，未拉取模型',
          })
        }
      }
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
    void Promise.all([
      settingsQuery.refetch(),
      userConfigQuery.refetch(),
      globalConfigQuery.refetch(),
      versionQuery.refetch(),
      ...(storedProviderType === 'kemo' ? [providerModelsQuery.refetch()] : []),
    ])
  }
  const settingsRefreshing = settingsQuery.isFetching || userConfigQuery.isFetching || globalConfigQuery.isFetching || versionQuery.isFetching

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
    const inputModalities = ['text']
    if (userDraft.provider.supports_image_input) inputModalities.push('image')
    if (userDraft.provider.type === 'kemo') {
      if (userDraft.provider.supports_audio_input) inputModalities.push('audio')
      if (userDraft.provider.supports_video_input) inputModalities.push('video')
      if (userDraft.provider.supports_file_input) inputModalities.push('file')
    }
    const provider: Record<string, unknown> = {
      type: userDraft.provider.type,
      model: userDraft.provider.model.trim(),
      base_url: userDraft.provider.base_url.trim(),
      stream: userDraft.provider.stream,
      reasoning_effort: userDraft.provider.reasoning_effort,
      input_modalities: inputModalities,
    }
    if (userDraft.provider.api_key !== initialApiKey) provider.api_key = userDraft.provider.api_key
    submit({
      label: '保存模型与 Provider',
      providerDiscovery: userDraft.provider.type,
      userChanges: {
        provider,
        agent_models: userDraft.agent_models,
        multimodal_models: userDraft.multimodal_models,
        multimodal_routing: userDraft.multimodal_routing,
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
    if (!userDraft) return
    submit({
      label: '保存权限边界',
      userChanges: {
        knowledge: userDraft.knowledge,
        skills: userDraft.skills,
        expand: userDraft.expand,
        perception: userDraft.perception,
        plugins: userDraft.plugins,
      },
    })
  }

  const saveRuntime = () => {
    if (!userDraft || !globalDraft) return
    const positiveIntegers = [globalDraft.tools.timeout, globalDraft.tools.max_iterations, globalDraft.tools.consecutive_identical_call_limit, globalDraft.history.consecutive_tool_fail_limit, globalDraft.task_plan.max_steps, globalDraft.cron.poll_interval, globalDraft.agent_runtime.default_timeout, globalDraft.provider_runtime.max_concurrent_requests, globalDraft.provider_runtime.request_semaphore_timeout, globalDraft.web.max_concurrent_chats, globalDraft.web.pending_chat_timeout]
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

  const confirmRestart = async (force = false) => {
    if ((!force && chatRunning) || restartState === 'restarting' || restartState === 'waiting') return
    const port = Number.parseInt(window.location.port || '80', 10)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      setRestartState('failed')
      setRestartMessage('无法从当前网页地址识别有效端口。')
      setRestartCanForce(false)
      return
    }
    setRestartState('restarting')
    setRestartCanForce(false)
    setRestartMessage(`正在请求${force ? '强制' : ''}重启智能体并继续使用端口 ${port}…`)
    try {
      await restartSystem(port, force)
      setRestartMessage(`${force ? '强制重启' : '重启'}请求已提交，正在等待端口 ${port} 恢复…`)
    } catch (error) {
      if (error instanceof ApiError) {
        setRestartState('failed')
        setRestartMessage(error.message)
        setRestartCanForce(!force && error.status === 409 && error.code === 'conflict')
        return
      }
      setRestartMessage('服务连接已中断，正在等待新实例接管当前端口…')
    }
    setRestartState('waiting')
    restartTimerRef.current = window.setTimeout(() => window.location.reload(), 4000)
  }
  const closeRestartConfirmation = () => {
    setRestartState((current) => current === 'confirming-force' ? 'failed' : 'idle')
  }
  const versionCheck = versionCheckQuery.data
  const versionChecking = versionCheckQuery.isFetching || versionCheckMutation.isPending
  const checkVersionNow = () => {
    setVersionCopyState('idle')
    versionCheckMutation.mutate()
  }
  const copyVersionUpdateCommand = async () => {
    const command = versionCheck?.commands?.recommended
    if (!command) return
    try {
      await copyText(command)
      setVersionCopyState('copied')
    } catch {
      setVersionCopyState('failed')
    }
  }
  const modelPickerEnabled = Boolean(
    userDraft?.provider.type === 'kemo'
      && providerDiscovery.status === 'valid'
      && providerModels.length > 0,
  )
  const kemoReasoning = providerCapabilitiesQuery.data?.capabilities.reasoning
  const kemoReasoningOptions = kemoReasoning?.supported
    ? reasoningEffortOptionsFor(kemoReasoning.efforts)
    : []
  const kemoReasoningAvailable = kemoReasoningOptions.length > 0
  const kemoReasoningDescription = providerCapabilitiesQuery.data
    ? kemoReasoningAvailable
      ? `${reasoningPolicyDescription(providerCapabilitiesQuery.data)}${providerCapabilitiesQuery.data.warning ? `；${providerCapabilitiesQuery.data.warning}` : ''}`
      : '当前模型声明不支持推理；运行时不会向 Kemo 网关提交 reasoning 参数'
    : providerCapabilitiesQuery.isPending && providerCapabilitiesEnabled
      ? '正在读取当前模型的 Kemo 思考能力声明'
      : providerCapabilitiesQuery.isError
        ? '能力信息读取失败；为避免提交无效参数，运行时不会假定模型支持固定五档'
        : userDraft?.provider.type === 'kemo'
          ? selectedCatalogModel
            ? '网关未为当前模型提供可用的思考能力声明'
            : '请先选择当前密钥可用的 Kemo 模型；不会套用固定五档'
          : '控制主对话和子智能体的推理深度；不可关闭，缺省使用中度'

  return <ModuleFrame
    kicker="Configuration Overview"
    title="配置"
    description="通过结构化字段管理界面、Provider、上下文、权限和运行限制；敏感凭据始终脱敏。"
    actions={<RefreshActionButton pending={settingsRefreshing} label="重新读取" pendingLabel="读取中…" onClick={refreshAll} />}
  >
    {settingsQuery.isError || userConfigQuery.isError || globalConfigQuery.isError ? <ModuleError message="配置读取失败，请检查配置文件格式或 Web API。" /> : null}
    {tab === 'version' && versionQuery.isError ? <ModuleError message="版本信息读取失败，请检查 version.json。" /> : null}
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
            <SettingRow title="Provider 类型" description={userDraft.provider.type === 'chat' ? '标准 /v1/chat/completions，保证文本、工具和图片输入基线' : '原生 Kemo Provider，支持网关声明的完整能力'} source="user" control={<ProviderSelect value={userDraft.provider.type} onChange={(value) => {
              if (value !== 'kemo') {
                setProviderModels([])
                setProviderDiscovery({ status: 'idle', message: '' })
              }
              setUserDraft({
                ...userDraft,
                provider: {
                  ...userDraft.provider,
                  type: value,
                  reasoning_effort: value === 'kemo'
                    ? normalizeKemoReasoningEffort(userDraft.provider.reasoning_effort)
                    : normalizeReasoningEffort(userDraft.provider.reasoning_effort),
                },
              })
            }} />} />
            <SettingRow title="模型" description={modelPickerEnabled ? '可直接输入模型名，或从已验证 Kemo 模型目录中选择' : '主对话模型标识，可自由填写或修改'} source="user" control={<ModelSelectField
              label="模型"
              value={userDraft.provider.model}
              placeholder="输入模型名称"
              models={providerModels}
              enabled={modelPickerEnabled}
              onChange={(model) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, model } })}
            />} />
            {userDraft.provider.type === 'kemo' && providerDiscovery.status !== 'idle' ? <SettingRow title="Kemo 模型目录" description={providerDiscovery.message} control={<StatusChip status={providerDiscovery.status === 'valid' ? 'configured' : 'error'}>{providerDiscovery.status === 'valid' ? 'API 有效' : '未拉取'}</StatusChip>} /> : null}
            <SettingRow title="Base URL" description="chat 模式自动补全 /v1；kemo 模式使用协议根地址" source="user" control={<input className="config-field" aria-label="Base URL" value={userDraft.provider.base_url} onChange={(event) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, base_url: event.target.value } })} />} />
            <SettingRow title="API Key" description="已保存的密钥只显示脱敏占位；不修改就不会覆盖" source="user" control={<input className="config-field" type="password" autoComplete="new-password" aria-label="API Key" placeholder="未配置" value={userDraft.provider.api_key} onChange={(event) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, api_key: event.target.value } })} />} />
            <SettingRow title="流式输出" description="控制 Provider 原生流式；Web 消息通道仍使用 SSE" source="user" control={<Toggle checked={userDraft.provider.stream} label="流式输出" onChange={(value) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, stream: value } })} />} />
            <SettingRow
              title="思考强度"
              description={kemoReasoningDescription}
              source="user"
              control={userDraft.provider.type === 'chat'
                ? <ReasoningEffortSelect ariaLabel="思考强度" value={normalizeReasoningEffort(userDraft.provider.reasoning_effort)} onChange={(reasoningEffort) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, reasoning_effort: reasoningEffort } })} />
                : kemoReasoningAvailable
                  ? <ReasoningEffortSelect ariaLabel="思考强度" value={userDraft.provider.reasoning_effort} options={kemoReasoningOptions} onChange={(reasoningEffort) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, reasoning_effort: reasoningEffort } })} />
                  : <ReasoningEffortSelect
                    ariaLabel="思考强度"
                    value={userDraft.provider.reasoning_effort}
                    options={[]}
                    emptyLabel={providerCapabilitiesQuery.isPending && providerCapabilitiesEnabled ? '读取能力中…' : '推理不可用'}
                    disabled
                    onChange={() => undefined}
                  />}
            />
            <SettingRow title="主模型支持图片输入" description="只在已确认当前主模型能够接收图片时开启；框架不会根据模型名称猜测" source="user" control={<Toggle checked={userDraft.provider.supports_image_input} label="主模型支持图片输入" onChange={(value) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, supports_image_input: value } })} />} />
            {userDraft.provider.type === 'kemo' ? <>
              <SettingRow title="主模型支持音频输入" description="仅 Kemo 模式；仍以网关能力声明为最终依据" source="user" control={<Toggle checked={userDraft.provider.supports_audio_input} label="主模型支持音频输入" onChange={(value) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, supports_audio_input: value } })} />} />
              <SettingRow title="主模型支持视频输入" description="仅 Kemo 模式；未开启时由视频理解专用模型处理" source="user" control={<Toggle checked={userDraft.provider.supports_video_input} label="主模型支持视频输入" onChange={(value) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, supports_video_input: value } })} />} />
              <SettingRow title="主模型支持普通文件输入" description="仅 Kemo 模式；未开启时普通文件继续使用 file 工具" source="user" control={<Toggle checked={userDraft.provider.supports_file_input} label="主模型支持普通文件输入" onChange={(value) => setUserDraft({ ...userDraft, provider: { ...userDraft.provider, supports_file_input: value } })} />} />
            </> : null}
          </article>
          <details className="setting-section settings-disclosure" open>
            <summary><span><strong>子智能体模型</strong><small>按任务档位指定专用模型；留空时使用主对话模型。</small></span><ChevronDown size={16} /></summary>
            <div>{agentModelFields.map((field) => <SettingRow key={field.key} title={field.label} description={field.description} source="user" control={<ModelSelectField
              label={field.label}
              value={userDraft.agent_models[field.key]}
              placeholder="继承主对话模型"
              models={providerModels}
              enabled={modelPickerEnabled}
              onChange={(model) => setUserDraft({ ...userDraft, agent_models: { ...userDraft.agent_models, [field.key]: model } })}
            />} />)}</div>
          </details>
          <details className="setting-section settings-disclosure" open>
            <summary><span><strong>多模态模型</strong><small>为不同能力指定专用模型；留空表示不指定专用模型。</small></span><ChevronDown size={16} /></summary>
            <div>
              <SettingRow title="图片路由" description="自动模式优先主模型，不支持图片时改用专用视觉模型" source="user" control={<VisionRoutingSelect value={userDraft.multimodal_routing.vision} onChange={(vision) => setUserDraft({ ...userDraft, multimodal_routing: { vision } })} />} />
              {multimodalFields.map((field) => <SettingRow key={field.key} title={field.label} description={field.description} source="user" control={<ModelSelectField
                label={field.label}
                value={userDraft.multimodal_models[field.key]}
                placeholder="未指定"
                models={providerModels}
                enabled={modelPickerEnabled}
                onChange={(model) => setUserDraft({ ...userDraft, multimodal_models: { ...userDraft.multimodal_models, [field.key]: model } })}
              />} />)}
            </div>
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

        {tab === 'permissions' && userDraft ? <>
          <ConfigSaveBar label="保存权限边界" description="知识范围与模块白名单写入当前用户配置；知识图谱外挂由普通拓展和插件权限控制。" pending={saveMutation.isPending} saved={savedLabel === '保存权限边界'} onSave={savePermissions} />
          <article className="setting-section">
            <div className="setting-section-head"><strong>知识库开关</strong><span>用户知识库始终有效；以下开关控制额外知识层。</span></div>
            <SettingRow title="使用共享知识库" description="将 shared_knowledge 加入当前用户知识范围" source="user" control={<Toggle checked={userDraft.knowledge.use_shared} label="使用共享知识库" onChange={(value) => setUserDraft({ ...userDraft, knowledge: { ...userDraft.knowledge, use_shared: value } })} />} />
            <SettingRow title="使用全局知识库" description="将 global_knowledge 加入当前用户知识范围" source="user" control={<Toggle checked={userDraft.knowledge.use_global} label="使用全局知识库" onChange={(value) => setUserDraft({ ...userDraft, knowledge: { ...userDraft.knowledge, use_global: value } })} />} />
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
            <div className="setting-section-head"><strong>工具执行</strong><span>分别约束工具等待时间、单轮调用总数以及连续重复或失败行为。</span></div>
            <SettingRow title="工具调用超时（秒）" description="单个工具执行的最长等待时间" source="global" control={<NumberInput label="工具调用超时" value={globalDraft.tools.timeout} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, tools: { ...globalDraft.tools, timeout: value } })} />} />
            <SettingRow title="单轮最大工具调用数" description="一轮用户对话内允许处理的工具调用总数；同一响应中的并行调用分别计数" source="global" control={<NumberInput label="单轮最大工具调用数" value={globalDraft.tools.max_iterations} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, tools: { ...globalDraft.tools, max_iterations: value } })} />} />
            <SettingRow title="单个工具最大连续使用上限" description="仅当工具名称和完整参数连续完全相同时累计；参数变化后重新计数" source="global" control={<NumberInput label="单个工具最大连续使用上限" value={globalDraft.tools.consecutive_identical_call_limit} min={1} onChange={(value) => setGlobalDraft({ ...globalDraft, tools: { ...globalDraft.tools, consecutive_identical_call_limit: value } })} />} />
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
              onClick={() => { setRestartMessage(''); setRestartCanForce(false); setRestartState('confirming') }}
            ><Power size={15} />{restartState === 'failed' ? '重新尝试' : '重启智能体'}</button>
            {restartMessage ? <div className={`settings-restart-status ${restartState === 'failed' ? 'error' : ''}`} role="status">
              <span className="settings-restart-status-message">{restartState === 'restarting' || restartState === 'waiting' ? <RefreshCw className="spin" size={14} /> : null}{restartMessage}</span>
              {restartCanForce ? <button type="button" className="settings-force-restart-button" onClick={() => setRestartState('confirming-force')}><Power size={13} />强制重启</button> : null}
            </div> : null}
          </article>
          {restartState === 'confirming' || restartState === 'confirming-force' ? createPortal(<div className="settings-restart-confirm-layer" onMouseDown={(event) => { if (event.target === event.currentTarget) closeRestartConfirmation() }}>
            <section className={`settings-restart-confirm ${restartState === 'confirming-force' ? 'force' : ''}`} role="alertdialog" aria-modal="true" aria-label={restartState === 'confirming-force' ? '确认强制重启智能体' : '确认重启智能体'}>
              <span className="settings-restart-confirm-icon"><AlertTriangle size={23} /></span>
              <span className="settings-restart-confirm-copy">
                <strong>{restartState === 'confirming-force' ? '您确定要强制重启吗？' : '您确定要重启吗？'}</strong>
                <small>{restartState === 'confirming-force' ? '后端仍报告存在运行中的对话。强制重启将绕过运行状态检查，未完成的回复或任务可能会被中断。' : '智能体在执行任务时重启可能会出现故障。请先确认当前任务已经结束，并保存尚未提交的配置。'}</small>
                <small>重启期间网页会短暂断开，服务恢复后将自动刷新。</small>
              </span>
              <span className="settings-restart-confirm-actions"><button type="button" onClick={closeRestartConfirmation}>取消</button><button type="button" className="confirm" onClick={() => void confirmRestart(restartState === 'confirming-force')}>{restartState === 'confirming-force' ? '确认强制重启' : '确认重启'}</button></span>
            </section>
          </div>, document.body) : null}
        </> : null}

        {tab === 'version' && versionQuery.data ? <>
          <article className="setting-section version-overview-section">
            <div className="setting-section-head"><strong>当前版本</strong><span>只读展示项目内的版本声明，不提供检查、下载或更新功能。</span></div>
            <SettingRow title="项目" description="当前运行的智能体框架" control={<span className="settings-version-name">{versionQuery.data.name}</span>} />
            <SettingRow title="正式版本" description="来自项目根目录 version.json" control={<span className="settings-version-value primary">{versionLabel(versionQuery.data.version)}</span>} />
            <SettingRow title="版本结构" description="version.json 使用的结构版本" control={<span className="settings-version-value">Schema {versionQuery.data.schema_version || '未声明'}</span>} />
            <SettingRow title="页面能力" description="此栏目只能查看版本信息" control={<span className="settings-version-readonly">只读</span>} />
          </article>
          {settingsQuery.data?.schema_versions ? <article className="setting-section">
            <div className="setting-section-head"><strong>配置 Schema 版本</strong><span>仅供更新校验参考；历史与记忆运行时仍以数据库内部 Schema 为准。</span></div>
            <SettingRow title="配置结构版本" description="config/global_config.json → schema_version" control={<span className="settings-version-value" aria-label="配置结构 Schema 版本">{settingsQuery.data.schema_versions.config_schema}</span>} />
            <SettingRow title="历史结构版本" description="history.schema_version" control={<span className="settings-version-value" aria-label="历史结构 Schema 版本">{settingsQuery.data.schema_versions.history_schema}</span>} />
            <SettingRow title="记忆存储版本" description="memory.storage_schema_version" control={<span className="settings-version-value" aria-label="记忆存储 Schema 版本">{settingsQuery.data.schema_versions.memory_storage_schema}</span>} />
          </article> : null}
          <article className="setting-section">
            <div className="setting-section-head"><strong>组件版本</strong><span>分别查看核心引擎、子代理、插件生态和 Web 界面的版本声明。</span></div>
            {versionQuery.data.components.map((component) => <SettingRow
              key={component.id}
              title={component.description || component.id}
              description={`组件标识：${component.id}`}
              control={<span className="settings-version-value">{versionLabel(component.version)}</span>}
            />)}
            {!versionQuery.data.components.length ? <div className="settings-version-empty">version.json 尚未声明组件版本。</div> : null}
          </article>
          <article className="setting-section settings-version-check-section">
            <div className="setting-section-head"><strong>云端版本检查</strong><span>只比较 GitHub 正式版本，不会下载文件、修改代码或执行更新。</span></div>
            <div className="settings-version-check-head">
              <span className="settings-version-check-icon"><Cloud size={20} /></span>
              <span className="settings-version-check-copy">
                <strong>{versionChecking && !versionCheck ? '正在连接 GitHub…' : '检查是否存在新版本'}</strong>
                <small>{versionCheck?.checked_at ? `上次检查：${versionCheckTime(versionCheck.checked_at)}` : '进入此栏目后自动检查，也可以随时手动重新检查。'}</small>
              </span>
              <button type="button" className="module-btn" disabled={versionChecking} onClick={checkVersionNow}>
                <RefreshCw className={versionChecking ? 'spin' : ''} size={14} />
                {versionChecking ? '检查中…' : versionCheck ? '重新检查' : '检查新版本'}
              </button>
            </div>

            {versionCheckQuery.isError || versionCheckMutation.isError ? <div className="settings-version-check-result error" role="status">
              <strong>版本检查请求失败</strong>
              <span>Web 服务暂时无法完成请求，请稍后重新检查。</span>
            </div> : null}

            {versionCheck?.status === 'check_failed' ? <div className="settings-version-check-result error" role="status">
              <strong>无法完成云端版本检查</strong>
              <span>{versionCheck.error?.message || '云端版本信息暂时不可用，请稍后重试。'}</span>
            </div> : null}

            {versionCheck?.status === 'up_to_date' ? <div className="settings-version-check-result success" role="status">
              <strong>当前已是最新版本</strong>
              <span>本地 {versionLabel(versionCheck.local_version || '')} · 云端 {versionLabel(versionCheck.remote_version || '')}</span>
            </div> : null}

            {versionCheck?.status === 'local_newer' ? <div className="settings-version-check-result info" role="status">
              <strong>本地版本高于云端正式版本</strong>
              <span>本地 {versionLabel(versionCheck.local_version || '')} · 云端 {versionLabel(versionCheck.remote_version || '')}，无需执行更新。</span>
            </div> : null}

            {versionCheck?.status === 'update_available' ? <>
              <div className="settings-version-check-result available" role="status">
                <strong>发现新版本 {versionLabel(versionCheck.remote_version || '')}</strong>
                <span>当前为 {versionLabel(versionCheck.local_version || '')}。网页端不会替您更新，请在项目根目录的终端中执行下方命令。</span>
              </div>
              <div className="settings-version-components" aria-label="云端组件版本差异">
                {(versionCheck.components || []).map((component) => <div key={component.id} className={`settings-version-component ${component.status}`}>
                  <span><strong>{component.description}</strong><small>{component.id}</small></span>
                  <code>{versionLabel(component.local_version)} → {versionLabel(component.remote_version)}</code>
                  <b>{component.status === 'update_available' ? '待更新' : component.status === 'local_newer' ? '本地较新' : '已是最新'}</b>
                </div>)}
              </div>
              {versionCheck.commands?.recommended ? <div className="settings-version-command">
                <span><small>推荐：完整更新</small><code>{versionCheck.commands.recommended}</code></span>
                <button type="button" className="module-btn" onClick={() => void copyVersionUpdateCommand()} aria-label="复制更新命令">
                  {versionCopyState === 'copied' ? <Check size={14} /> : <Copy size={14} />}
                  {versionCopyState === 'copied' ? '已复制' : '复制命令'}
                </button>
                {versionCopyState === 'failed' ? <small className="settings-version-copy-error">复制失败，请手动选择命令。</small> : null}
              </div> : null}
            </> : null}
          </article>
        </> : null}

        {versionQuery.isLoading && tab === 'version' ? <div className="settings-loading">正在读取版本信息…</div> : null}
        {(userConfigQuery.isLoading || globalConfigQuery.isLoading) && tab !== 'appearance' && tab !== 'users' && tab !== 'version' ? <div className="settings-loading">正在读取结构化配置…</div> : null}
      </div>
    </div>
  </ModuleFrame>
}
