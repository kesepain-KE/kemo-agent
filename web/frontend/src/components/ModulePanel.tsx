import { useEffect, useMemo, useState, type Dispatch, type SetStateAction } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from '../markdownLinks'
import { Eye, EyeOff, Play, RotateCcw, Save, Settings2, Trash2 } from 'lucide-react'
import type {
  ModulePanelConfigContainer,
  ModulePanelContainer,
  ModulePanelField,
  ModulePanelResponse,
  ModulePanelValue,
} from '../types/api'
import { HoverPreview } from './HoverPreview'
import styles from './ModulePanel.module.css'

interface ModulePanelProps {
  kind: 'expand' | 'sense'
  data?: ModulePanelResponse
  loading?: boolean
  busy?: boolean
  error?: string
  emptyText: string
  onSave: (values: Record<string, ModulePanelValue>, clearSecrets: string[]) => Promise<void>
  onAction: (command: string, params: Record<string, ModulePanelValue>) => Promise<void>
}

function fieldDefault(field: ModulePanelField): ModulePanelValue {
  if (field.default !== undefined) return field.default
  if (field.type === 'boolean') return false
  if (field.type === 'number') return field.min ?? 0
  if (field.type === 'enum') return field.options?.[0]?.value ?? ''
  return ''
}

function initialValues(data?: ModulePanelResponse): Record<string, ModulePanelValue> {
  const result: Record<string, ModulePanelValue> = {}
  for (const container of data?.panel?.containers || []) {
    if (container.kind !== 'config') continue
    for (const field of container.fields) {
      result[field.key] = field.masked
        ? ''
        : container.current_values?.[field.key] ?? fieldDefault(field)
    }
  }
  return result
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'object') return JSON.stringify(value, null, 2)
  return String(value)
}

function badgeTone(value: unknown): string {
  const text = String(value || '').toLowerCase()
  if (/正常|健康|成功|启用|active|healthy|success|ready/.test(text)) return styles.success
  if (/异常|失败|错误|停用|error|failed|danger|dead/.test(text)) return styles.danger
  if (/等待|警告|降级|pending|warning|degraded/.test(text)) return styles.warning
  return styles.neutral
}

function previewText(
  container: ModulePanelContainer,
  draft: Record<string, ModulePanelValue>,
  actionValues: Record<string, ModulePanelValue>,
): string {
  const lines = [container.title]
  if (container.kind === 'status') {
    for (const field of container.fields) {
      lines.push(`${field.label}：${field.masked ? (container.secret_set?.[field.key] ? '已设置' : '未设置') : displayValue(container.data?.[field.key])}`)
    }
  } else if (container.kind === 'config') {
    for (const field of container.fields) {
      lines.push(`${field.label}：${field.masked ? (container.secret_set?.[field.key] ? '已设置（不回显）' : '未设置') : displayValue(draft[field.key] ?? fieldDefault(field))}`)
    }
  } else {
    for (const control of container.controls) {
      lines.push(`操作：${control.label}`)
      for (const field of control.inputs || []) {
        const value = actionValues[`${control.command}:${field.key}`] ?? fieldDefault(field)
        lines.push(`${field.label}：${field.masked ? (value ? '已填写（不回显）' : '未填写') : displayValue(value)}`)
      }
    }
  }
  const text = lines.join('\n')
  return text.length > 4_000 ? `${text.slice(0, 4_000)}\n\n共 ${text.length} 字符，已截断显示。` : text
}

function StatusContainer({ container }: { container: Extract<ModulePanelContainer, { kind: 'status' }> }) {
  return <div className={styles.statusFields}>
    {container.data_error ? <p className={styles.containerError}>{container.data_error}</p> : null}
    {container.fields.length ? container.fields.map((field) => {
      const value = field.masked
        ? container.secret_set?.[field.key] ? '已设置' : '未设置'
        : container.data?.[field.key]
      if (field.type === 'markdown') {
        return <section className={styles.markdownField} key={field.key}><strong>{field.label}</strong><ReactMarkdown remarkPlugins={[remarkGfm]}>{displayValue(value)}</ReactMarkdown></section>
      }
      if (field.type === 'badge') {
        return <div className={styles.statusRow} key={field.key}><span>{field.label}</span><b className={`${styles.badge} ${badgeTone(value)}`}>{displayValue(value)}</b></div>
      }
      if (field.type === 'keyvalue' && value && typeof value === 'object') {
        return <section className={styles.keyValueField} key={field.key}><strong>{field.label}</strong><dl>{Object.entries(value).slice(0, 100).map(([key, item]) => <div key={key}><dt>{key}</dt><dd>{displayValue(item)}</dd></div>)}</dl></section>
      }
      return <div className={styles.statusRow} key={field.key}><span>{field.label}</span><strong>{displayValue(value)}</strong></div>
    }) : <p className={styles.containerEmpty}>暂无状态字段</p>}
  </div>
}

function ConfigField({ field, value, secretSet, cleared, reveal, onReveal, onChange, onClear, idPrefix = 'config' }: {
  field: ModulePanelField
  value: ModulePanelValue
  secretSet: boolean
  cleared: boolean
  reveal: boolean
  onReveal: () => void
  onChange: (value: ModulePanelValue) => void
  onClear: () => void
  idPrefix?: string
}) {
  const id = `module-panel-${idPrefix.replace(/[^A-Za-z0-9_-]/g, '-')}-${field.key}`
  return <label className={`${styles.configField} ${field.type === 'boolean' ? styles.booleanField : ''}`} htmlFor={id}>
    <span><strong>{field.label}{field.required ? ' *' : ''}</strong>{field.description ? <small>{field.description}</small> : null}</span>
    {field.type === 'boolean' ? <button id={id} type="button" role="switch" aria-checked={Boolean(value)} className={`${styles.switch} ${value ? styles.checked : ''}`} onClick={() => onChange(!value)}><span /></button>
      : field.type === 'enum' ? <select id={id} value={String(value)} onChange={(event) => onChange(event.target.value)}>{field.options?.map((option) => <option value={option.value} key={option.value}>{option.label}</option>)}</select>
        : field.type === 'text' ? <textarea id={id} value={String(value)} maxLength={field.max_length} placeholder={field.placeholder} onChange={(event) => onChange(event.target.value)} />
          : <span className={styles.inputWrap}><input
            id={id}
            type={field.type === 'number' ? 'number' : field.masked && !reveal ? 'password' : 'text'}
            value={String(value)}
            min={field.min}
            max={field.max}
            maxLength={field.max_length}
            placeholder={field.masked && secretSet && !cleared ? '已设置；留空保持不变' : field.placeholder}
            onChange={(event) => onChange(field.type === 'number' ? event.target.value === '' ? '' : Number(event.target.value) : event.target.value)}
          />{field.masked ? <button type="button" className={styles.inputAction} aria-label={`${reveal ? '隐藏' : '显示'}${field.label}`} onClick={onReveal}>{reveal ? <EyeOff size={14} /> : <Eye size={14} />}</button> : null}{field.masked && secretSet ? <button type="button" className={`${styles.inputAction} ${styles.clearAction}`} aria-label={`清除${field.label}`} onClick={onClear}>{cleared ? <RotateCcw size={14} /> : <Trash2 size={14} />}</button> : null}</span>}
  </label>
}

function ConfigContainer({ container, draft, clearSecrets, revealed, onChange, onPreset, onToggleClear, onToggleReveal }: {
  container: ModulePanelConfigContainer
  draft: Record<string, ModulePanelValue>
  clearSecrets: Set<string>
  revealed: Set<string>
  onChange: (key: string, value: ModulePanelValue) => void
  onPreset: (values: Record<string, ModulePanelValue>) => void
  onToggleClear: (key: string) => void
  onToggleReveal: (key: string) => void
}) {
  return <div className={styles.configBody}>
    {container.data_error ? <p className={styles.containerError}>{container.data_error}</p> : null}
    {container.presets.length ? <div className={styles.presets}><span>快速配置</span>{container.presets.map((preset) => <button type="button" key={preset.name} onClick={() => onPreset(preset.values)}>{preset.name}</button>)}</div> : null}
    <div className={styles.configFields}>{container.fields.map((field) => <ConfigField
      key={field.key}
      field={field}
      value={draft[field.key] ?? fieldDefault(field)}
      secretSet={Boolean(container.secret_set?.[field.key])}
      cleared={clearSecrets.has(field.key)}
      reveal={revealed.has(field.key)}
      onReveal={() => onToggleReveal(field.key)}
      onClear={() => onToggleClear(field.key)}
      onChange={(value) => onChange(field.key, value)}
      idPrefix="config"
    />)}</div>
  </div>
}

export function ModulePanel({ kind, data, loading = false, busy = false, error = '', emptyText, onSave, onAction }: ModulePanelProps) {
  const [draft, setDraft] = useState<Record<string, ModulePanelValue>>({})
  const [clearSecrets, setClearSecrets] = useState<Set<string>>(new Set())
  const [revealed, setRevealed] = useState<Set<string>>(new Set())
  const [actionValues, setActionValues] = useState<Record<string, ModulePanelValue>>({})
  const [feedback, setFeedback] = useState('')
  const [localError, setLocalError] = useState('')
  const panel = data?.panel
  const identity = `${data?.scope || kind}:${data?.module || ''}:${panel?.title || ''}`

  useEffect(() => {
    setDraft(initialValues(data))
    setClearSecrets(new Set())
    setRevealed(new Set())
    setActionValues({})
    setFeedback('')
    setLocalError('')
  }, [identity, data])

  const hasConfig = useMemo(() => panel?.containers.some((container) => container.kind === 'config') ?? false, [panel])
  const updateDraft = (key: string, value: ModulePanelValue) => {
    setDraft((current) => ({ ...current, [key]: value }))
    setClearSecrets((current) => { const next = new Set(current); next.delete(key); return next })
  }
  const applyPreset = (values: Record<string, ModulePanelValue>) => setDraft((current) => ({ ...current, ...values }))
  const toggleSet = (setter: Dispatch<SetStateAction<Set<string>>>, key: string) => setter((current) => {
    const next = new Set(current)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })
  const save = async () => {
    setFeedback(''); setLocalError('')
    try {
      const writable: Record<string, ModulePanelValue> = {}
      for (const container of panel?.containers || []) {
        if (container.kind !== 'config') continue
        for (const field of container.fields) {
          const value = draft[field.key] ?? fieldDefault(field)
          if (!field.masked || value !== '') writable[field.key] = value
        }
      }
      await onSave(writable, [...clearSecrets])
      setFeedback('配置已保存并触发刷新')
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : '配置保存失败')
    }
  }
  const runAction = async (command: string, fields: ModulePanelField[] = []) => {
    setFeedback(''); setLocalError('')
    try {
      const params: Record<string, ModulePanelValue> = {}
      for (const field of fields) params[field.key] = actionValues[`${command}:${field.key}`] ?? fieldDefault(field)
      await onAction(command, params)
      setFeedback(command === 'refresh' ? '已触发刷新' : `操作“${command}”已完成`)
    } catch (caught) {
      setLocalError(caught instanceof Error ? caught.message : '面板操作失败')
    }
  }

  if (loading) return <div className={styles.state} aria-busy="true"><Settings2 size={19} />正在读取用户配置…</div>
  if (error || data?.panel_error) return <div className={`${styles.state} ${styles.error}`} role="alert"><Settings2 size={19} /><span><strong>组件声明无法解析</strong><small>{error || data?.panel_error}</small></span></div>
  if (!panel) return <div className={`${styles.state} ${styles.empty}`}><Settings2 size={19} />{emptyText}</div>

  return <section className={styles.panel} aria-label={panel.title} aria-busy={busy}>
    <header className={styles.panelHeader}><span><Settings2 size={16} /><span><strong>{panel.title}</strong><small>{kind === 'expand' ? '拓展模块用户配置与快捷操作' : '感知模块用户配置与手动采集'}</small></span></span>{hasConfig ? <button type="button" className={styles.saveButton} disabled={busy} onClick={() => { void save() }}>{busy ? <RotateCcw className={styles.spinning} size={14} /> : <Save size={14} />}保存配置</button> : null}</header>
    {(localError || feedback) ? <div className={`${styles.feedback} ${localError ? styles.error : styles.saved}`} role={localError ? 'alert' : 'status'}>{localError || feedback}</div> : null}
    <div className={styles.grid}>{panel.containers.map((container, index) => <HoverPreview
      key={`${container.kind}-${container.title}-${index}`}
      className={`${styles.card} ${styles[container.width]} ${styles[container.height]}`}
      ariaLabel={`${container.title}，悬停或聚焦可查看完整内容`}
      preview={<pre className={styles.previewText}>{previewText(container, draft, actionValues)}</pre>}
    >
      <section><header className={styles.cardHeader}><strong>{container.title}</strong><span>{container.kind === 'status' ? '状态' : container.kind === 'config' ? '配置' : '操作'}</span></header>
        <div className={styles.cardBody}>{container.kind === 'status' ? <StatusContainer container={container} /> : container.kind === 'config' ? <ConfigContainer
          container={container}
          draft={draft}
          clearSecrets={clearSecrets}
          revealed={revealed}
          onChange={updateDraft}
          onPreset={applyPreset}
          onToggleClear={(key) => toggleSet(setClearSecrets, key)}
          onToggleReveal={(key) => toggleSet(setRevealed, key)}
        /> : <div className={styles.actions}>{container.controls.map((control) => <section key={`${control.command}-${control.label}`} className={styles.actionControl}>{control.inputs?.map((field) => <ConfigField
          key={field.key}
          field={field}
          value={actionValues[`${control.command}:${field.key}`] ?? fieldDefault(field)}
          secretSet={false}
          cleared={false}
          reveal={revealed.has(`${control.command}:${field.key}`)}
          onReveal={() => toggleSet(setRevealed, `${control.command}:${field.key}`)}
          onClear={() => undefined}
          onChange={(value) => setActionValues((current) => ({ ...current, [`${control.command}:${field.key}`]: value }))}
          idPrefix={`action-${control.command}`}
        />)}<button type="button" disabled={busy} onClick={() => { void runAction(control.command, control.inputs) }}>{control.command === 'refresh' ? <RotateCcw size={14} /> : <Play size={14} />}{control.label}</button></section>)}</div>}</div>
      </section>
    </HoverPreview>)}</div>
    {busy ? <div className={styles.busyOverlay}><RotateCcw className={styles.spinning} size={18} />正在处理…</div> : null}
  </section>
}
