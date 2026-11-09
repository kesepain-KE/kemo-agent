import { kemo } from '/__kemo_bridge__'
import './style.css'

const $ = selector => document.querySelector(selector)
let configRevision = ''
let dirty = false
let loading = false
const text = (selector, value) => { $(selector).textContent = value ?? '—' }
function theme(value) {
  document.documentElement.dataset.theme = value.mode
  for (const [key, color] of Object.entries(value.colors)) document.documentElement.style.setProperty(key, color)
}
async function loadConfig() {
  const response = await kemo.request('module.config.get')
  configRevision = response.config_revision
  const container = response.panel?.containers.find(c => c.kind === 'config' && c.fields.some(f => f.key === 'timezone'))
  const field = container?.fields.find(f => f.key === 'timezone')
  if (!field) throw new Error('模块未提供 timezone 配置字段')
  $('#zone').replaceChildren()
  for (const entry of field.options) {
    const option = document.createElement('option'); option.value = entry.value; option.textContent = entry.label; $('#zone').append(option)
  }
  $('#zone').value = container.current_values.timezone
}
async function load() {
  if (loading) return
  loading = true
  try {
    const response = await kemo.request('module.action.invoke', { action: 'clock_data', params: {} })
    text('#timezone', response.timezone); text('#time', response.current.time)
    text('#date', `${response.current.date} · ${response.current.weekday} · ${response.current.iso_week}`)
    text('#beijing', response.current.beijing); text('#utc', response.current.utc)
    text('#health', response.last_collection.health); text('#last', response.last_collection.last)
    text('#offset', response.current.offset)
    text('#data', JSON.stringify(response, null, 2))
    if (response.note) text('#message', response.note)
  } finally { loading = false }
}
async function perform(button, action) {
  button.disabled = true; text('#message', '')
  try { await action(); await load() }
  catch (error) { text('#message', error.message) }
  finally { button.disabled = false }
}
$('#zone').addEventListener('change', () => {
  dirty = true; text('#dirty', '未保存'); void kemo.setDirty(true)
})
$('#save').addEventListener('click', () => {
  void perform($('#save'), async () => {
    await kemo.request('module.config.patch', { values: { timezone: $('#zone').value }, clear_secrets: [], config_revision: configRevision })
    dirty = false; text('#dirty', '已保存'); await kemo.setDirty(false); await loadConfig()
  })
})
$('#collect').addEventListener('click', () => { void perform($('#collect'), () => kemo.request('module.action.invoke', { action: 'refresh', params: {} })) })
const context = await kemo.ready
theme(context.theme)
kemo.onEvent((event, value) => {
  if (event === 'host.theme.changed') theme(value)
  if (event === 'module.status.changed') { void load().catch(error => text('#message', error.message)) }
  if (event === 'module.error') text('#message', value.message)
})
try { await loadConfig(); await load(); await kemo.request('module.events.subscribe') }
catch (error) { text('#message', error.message) }
window.addEventListener('beforeunload', event => { if (dirty) event.preventDefault() })
