import { kemo } from '/__kemo_bridge__'
import './style.css'

const $ = selector => document.querySelector(selector)
const text = (selector, value) => { $(selector).textContent = value ?? '—' }
let busy = false
let configuration = null
let liveStatus = []
function theme(value) {
  document.documentElement.dataset.theme = value.mode
  for (const [key, color] of Object.entries(value.colors)) document.documentElement.style.setProperty(key, color)
}
async function action(id, params = {}) {
  const response = await kemo.request('module.action.invoke', { action: id, params })
  return response.result ?? response
}
function render(status) {
  const data = Object.assign({}, ...status.status.map(container => container.data))
  $('#setup').hidden = Boolean(configuration.active)
  text('#save-endpoint', configuration.active ? '保存连接地址' : '初始化本地配置')
  text('#connection', data.backend_status || data.configuration || '未配置')
  text('#endpoint', configuration.base_url)
  text('#library-count', configuration.libraries.length)
  text('#enabled-count', configuration.libraries.filter(library => library.enabled).length)
  text('#checked', data.last_checked || '尚未检测')
  text('#admin-note', !configuration.active ? '首次初始化将以当前用户创建模块管理员和内置文档库权限。' : configuration.caller_is_admin ? '当前用户可以管理此图谱连接。' : '当前用户没有图谱管理权限。')
  $('#empty').hidden = configuration.libraries.length > 0
  const select = $('#query-form [name=library]')
  const previous = select.value
  select.replaceChildren(new Option('全部可访问文档库', ''))
  $('#library-rows').replaceChildren()
  for (const library of configuration.libraries) {
    if (library.enabled) select.append(new Option(library.display_name, library.id))
    const current = liveStatus.find(row => row.id === library.id)
    const row = document.createElement('tr')
    for (const value of [library.display_name, library.kind, library.enabled ? '已启用' : '未启用', current?.status || '尚未查询', current ? `${current.pending_graph ?? '—'} / ${current.pending_rag ?? '—'}` : '—']) {
      const cell = document.createElement('td'); cell.textContent = String(value); row.append(cell)
    }
    $('#library-rows').append(row)
  }
  if ([...select.options].some(option => option.value === previous)) select.value = previous
  if (!$('#endpoint-form').dataset.dirty && configuration.active) {
    const url = new URL(configuration.base_url)
    $('#endpoint-form [name=scheme]').value = url.protocol.slice(0, -1)
    $('#endpoint-form [name=host]').value = url.hostname
    $('#endpoint-form [name=port]').value = url.port || (url.protocol === 'https:' ? 443 : 80)
    $('#endpoint-form [name=remote]').checked = configuration.allow_remote
  }
}
async function load() {
  const [status, config] = await Promise.all([kemo.request('module.status.get'), action('libraries')])
  configuration = config; render(status)
}
function setBusy(value) {
  busy = value
  document.querySelectorAll('button').forEach(button => {
    button.disabled = value
      || (button.hasAttribute('data-configured') && !configuration?.active)
      || (button.hasAttribute('data-admin') && configuration?.active && !configuration.caller_is_admin)
  })
}
async function run(callback, message = '') {
  if (busy) return
  setBusy(true); text('#message', '正在处理…')
  try { await callback(); await load(); text('#message', message) }
  catch (error) { text('#message', error.message) }
  finally { setBusy(false) }
}
function showResult(result) {
  $('#result-details').hidden = false
  text('#result', JSON.stringify(result, null, 2))
}
document.querySelectorAll('[data-view]').forEach(button => button.addEventListener('click', () => {
  document.querySelectorAll('[data-section]').forEach(section => { section.hidden = section.dataset.section !== button.dataset.view })
  document.querySelectorAll('[data-view]').forEach(tab => tab.setAttribute('aria-pressed', String(tab === button)))
  location.hash = button.dataset.view
}))
$('#reload').addEventListener('click', () => run(async () => {}))
$('#check-backend').addEventListener('click', () => run(async () => { showResult(await action('check_backend')) }, '检测完成'))
$('#library-status').addEventListener('click', () => run(async () => {
  const result = await action('status')
  liveStatus = result.libraries || []
  showResult(result)
}, '状态已更新'))
$('#collect').addEventListener('click', () => run(async () => { showResult(await action('refresh')) }, '采集已更新'))
$('#query-submit').addEventListener('click', () => {
  const form = $('#query-form')
  if (!form.reportValidity()) return
  void run(async () => {
    const library = form.elements.library.value
    const result = await action('query', { query: form.elements.query.value.trim(), ...(library ? { library_ids: [library] } : {}) })
    text('#query-result', JSON.stringify(result, null, 2))
  }, '检索完成')
})
$('#endpoint-form').addEventListener('input', () => { $('#endpoint-form').dataset.dirty = 'yes'; void kemo.setDirty(true) })
$('#query-form').addEventListener('input', () => { $('#query-form').dataset.dirty = 'yes'; void kemo.setDirty(true) })
$('#save-endpoint').addEventListener('click', () => {
  const form = $('#endpoint-form')
  if (!form.reportValidity()) return
  void run(async () => {
    if (configuration.active) {
      await action('configure_endpoint', {
        scheme: form.elements.scheme.value, graph_ip: form.elements.host.value.trim(),
        graph_port: Number(form.elements.port.value), allow_remote: form.elements.remote.checked,
      })
    } else {
      await action('initialize_config', {
        base_url: `${form.elements.scheme.value}://${form.elements.host.value.trim()}:${Number(form.elements.port.value)}/api/v1`,
        allow_remote: form.elements.remote.checked,
      })
    }
    delete form.dataset.dirty
    await kemo.setDirty(Boolean($('#query-form').dataset.dirty))
  }, '连接已保存')
})
const context = await kemo.ready
theme(context.theme)
kemo.onEvent((event, data) => {
  if (event === 'host.theme.changed') theme(data)
  if (event === 'module.error') text('#message', data.message)
})
if (location.hash) document.querySelector(`[data-view="${CSS.escape(location.hash.slice(1))}"]`)?.click()
await run(async () => {})
