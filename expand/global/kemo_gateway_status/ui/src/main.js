import { kemo } from '/__kemo_bridge__'
import './style.css'

const $ = selector => document.querySelector(selector)
const state = { snapshot: null, status: null, view: 'overview' }
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
  text('#connection', status.backend_status || status.configuration || '尚未采集')
  text('#updated', status.last_checked || snapshot?.generated_at)
  text('#endpoint', status.base_url)
  if (!$('#endpoint-form').matches(':focus-within') && !$('#endpoint-form').dataset.dirty) {
    $('#scheme').value = status.scheme || 'http'; $('#host').value = status.gateway_ip || ''; $('#port').value = status.gateway_port || 7531
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
  state.status = status; state.snapshot = data.snapshot; render()
}
async function run(button, callback) {
  button.disabled = true; text('#message', '')
  try { await callback(); await load() }
  catch (error) { text('#message', error.message) }
  finally { button.disabled = false }
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
$('#reload').addEventListener('click', event => run(event.currentTarget, load))
$('#collect').addEventListener('click', event => run(event.currentTarget, () => action('refresh')))
$('#endpoint-form').addEventListener('input', () => { $('#endpoint-form').dataset.dirty = 'yes'; void kemo.setDirty(true) })
$('#save-endpoint').addEventListener('click', () => {
  if (!$('#endpoint-form').reportValidity()) return
  void run($('#save-endpoint'), async () => {
    await action('configure_endpoint', { scheme: $('#scheme').value, gateway_ip: $('#host').value.trim(), gateway_port: Number($('#port').value) })
    delete $('#endpoint-form').dataset.dirty; await kemo.setDirty(false); text('#message', '地址已保存')
  })
})
const context = await kemo.ready
applyTheme(context.theme)
kemo.onEvent((event, data) => {
  if (event === 'host.theme.changed') applyTheme(data)
  if (event === 'module.status.changed') { state.status = data; render() }
  if (event === 'module.error') text('#message', data.message)
})
await kemo.request('module.events.subscribe')
await run($('#reload'), load)
