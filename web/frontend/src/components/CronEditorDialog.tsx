import { t as tr, useLanguage } from '../i18n'
import { useState } from 'react'
import { TimerReset } from 'lucide-react'
import type { CronTaskSummary } from '../types/api'
import { GlobalConfirmDialog } from './GlobalConfirmDialog'

const list = (text: string) => [...new Set(text.split(/[,，\s]+/).filter(Boolean).map(Number))].sort((a, b) => a - b)

export function beijingDateTimeInput(value: string | undefined) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(date)
  const part = (type: string) => parts.find(item => item.type === type)?.value || ''
  return `${part('year')}-${part('month')}-${part('day')}T${part('hour')}:${part('minute')}`
}

export function CronEditorDialog({ task, pending, error, onSave, onClose }: {
  task?: CronTaskSummary; pending: boolean; error: string
  onSave: (body: Record<string, unknown>) => Promise<void>; onClose: () => void
}) {
  useLanguage()
  const [title, setTitle] = useState(task?.title || '')
  const [prompt, setPrompt] = useState('')
  const [type, setType] = useState(task?.type || 'daily')
  const [times, setTimes] = useState(task?.times?.join(', ') || task?.time || '09:00')
  const [days, setDays] = useState((task?.type === 'monthly' ? task.month_days : task?.weekdays)?.join(', ') || '1')
  const [interval, setInterval] = useState(String(task?.interval_seconds || 3600))
  const [next, setNext] = useState(beijingDateTimeInput(task?.next_run_at))
  const [start, setStart] = useState(task?.start_date || '')
  const [end, setEnd] = useState(task?.end_date || '')
  const [max, setMax] = useState(task?.max_runs ? String(task.max_runs) : '')
  const [validation, setValidation] = useState('')
  const submit = async () => {
    const body: Record<string, unknown> = { title: title.trim(), type }
    if (!title.trim() || (!task && !prompt.trim())) { setValidation('请填写标题和执行内容'); return }
    if (max && (!Number.isInteger(Number(max)) || Number(max) < 1)) { setValidation('最大成功执行次数必须是大于等于 1 的整数，或留空表示不限。'); return }
    if (type === 'recurring' && (!Number.isInteger(Number(interval)) || Number(interval) < 60)) { setValidation('重复间隔不能小于 60 秒'); return }
    if (!task || prompt.trim()) body.prompt = prompt
    if (type === 'once') {
      if (!next) { setValidation('请选择单次执行时间'); return }
      body.next_run_at = `${next}:00+08:00`
    } else {
      if (type === 'recurring') body.interval_seconds = Number(interval)
      else {
        const values = [...new Set(times.split(/[,，\s]+/).filter(Boolean))].sort()
        if (!values.length || values.some(time => !/^([01]\d|2[0-3]):[0-5]\d$/.test(time))) { setValidation('执行时刻应为 HH:MM，多个以逗号分隔'); return }
        if (values.length === 1) body.time = values[0]
        else body.times = values
        if (type === 'weekly' || type === 'monthly') {
          const values = list(days)
          if (!values.length || values.some(day => !Number.isInteger(day) || day < 1 || day > (type === 'weekly' ? 7 : 31))) {
            setValidation(type === 'weekly' ? '星期必须为 1–7 的整数' : '每月日期必须为 1–31 的整数'); return
          }
          body[type === 'weekly' ? 'weekdays' : 'month_days'] = values
        }
      }
      body.start_date = start; body.end_date = end
      body.max_runs = max ? Number(max) : null
    }
    setValidation('')
    await onSave(body)
  }
  const fieldStyle = { display: 'grid', gap: 6 } as const
  return <GlobalConfirmDialog open title={task ? '编辑定时任务' : '新建定时任务'} icon={<TimerReset size={22} />}
    description={tr("按北京时间配置任务；执行内容应能在每次触发时独立理解。")}
    detailMode="content" detail={<div style={{ display: 'grid', gap: 12, maxHeight: '55vh', overflow: 'auto' }}>
      <label style={fieldStyle}>{tr("任务标题")}<input value={title} onChange={e => setTitle(e.target.value)} /></label>
      <label style={fieldStyle}>{tr("执行内容")}{task ? '（留空保留原内容）' : ''}<textarea rows={4} value={prompt} onChange={e => setPrompt(e.target.value)} /></label>
      <label style={fieldStyle}>{tr("任务类型")}<select value={type} onChange={e => { setType(e.target.value as CronTaskSummary['type']); setDays('1') }}>
        <option value="once">{tr("单次")}</option><option value="daily">{tr("每天")}</option><option value="weekly">{tr("每周")}</option><option value="monthly">{tr("每月")}</option><option value="recurring">{tr("固定间隔")}</option>
      </select></label>
      {type === 'once' ? <label style={fieldStyle}>{tr("执行时间（北京时间）")}<input type="datetime-local" value={next} onChange={e => setNext(e.target.value)} /></label>
        : <>
          {type === 'recurring' ? <label style={fieldStyle}>{tr("间隔秒数")}<input type="number" min={60} value={interval} onChange={e => setInterval(e.target.value)} /></label>
            : <label style={fieldStyle}>{tr("执行时刻（北京时间，多个 HH:MM 以逗号分隔）")}<input value={times} onChange={e => setTimes(e.target.value)} /></label>}
          {['weekly', 'monthly'].includes(type) && <label style={fieldStyle}>{type === 'weekly' ? '星期（1=周一，7=周日）' : '每月日期（1–31）'}<input value={days} onChange={e => setDays(e.target.value)} /></label>}
          <label style={fieldStyle}>{tr("生效日期")}<input type="date" value={start} onChange={e => setStart(e.target.value)} /></label>
          <label style={fieldStyle}>{tr("结束日期")}<input type="date" value={end} onChange={e => setEnd(e.target.value)} /></label>
          <label style={fieldStyle}>{tr("最大成功执行次数（留空不限）")}<input type="number" min={1} value={max} onChange={e => setMax(e.target.value)} /></label>
        </>}
    </div>}
    error={validation || error} pending={pending} confirmLabel={tr("保存")} pendingLabel={tr("正在保存…")}
    onCancel={onClose} onConfirm={() => { void submit().catch(() => undefined) }} />
}
