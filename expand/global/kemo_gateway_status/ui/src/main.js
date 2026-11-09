import { kemo } from '/__kemo_bridge__'
import './style.css'

const $ = selector => document.querySelector(selector)
const state = { snapshot: null, status: null, configuration: null, view: 'overview' }
let busy = false
const count = value => Number(value || 0).toLocaleString()
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
  if (!snapshot) return
  const summary = snapshot.statistics?.summary || {}
  const tokens = summary.tokens || {}
  text('#requests', count(summary.calls))
  text('#tokens', count(tokens.total_tokens))
  text('#phase', snapshot.runtime?.phase)
  text('#models', count(snapshot.registry?.enabled_models?.length))
  table('#providers-table', ['服务商', '启用', '已注册模型'], (snapshot.registry?.providers || []).map(p => [p.provider_id, p.enabled ? '是' : '否', (p.registered_models || []).join('、')]))
  table('#models-table', ['模型', '服务商'], (snapshot.registry?.enabled_models || []).map(p => [p.model, p.provider_id]))
  if (state.view === 'logs') void renderLogs().catch(error => text('#message', error.message))
  const rankings = snapshot.statistics?.rankings?.providers || []
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
  text('#message', '')
  try { await callback(); await load() }
  catch (error) { text('#message', error.message) }
  finally {
    busy = false
    document.querySelectorAll('button').forEach(button => { button.disabled = false })
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
$('#collect').addEventListener('click', () => run(() => action('refresh')))
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
