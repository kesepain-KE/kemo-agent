import { useEffect, useState } from 'react'
import { t as tr, useLanguage } from '../i18n'
import { ConfigSaveBar, record, SettingRow, Toggle, type SaveRequest } from './settingsSupport'

type Field = {
  path: string; title: string; description: string
  kind: 'number' | 'boolean' | 'select' | 'time' | 'delays' | 'readonly'
  fallback: string | number | boolean | null
  min?: number; max?: number; step?: number; nullable?: boolean
  options?: Array<[string, string]>
}

const groups: Array<{ title: string; description: string; fields: Field[] }> = [
  { title: '记忆策略与提取', description: '全局默认值；用户记忆模式和各层注入数量可在“记忆与上下文”覆盖。', fields: [
    { path: 'memory.profile', title: '默认记忆模式', description: '陪伴模式保存事实、关系和状态；工作模式仅保存事实。', kind: 'select', fallback: 'companion', options: [['companion', '陪伴模式'], ['work', '工作模式']] },
    { path: 'memory.extraction_mode', title: '记忆提取模式', description: '选择每次提交后提取，或仅在压缩和会话归档时提取。', kind: 'select', fallback: 'compression_only', options: [['compression_only', '压缩与归档时提取'], ['on_commit', '每次提交后提取']] },
    { path: 'memory.extraction_input_tokens', title: '记忆提取输入预算', description: '每批记忆提取允许读取的 Token 数。', kind: 'number', fallback: 12000, min: 512, max: 64000 },
    { path: 'memory.extraction_max_candidates_per_batch', title: '每批候选记忆上限', description: '每批提取最多返回的候选条数。', kind: 'number', fallback: 30, min: 1, max: 40 },
    { path: 'memory.history_read_enabled', title: '允许读取历史', description: '控制历史检索能力；不改变已有对话的存储。', kind: 'boolean', fallback: true },
    { path: 'memory.important_memory_max_chars', title: '临时重要记忆注入字符上限', description: '临时重要记忆进入系统提示词时的字符预算。', kind: 'number', fallback: 20000, min: 0 },
    { path: 'memory.important_memory_output_max_chars', title: '临时重要记忆输出字符上限', description: '后台整理生成临时重要记忆时的字符预算。', kind: 'number', fallback: 20000, min: 1 },
    ...([['seven_days', '周', 300], ['one_month', '月', 200], ['half_year', '半年', 100]] as const).map(([key, label, fallback]): Field => ({ path: `memory.temporary_injection_limits.${key}`, title: `默认${label}记忆注入数量`, description: '用户未填写数量时使用此全局默认值；0 表示不注入该层。', kind: 'number', fallback, min: 0 })),
  ] },
  { title: '记忆分层与晋升', description: '有效期留空表示无期限；晋升路径由框架固定。', fields: [
    ...([['seven_days', '周', 7, 3, '月记忆'], ['one_month', '月', 30, 10, '半年记忆'], ['half_year', '半年', 180, 60, '永久记忆']] as const).flatMap(([key, label, days, threshold, next]): Field[] => [
      { path: `memory.tiers.${key}.days`, title: `${label}记忆有效天数`, description: '留空为无期限；填写时必须是正整数天。', kind: 'number', fallback: days, min: 1, nullable: true },
      { path: `memory.tiers.${key}.upgrade_threshold`, title: `${label}记忆晋升阈值`, description: '事实记忆的晋升权重；留空按 0 处理。', kind: 'number', fallback: threshold, min: 0, nullable: true },
      { path: `memory.tiers.${key}.next`, title: `${label}记忆下一层`, description: '固定晋升路径，只读展示。', kind: 'readonly', fallback: next },
    ]),
    { path: 'memory.domain_rules.relation.upgrade_threshold', title: '关系记忆晋升阈值', description: '关系记忆的晋升权重；留空表示不自动晋升。', kind: 'number', fallback: 1, min: 0, nullable: true },
    { path: 'memory.domain_rules.state.upgrade_threshold', title: '状态记忆晋升阈值', description: '当前状态域始终不晋升；此配置值保留供核对。', kind: 'number', fallback: null, min: 0, nullable: true },
    { path: 'memory.domain_rules.state.refresh_expiry_on_update', title: '更新状态记忆时续期', description: '更新状态记忆时重新计算其有效期。', kind: 'boolean', fallback: true },
  ] },
  { title: '提示词字符预算', description: '各提示词片段的独立字符上限。', fields: (
    [['user_soul', '用户人格', 12000], ['agents_manual', '操作手册', 26000], ['task_plan', '任务计划', 6000], ['perception', '感知数据', 20000], ['expand_data', '拓展数据', 20000], ['skill_prompts', '技能说明', 80000], ['plugin_prompts', '插件说明', 80000]] as const
  ).map(([key, label, fallback]): Field => ({ path: `prompt.char_limits.${key}`, title: `${label}字符上限`, description: '按字符数限制该片段；不影响工具结果的独立限制。', kind: 'number', fallback, min: 0 })) },
  { title: '记忆维护与标题摘要', description: '后台队列和定期整理的参数；调度参数保存后重启生效。', fields: [
    { path: 'agents.important_memory_review_hours', title: '临时重要记忆审查间隔（小时）', description: '支持小数，最短 1 分钟。', kind: 'number', fallback: 3, min: 1 / 60, step: 0.01 },
    { path: 'agents.daily_memory_review_time', title: '每日记忆整理时间', description: '每日整理使用的北京时间，24 小时制。', kind: 'time', fallback: '02:00' },
    { path: 'history_summary.poll_interval', title: '标题摘要轮询间隔（秒）', description: '后台检查待处理标题和摘要的频率。', kind: 'number', fallback: 5, min: 1 },
    { path: 'history_summary.max_jobs_per_cycle', title: '每轮标题摘要任务数', description: '单轮最多处理的后台任务数。', kind: 'number', fallback: 3, min: 1 },
    { path: 'history_summary.max_attempts', title: '标题摘要最大尝试次数', description: '任务失败后的总尝试次数上限。', kind: 'number', fallback: 5, min: 1 },
    { path: 'history_summary.retry_delays_seconds', title: '标题摘要重试间隔（秒）', description: '用逗号分隔每次重试的等待秒数，至少填写一项。', kind: 'delays', fallback: '30, 120, 600, 1800' },
  ] },
  { title: '工具、压缩与资源', description: '控制工具使用、压缩等待和短期资源接续。', fields: [
    { path: 'tools.enabled', title: '允许调用工具', description: '全局工具总开关。', kind: 'boolean', fallback: true },
    { path: 'task_plan.auto_retry_on_fix', title: '计划修复后自动重试', description: '计划修复成功后自动继续执行。', kind: 'boolean', fallback: false },
    { path: 'agents.compression_timeout_seconds', title: '上下文压缩超时（秒）', description: '单次历史压缩的最大等待时间。', kind: 'number', fallback: 180, min: 0.01, step: 0.01 },
    { path: 'agent_runtime.timeout_survival_seconds', title: '子代理超时后存活时间（秒）', description: '超时后的子代理继续存活和接续的期限。', kind: 'number', fallback: 120, min: 0 },
    { path: 'media_assets.ttl_seconds', title: '媒体资源存活时间（秒）', description: '上传文件和生成产物的 asset_id 可接续使用的时间。', kind: 'number', fallback: 120, min: 1 },
  ] },
  { title: '后台调度与网页连接', description: '进程级调度、消息并发和连接参数保存后重启生效。', fields: [
    { path: 'runtime_host.enable_background_scheduler', title: '启用后台调度', description: '控制 RuntimeHost 的后台调度。', kind: 'boolean', fallback: true },
    { path: 'cron.enabled', title: '启用 Cron 调度', description: '控制定时任务调度；还需开启后台调度总开关。', kind: 'boolean', fallback: true },
    { path: 'cron.log_retention_days', title: 'Cron 日志保留天数', description: '定时任务日志保留的天数。', kind: 'number', fallback: 30, min: 0 },
    { path: 'cron.task_cron_system.expand_update.interval_seconds', title: '拓展更新间隔（秒）', description: '后台采集拓展数据的间隔。', kind: 'number', fallback: 30, min: 1 },
    { path: 'task_cron_system.sense_update_rate', title: '感知更新间隔（秒）', description: '后台采集感知数据的间隔。', kind: 'number', fallback: 5, min: 1 },
    { path: 'task_cron_system.module_update_timeout', title: '模块更新超时（秒）', description: '单次拓展或感知更新的超时。', kind: 'number', fallback: 120, min: 1 },
    { path: 'task_cron_system.runtime_checkpoint_seconds', title: '运行状态检查点间隔（秒）', description: '调度运行状态写入磁盘的间隔。', kind: 'number', fallback: 300, min: 1 },
    { path: 'task_cron_system.success_log_flush_seconds', title: '成功日志落盘间隔（秒）', description: '后台成功日志批量写入磁盘的间隔。', kind: 'number', fallback: 3600, min: 1 },
    { path: 'message.max_workers', title: '消息工作线程数', description: '外部消息路由允许同时处理的工作线程数量。', kind: 'number', fallback: 8, min: 1 },
    { path: 'web.sse_heartbeat_seconds', title: '网页连接心跳间隔（秒）', description: 'SSE 长连接发送心跳的间隔。', kind: 'number', fallback: 15, min: 1 },
  ] },
]

function readField(config: Record<string, unknown>, field: Field): string | boolean {
  if (field.kind === 'readonly') return String(field.fallback)
  let value: unknown = config
  for (const key of field.path.split('.')) value = record(value)[key]
  if (value === undefined) value = field.fallback
  if (field.kind === 'boolean') return value as boolean
  if (Array.isArray(value)) return value.join(', ')
  return value === null ? '' : String(value)
}

export function AdvancedSettings({ config, pending, saved, onSave }: {
  config: Record<string, unknown>; pending: boolean; saved: boolean
  onSave: (request: SaveRequest, error?: string) => void
}) {
  useLanguage()
  const [edits, setEdits] = useState<Record<string, string | boolean>>({})
  useEffect(() => { setEdits({}) }, [config])
  const update = (path: string, value: string | boolean) => setEdits((current) => ({ ...current, [path]: value }))
  const save = () => {
    const changes: Record<string, unknown> = {}
    for (const field of groups.flatMap((group) => group.fields)) {
      if (field.kind === 'readonly' || !(field.path in edits) || edits[field.path] === readField(config, field)) continue
      const raw = edits[field.path]
      let value: unknown = raw
      let valid = true
      if (field.kind === 'number') {
        value = String(raw).trim() === '' && field.nullable ? null : Number(raw)
        valid = value === null || (String(raw).trim() !== '' && Number.isFinite(value as number) && (field.step !== undefined || Number.isInteger(value)) && (field.min === undefined || (value as number) >= field.min) && (field.max === undefined || (value as number) <= field.max))
      } else if (field.kind === 'delays') {
        const items = String(raw).split(/[,，\n]/).map((item) => item.trim()).filter(Boolean)
        value = items.map(Number)
        valid = (value as number[]).length > 0 && (value as number[]).every((item) => Number.isInteger(item) && item >= 1)
      } else if (field.kind === 'time') valid = /^([01]\d|2[0-3]):[0-5]\d$/.test(String(raw))
      if (!valid) {
        onSave({ label: '保存高级配置' }, `${tr(field.title)}：${tr('请输入有效的配置值。')}`)
        return
      }
      const parts = field.path.split('.')
      let target = changes
      for (const part of parts.slice(0, -1)) {
        target[part] ??= {}
        target = target[part] as Record<string, unknown>
      }
      target[parts.at(-1)!] = value
    }
    if (Object.keys(changes).length) onSave({ label: '保存高级配置', globalChanges: changes })
  }
  return <>
    <ConfigSaveBar label="保存高级配置" description="仅保存修改过的全局字段；后台调度、并发和连接参数需要重启后生效。" pending={pending} saved={saved} onSave={save} />
    {groups.map((group) => <article className="setting-section settings-advanced-group" key={group.title}>
      <div className="setting-section-head"><strong>{tr(group.title)}</strong><span>{tr(group.description)}</span></div>
      {group.fields.map((field) => {
        const value = edits[field.path] ?? readField(config, field)
        return <SettingRow key={field.path} title={field.title} description={field.description} source="global" control={
          field.kind === 'readonly' ? <span className="settings-version-value">{tr(String(value))}</span>
            : field.kind === 'boolean' ? <Toggle label={field.title} checked={Boolean(value)} onChange={(next) => update(field.path, next)} />
              : field.kind === 'select' ? <select className="config-field settings-choice" aria-label={tr(field.title)} value={String(value)} onChange={(event) => update(field.path, event.target.value)}>{field.options!.map(([key, label]) => <option key={key} value={key}>{tr(label)}</option>)}</select>
                : <input className={`config-field ${field.kind === 'number' ? 'config-number' : 'settings-choice'}`} aria-label={tr(field.title)} type={field.kind === 'number' ? 'number' : field.kind === 'time' ? 'time' : 'text'} value={String(value)} min={field.min} max={field.max} step={field.step ?? 1} placeholder={field.nullable ? tr('留空') : undefined} onChange={(event) => update(field.path, event.target.value)} />
        } />
      })}
    </article>)}
  </>
}
