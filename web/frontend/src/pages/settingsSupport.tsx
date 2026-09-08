import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { Check, ChevronDown, Save } from 'lucide-react'
import type { KemoModelCapabilitiesResponse, KemoModelCatalogItem } from '../types/api'
import { normalizeKemoReasoningEffort, normalizeReasoningEffort, type ReasoningEffort } from '../reasoningEffort'

export type SettingsTab = 'appearance' | 'provider' | 'users' | 'memory' | 'permissions' | 'runtime' | 'version'
export type ProviderType = 'chat' | 'kemo'
export type VisionRoutingMode = 'auto' | 'main' | 'dedicated'
export type MultimodalKey = 'vision' | 'image_generation' | 'image_edit' | 'audio_transcription' | 'speech_generation' | 'speech_to_speech' | 'video_understanding' | 'video_generation'
export type AgentModelProfile = 'default' | 'cheap' | 'reasoning'
export type RestartState = 'idle' | 'confirming' | 'confirming-force' | 'restarting' | 'waiting' | 'failed'

export interface UserConfigDraft {
  provider: { type: ProviderType; model: string; base_url: string; api_key: string; stream: boolean; reasoning_effort: ReasoningEffort; supports_image_input: boolean; supports_audio_input: boolean; supports_video_input: boolean; supports_file_input: boolean }
  agent_models: Record<AgentModelProfile, string>
  multimodal_models: Record<MultimodalKey, string>
  multimodal_routing: { vision: VisionRoutingMode }
  knowledge: { use_shared: boolean; use_global: boolean }
  skills: { shared_whitelist: string[] }
  expand: { shared_whitelist: string[]; global_whitelist: string[]; prompt_injection: boolean; realtime_injection: boolean }
  perception: { global_whitelist: string[]; prompt_injection: boolean; realtime_injection: boolean }
  plugins: { whitelist: string[] }
  task_plan: { auto_accept: boolean }
}

export interface GlobalConfigDraft {
  agents: { token_limit: number; token_compression_ratio: number; max_rounds: number; rounds_after_compression: number }
  memory: { temporary_injection_limits: { seven_days: number; one_month: number; half_year: number } }
  tools: { timeout: number; max_iterations: number; consecutive_identical_call_limit: number; invalid_tool_arguments_retries: number }
  history: { consecutive_tool_fail_limit: number }
  task_plan: { max_steps: number }
  provider_runtime: { max_concurrent_requests: number; request_semaphore_timeout: number }
  web: { max_concurrent_chats: number; max_pending_chats: number; pending_chat_timeout: number }
  message: { max_workers: number; max_queued_messages: number }
  cron: { poll_interval: number; avoid_congestion: boolean; congestion_threshold_ratio: number }
  agent_runtime: { default_timeout: number; queue_maxsize: number }
}

export interface SaveRequest {
  label: string
  userChanges?: Record<string, unknown>
  globalChanges?: Record<string, unknown>
  providerDiscovery?: ProviderType
}

export interface SaveResult {
  providerModels?: KemoModelCatalogItem[]
  providerDiscoveryError?: string
}

export const settingsTabs: Array<{ id: SettingsTab; label: string }> = [
  { id: 'appearance', label: '外观与主题' },
  { id: 'provider', label: '模型与 Provider' },
  { id: 'users', label: '用户切换' },
  { id: 'memory', label: '记忆与上下文' },
  { id: 'permissions', label: '权限边界' },
  { id: 'runtime', label: '运行限制' },
  { id: 'version', label: '版本查看' },
]

const settingsTabIds = new Set<SettingsTab>(settingsTabs.map((item) => item.id))

export const multimodalFields: Array<{ key: MultimodalKey; label: string; description: string }> = [
  { key: 'vision', label: '图片识别', description: '图片分析、OCR 与视觉理解模型' },
  { key: 'image_generation', label: '图片生成', description: '文本生成图片的专用模型' },
  { key: 'image_edit', label: '图片编辑', description: '图片修改、局部重绘与图生图模型' },
  { key: 'audio_transcription', label: '语音识别', description: '音频转写与语音转文字模型' },
  { key: 'speech_generation', label: '语音生成', description: '文本转语音模型' },
  { key: 'speech_to_speech', label: '语音生语音', description: '语音到语音的转换模型' },
  { key: 'video_understanding', label: '视频理解', description: '视频分析、时间轴摘要与内容理解模型' },
  { key: 'video_generation', label: '视频生成', description: '文本或素材生成视频的模型' },
]

export const agentModelFields: Array<{ key: AgentModelProfile; label: string; description: string }> = [
  { key: 'default', label: '默认子智能体模型', description: '普通用户子智能体和未指定档位的后台代理；留空时继承主对话模型' },
  { key: 'cheap', label: '轻量子智能体模型', description: '历史摘要、上下文压缩和临时记忆整理等高频轻量任务；留空时继承主对话模型' },
  { key: 'reasoning', label: '推理子智能体模型', description: '任务计划、自我改进和需要较深分析的后台任务；留空时继承主对话模型' },
]

export function isSettingsTab(value: string | null): value is SettingsTab {
  return value !== null && settingsTabIds.has(value as SettingsTab)
}

export function record(value: unknown): Record<string, unknown> {
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

export function versionLabel(value: string) {
  const normalized = String(value || '').trim().replace(/^v/i, '')
  return normalized ? `v${normalized}` : '未声明'
}

export function versionCheckTime(value: string) {
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) return value || '未知'
  return parsed.toLocaleString('zh-CN', { hour12: false })
}

export function reasoningPolicyDescription(response: KemoModelCapabilitiesResponse | undefined) {
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

export function buildUserDraft(config: Record<string, unknown>): UserConfigDraft {
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
      prompt_injection: booleanValue(expand.prompt_injection, true),
      realtime_injection: booleanValue(expand.realtime_injection, false),
    },
    perception: {
      global_whitelist: stringList(perception.global_whitelist),
      prompt_injection: booleanValue(perception.prompt_injection, true),
      realtime_injection: booleanValue(perception.realtime_injection, false),
    },
    plugins: { whitelist: stringList(plugins.whitelist) },
    task_plan: { auto_accept: booleanValue(taskPlan.auto_accept, false) },
  }
}

export function buildGlobalDraft(config: Record<string, unknown>): GlobalConfigDraft {
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
      invalid_tool_arguments_retries: numberValue(tools.invalid_tool_arguments_retries, 2),
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

export function SettingRow({ title, description, control, source }: { title: string; description: string; control: ReactNode; source?: 'user' | 'global' }) {
  return <div className="setting-row"><span className="setting-copy"><strong>{title}{source ? <i className={`config-source ${source}`}>{source === 'user' ? '用户' : '全局'}</i> : null}</strong><span>{description}</span></span><span className="setting-control">{control}</span></div>
}

export function Toggle({ checked, label, disabled = false, onChange }: { checked: boolean; label: string; disabled?: boolean; onChange: (value: boolean) => void }) {
  return <button type="button" role="switch" aria-label={label} aria-checked={checked} disabled={disabled} className={`config-switch ${checked ? 'on' : ''}`} onClick={() => onChange(!checked)}>
    <span>{checked ? '已开启' : '已关闭'}</span><i aria-hidden="true"><b /></i>
  </button>
}

export function ProviderSelect({ value, onChange }: { value: ProviderType; onChange: (value: ProviderType) => void }) {
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

export function VisionRoutingSelect({ value, onChange }: { value: VisionRoutingMode; onChange: (value: VisionRoutingMode) => void }) {
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

export function ModelSelectField({
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

export function NumberInput({ label, value, min = 0, max, step = 1, onChange }: { label: string; value: number; min?: number; max?: number; step?: number; onChange: (value: number) => void }) {
  return <input className="config-field config-number" type="number" aria-label={label} value={value} min={min} max={max} step={step} onChange={(event) => onChange(Number(event.target.value))} />
}

export function TagInput({ label, value, onChange }: { label: string; value: string[]; onChange: (value: string[]) => void }) {
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

export function ConfigSaveBar({ label, description, pending, saved, onSave }: { label: string; description: string; pending: boolean; saved: boolean; onSave: () => void }) {
  return <div className="settings-savebar"><span><strong>{saved ? `${label.replace(/^保存/, '')} 已保存` : label}</strong><small>{description}</small></span><button className="module-btn primary" disabled={pending} onClick={onSave}><Save size={14} />{pending ? '保存中…' : label}</button></div>
}
