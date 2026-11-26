import { kemo } from './__kemo__/bridge.js'
import { mountAdmin } from './admin.js'

const context = await kemo.ready
function applyTheme(value) {
  document.documentElement.style.colorScheme = value.mode
  for (const [key, color] of Object.entries(value.colors)) document.documentElement.style.setProperty(key, color)
}
applyTheme(context.theme)
kemo.onEvent((event, data) => { if (event === 'host.theme.changed') applyTheme(data) })
mountAdmin(kemo)
