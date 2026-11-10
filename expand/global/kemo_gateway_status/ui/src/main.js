import { kemo } from '/__kemo_bridge__'
import './style.css'

const $ = selector => document.querySelector(selector)
const state = { snapshot: null, status: null, configuration: null, view: 'overview' }
let busy = false
const count = value => value == null ? '—' : Number(value).toLocaleString()
const yesNo = value => value == null ? '未知' : value ? '是' : '否'
const periods = { today: '当天', '24h': '最近 24 小时', '7d': '最近 7 天', '30d': '最近 30 天', all: '全部历史' }
const resourceKinds = { protocol_adapters: '协议适配器', provider_templates: '提供商模板' }
const text = (id, value) => { $(id).textContent = value ?? '—' }
function applyTheme(theme) {
  document.documentElement.dataset.theme = theme.mode
  for (const [key, value] of Object.entries(theme.colors)) document.documentElement.style.setProperty(key, value)
}
async function action(id, params = {}) {
  const result = await kemo.request('module.action.invoke', { action: id, params })
  return result?.result ?? result
}
function table(selector, columns, rows) {
  const target = $(selector)
  target.replaceChildren()
  const head = document.createElement('tr')
  for (const label of columns) { const cell = document.createElement('th'); cell.textContent = label; head.append(cell) }
  const thead = document.createElement('thead'); thead.append(head); target.append(thead)
  const body = document.createElement('tbody')
  for (const row of rows) {
    const tr = document.createElement('tr')
    for (const value of row) { const td = document.createElement('td'); td.textContent = String(value ?? '—'); tr.append(td) }
    body.append(tr)
  }
  if (!rows.length) { const tr = document.createElement('tr'); const td = document.createElement('td'); td.colSpan = columns.length; td.textContent = '暂无采集记录'; tr.append(td); body.append(tr) }
  target.append(body)
}
function render() {
  const snapshot = state.snapshot
  $('#empty').hidden = Boolean(snapshot)
  $('#metrics').hidden = !snapshot
  const status = Object.assign({}, ...(state.status?.status || []).map(c => c.data || {}))
  const config = state.configuration
  $('#setup').hidden = Boolean(config?.active)
  text('#setup-state', config?.base_url ? '网关地址已保存，待配置独立 STATUS_TOKEN。' : '尚未配置网关地址和独立 STATUS_TOKEN。')
  text('#token-status', config?.status_token_configured ? '已配置' : '未配置')
  text('#connection', status.backend_status || status.configuration || '尚未采集')
  text('#updated', status.last_checked || snapshot?.generated_at)
  text('#endpoint', config?.base_url || '尚未配置')
  if (!$('#endpoint-form').matches(':focus-within') && !$('#endpoint-form').dataset.dirty) {
    const url = config?.base_url ? new URL(config.base_url) : null
    $('#scheme').value = url?.protocol.slice(0, -1) || 'http'; $('#host').value = url?.hostname || ''; $('#port').value = url?.port || (url?.protocol === 'https:' ? 443 : 7531)
  }
  $('#period').value = snapshot?.statistics?.overview?.period || config?.period || 'today'
  if (!snapshot) return
  const overview = snapshot.statistics?.overview
  const summary = overview?.summary || snapshot.statistics?.summary || {}
  const tokens = overview ? { total_tokens: summary.total_tokens } : summary.tokens || {}
  text('#usage-scope', overview
    ? `${periods[overview.period] || overview.period} · ${overview.timezone} · ${overview.start || '历史起点'} 至 ${overview.end}。按业务 Attempt 统计，重试分别计数。`
    : `单日辅助统计：${snapshot.statistics?.date || '未知'}（${snapshot.statistics?.timezone || '未知时区'}）。该快照未提供范围概览。`)
  const cost = summary.display_cost
  text('#cost', cost?.amount == null ? '未知' : `${cost.approximate ? '约 ' : ''}${Number(cost.amount).toLocaleString(undefined, { maximumSignificantDigits: 6 })} ${cost.currency}`)
  text('#cost-note', overview ? `参考汇率：${overview.exchange_rate_date || '未知'}；未定价调用：${count(summary.unknown_cost_calls)}${cost?.unconverted_currencies?.length ? `；无法换算：${cost.unconverted_currencies.join('、')}` : ''}` : '费用未知不代表免费')
  text('#version', `框架：${snapshot.version?.local?.version || '未知'} · 分支：${snapshot.version?.branch || '未知'} · ${snapshot.version?.status || '未检查'}`)
  text('#requests', count(summary.calls))
  text('#tokens', count(tokens.total_tokens))
  text('#phase', snapshot.runtime?.phase)
  text('#models', count(snapshot.registry?.enabled_models?.length))
  table('#providers-table', ['服务商', '启用', '已注册模型'], (snapshot.registry?.providers || []).map(p => [p.provider_id, p.enabled ? '是' : '否', (p.registered_models || []).join('、')]))
  table('#models-table', ['模型', '服务商'], (snapshot.registry?.enabled_models || []).map(p => [p.model, p.provider_id]))
  table('#upstreams-table', ['上游', 'ID', '适配器', '启用'], (snapshot.upstreams || []).map(p => [p.name, p.id, p.adapter_id, yesNo(p.enabled)]))
  table('#credentials-table', ['上游', '凭据 ID', '状态', '检查时间', '错误码', 'HTTP', '冷却秒数'], (snapshot.upstreams || []).flatMap(p => (p.credentials || []).map(c => [p.id, c.id, c.status, c.checked_at, c.error_code, c.provider_status, c.cooldown_seconds])))
  const routing = snapshot.routing
  text('#routing-options', routing ? `最大尝试：${routing.max_attempts}；默认冷却：${routing.cooldown_seconds} 秒` : '该快照未提供路由信息')
  table('#routes-table', ['部署', '公开模型', '上游', '上游模型', '启用', '优先级', '权重', 'RPM', '冷却秒数'], (routing?.deployments || []).map(d => [d.id, d.public_model, d.upstream_id, d.upstream_model, yesNo(d.enabled), d.priority, d.weight, d.rpm, d.cooldown_seconds]))
  table('#routing-models-table', ['模型', '任务', '启用', '验证状态'], (routing?.models || []).map(m => [m.model, m.task, yesNo(m.enabled), m.verification]))
  table('#plugins-table', ['插件', '版本', '启用', '状态', '模型数', '模型', '错误码', '更新版本'], (snapshot.plugins?.items || []).map(p => [p.name || p.id, p.version, yesNo(p.enabled), p.status, p.model_count, (p.models || []).join('、'), p.error_code, p.update_version]))
  const resources = snapshot.resources
  text('#resource-status', resources
    ? `待重启：${yesNo(resources.restart_required)}；${resources.update ? `缓存更新计划：${resources.update.available ? '有更新' : '未发现更新'}（${resources.update.branch}）` : '尚无缓存更新计划'}${resources.error_code ? `；${resources.error_code}` : ''}`
    : '该快照未提供资源版本')
  table('#resources-table', ['类型', '资源', '运行版本', '磁盘版本', '缓存目标版本'], Object.entries(resourceKinds).flatMap(([kind, label]) => {
    const running = resources?.running?.[kind]
    const installed = resources?.installed?.[kind]
    const group = resources?.update?.groups?.[kind]
    const ids = new Set([...Object.keys(running?.items || {}), ...Object.keys(installed?.items || {})])
    return [[label, '资源包', running?.version, installed?.version, group?.target], ...[...ids].map(id => [label, id, running?.items?.[id], installed?.items?.[id], group?.changes?.find(c => c.id === id)?.target])]
  }))
  table('#extensions-table', ['类型', '扩展', '版本', '来源', '启用', '清单有效'], Object.entries(resourceKinds).flatMap(([kind, label]) => (resources?.user?.[kind] || []).map(e => [label, e.name || e.id, e.version, e.source, yesNo(e.enabled), yesNo(e.valid)])))
  if (state.view === 'logs') void renderLogs().catch(error => text('#message', error.message))
  const rankings = overview?.rankings?.provider || snapshot.statistics?.rankings?.providers || []
  $('#chart').replaceChildren()
  const maximum = Math.max(1, ...rankings.map(p => Number(p.calls || 0)))
  for (const row of rankings) {
    const line = document.createElement('div'); line.className = 'bar-row'
    const label = document.createElement('span'); label.textContent = row.id || '—'
    const bar = document.createElement('i'); bar.style.width = `${Math.max(2, Number(row.calls || 0) / maximum * 100)}%`
    const value = document.createElement('small'); value.textContent = count(row.calls)
    line.append(label, bar, value); $('#chart').append(line)
  }
  $('#raw').textContent = JSON.stringify(snapshot.statistics || {}, null, 2)
}
async function load() {
  const [status, data] = await Promise.all([kemo.request('module.status.get'), action('dashboard_data')])
  state.status = status; state.snapshot = data.snapshot; state.configuration = data.configuration; render()
}
async function run(callback) {
  if (busy) return
  busy = true
  document.querySelectorAll('button').forEach(button => { button.disabled = true })
  $('#period').disabled = true
  text('#message', '')
  try { await callback(); await load() }
  catch (error) {
    text('#message', error.message)
    $('#period').value = state.snapshot?.statistics?.overview?.period || state.configuration?.period || 'today'
  }
  finally {
    busy = false
    document.querySelectorAll('button').forEach(button => { button.disabled = false })
    $('#period').disabled = false
  }
}
async function renderLogs() {
  const { renderRequestLogs } = await import('./requestLogs.js')
  renderRequestLogs($('#logs-table'), state.snapshot?.logs?.recent || [])
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
  state.view = button.dataset.view
  document.querySelectorAll('[data-section]').forEach(section => { section.hidden = section.dataset.section !== state.view })
  document.querySelectorAll('[data-view]').forEach(tab => tab.setAttribute('aria-selected', String(tab === button)))
  location.hash = state.view
  if (state.view === 'logs') void renderLogs().catch(error => text('#message', error.message))
}))
$('#reload').addEventListener('click', () => run(async () => {}))
$('#collect').addEventListener('click', () => run(() => action('refresh', { period: $('#period').value })))
$('#period').addEventListener('change', () => run(() => action('refresh', { period: $('#period').value })))
$('#initialize').addEventListener('click', () => run(async () => {
  const result = await action('initialize'); text('#message', result.message)
}))
document.querySelectorAll('form').forEach(form => form.addEventListener('input', () => { form.dataset.dirty = 'yes'; void kemo.setDirty(true) }))
$('#save-endpoint').addEventListener('click', () => {
  if (!$('#endpoint-form').reportValidity()) return
  void run(async () => {
    await action('configure_endpoint', { scheme: $('#scheme').value, gateway_ip: $('#host').value.trim(), gateway_port: Number($('#port').value) })
    delete $('#endpoint-form').dataset.dirty; await kemo.setDirty(Boolean(document.querySelector('form[data-dirty]'))); text('#message', '地址已保存')
  })
})
$('#activate').addEventListener('click', () => {
  if (!$('#endpoint-form').reportValidity() || !$('#activate-form').reportValidity()) return
  void run(async () => {
    const config = state.configuration
    await action('activate', {
      base_url: `${$('#scheme').value}://${$('#host').value.trim()}:${Number($('#port').value)}`,
      status_token: $('#status-token').value,
      timeout_seconds: config.timeout_seconds, ranking_limit: config.ranking_limit, log_limit: config.log_limit,
      period: $('#period').value,
    })
    $('#activate-form').reset()
    delete $('#activate-form').dataset.dirty; delete $('#endpoint-form').dataset.dirty
    await kemo.setDirty(false); text('#message', '网关连接已验证并激活')
  })
})
const context = await kemo.ready
applyTheme(context.theme)
kemo.onEvent((event, data) => {
  if (event === 'host.theme.changed') applyTheme(data)
  if (event === 'module.status.changed') { state.status = data; render() }
  if (event === 'module.error') text('#message', data.message)
})
if (location.hash) document.querySelector(`[data-view="${CSS.escape(location.hash.slice(1))}"]`)?.click()
await Promise.all([kemo.request('module.events.subscribe'), run(async () => {})])
