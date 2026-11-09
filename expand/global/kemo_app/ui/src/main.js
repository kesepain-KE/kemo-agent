import { kemo } from '/__kemo_bridge__'
import './style.css'

const $ = selector => document.querySelector(selector)
const text = (selector, value) => { $(selector).textContent = value ?? '—' }
let busy = false
function theme(value) {
  document.documentElement.dataset.theme = value.mode
  for (const [key, color] of Object.entries(value.colors)) document.documentElement.style.setProperty(key, color)
}
async function action(id, params = {}) {
  const response = await kemo.request('module.action.invoke', { action: id, params })
  return response.result ?? response
}
function cells(row, values) {
  for (const value of values) {
    const cell = document.createElement('td')
    cell.textContent = String(value ?? '—')
    row.append(cell)
  }
}
function render(data) {
  const config = data.configuration
  const missingLabels = { 'config.json': '本地配置', device_token: '设备 Token', session_secret: '会话密钥', user_account: 'App 账户', upstream: '上游地址', valid_config: '有效配置', valid_port: '有效端口' }
  $('#setup').hidden = Boolean(data.runtime.configured)
  text('#setup-state', `${data.runtime.initialized ? '本地配置已初始化' : '尚未初始化'}；待配置：${(data.runtime.missing || []).map(field => missingLabels[field] || field).join('、') || '无'}`)
  text('#state', config.bridge_state)
  text('#endpoint', config.endpoint)
  text('#running', data.runtime.running ? '运行中' : '未运行')
  text('#devices-count', data.devices.length)
  text('#connections', data.websocket_connections)
  text('#users-count', config.configured_users)
  text('#activation', `持续运行：${config.activation}${data.runtime.unmanaged_process ? '；端口上存在未受管服务，请检查进程归属。' : ''}`)
  text('#token-status', config.token_status)
  text('#fingerprint', config.token_fingerprint)
  if (!$('#port-form').dataset.dirty) $('#port-form [name=port]').value = config.exposed_port
  $('#device-rows').replaceChildren()
  for (const device of data.devices) {
    const row = document.createElement('tr')
    cells(row, [device.user, device.device_id, device.connections, device.connected_at ? new Date(device.connected_at * 1000).toLocaleString() : '—'])
    $('#device-rows').append(row)
  }
  $('#devices-empty').hidden = data.devices.length > 0
  $('#binding-rows').replaceChildren()
  const bindings = Object.entries(config.bindings)
  for (const [app, agent] of bindings) {
    const row = document.createElement('tr')
    cells(row, [app, agent])
    const cell = document.createElement('td')
    const button = document.createElement('button')
    button.type = 'button'; button.textContent = '解除绑定'; button.disabled = busy
    button.addEventListener('click', () => run(async () => { await action('unbind_user', { app_username: app }) }))
    cell.append(button); row.append(cell); $('#binding-rows').append(row)
  }
  $('#bindings-empty').hidden = bindings.length > 0
}
async function load() { render(await action('dashboard_data')) }
async function run(callback, message = '') {
  if (busy) return
  busy = true
  document.querySelectorAll('button').forEach(button => { button.disabled = true })
  text('#message', '正在处理…')
  try { await callback(); await load(); text('#message', message) }
  catch (error) { text('#message', error.message) }
  finally {
    busy = false
    document.querySelectorAll('button').forEach(button => { button.disabled = false })
  }
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-section]').forEach(section => { section.hidden = section.dataset.section !== button.dataset.view })
  document.querySelectorAll('[data-view]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === button)))
  location.hash = button.dataset.view
}))
document.querySelectorAll('[data-action]').forEach(button => button.addEventListener('click', () => run(async () => { await action(button.dataset.action) }, '操作完成')))
$('#reload').addEventListener('click', () => run(async () => {}))
document.querySelectorAll('form').forEach(form => form.addEventListener('input', () => {
  form.dataset.dirty = 'yes'; void kemo.setDirty(true)
}))
async function save(form, id, params) {
  if (!form.reportValidity()) return
  await run(async () => {
    await action(id, params())
    delete form.dataset.dirty
    if (form.id !== 'port-form') form.reset()
    await kemo.setDirty(Boolean(document.querySelector('form[data-dirty]')))
  }, '已保存')
}
$('#save-port').addEventListener('click', () => save($('#port-form'), 'set_port', () => ({ port: Number($('#port-form [name=port]').value) })))
$('#save-token').addEventListener('click', () => save($('#token-form'), 'set_token', () => ({ token: $('#token-form [name=token]').value })))
$('#save-binding').addEventListener('click', () => save($('#binding-form'), 'bind_user', () => ({
  app_username: $('#binding-form [name=app_username]').value.trim(),
  agent_username: $('#binding-form [name=agent_username]').value.trim(),
})))
const context = await kemo.ready
theme(context.theme)
kemo.onEvent((event, data) => {
  if (event === 'host.theme.changed') theme(data)
  if (event === 'module.error') text('#message', data.message)
})
if (location.hash) document.querySelector(`[data-view="${CSS.escape(location.hash.slice(1))}"]`)?.click()
await run(async () => {})
