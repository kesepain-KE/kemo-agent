import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const businessPages = {
  tasks: 'TasksPage', knowledge: 'KnowledgePage', memory: 'MemoryPage',
  agents: 'AgentsPage', skills: 'SkillsPage', sense: 'SensePage',
  expand: 'ExpandPage', messages: 'MessagesPage', status: 'RuntimeStatusPage',
  runtime: 'RuntimeModulesPage',
}
const failures = []
const router = readFileSync(join(root, 'src/router.tsx'), 'utf8')
for (const [route, name] of Object.entries(businessPages)) {
  const line = router.split('\n').find(line => line.includes(`path: '${route}'`))
  if (!line?.includes("import('./reference/ReferencePage')")) {
    failures.push(`${route}: production must load the current reference/ReferencePage UI`)
  }
  for (const suffix of ['.tsx', '.module.css']) {
    if (existsSync(join(root, `src/pages/${name}${suffix}`))) {
      failures.push(`obsolete duplicate must not be restored: src/pages/${name}${suffix}`)
    }
  }
}
for (const path of [
  'src/pages/FilesPage.tsx', 'src/pages/FilesPage.module.css',
  'src/pages/filesPageSupport.tsx', 'src/pages/TasksPageTheme.module.css',
  'src/pages/RuntimeLogsPanel.tsx', 'src/pages/RuntimeLogsPanel.module.css',
  'src/pages/ImportantMemoryStatus.tsx', 'src/styles/app.css', 'src/styles/prototype.css',
]) {
  if (existsSync(join(root, path))) failures.push(`obsolete UI must not be restored: ${path}`)
}
const main = readFileSync(join(root, 'src/main.tsx'), 'utf8')
if (!main.includes("import './reference/styles/index.css'")) failures.push('main must use current UI styles')
if (!router.includes("import('./reference/FilesPage')")) failures.push('files must use the current FilesPage')
for (const name of ['ChatPage', 'ProfilePage', 'SettingsPage']) {
  if (!router.includes(`import('./pages/${name}')`)) failures.push(`shared current page lost: ${name}`)
}
if (process.argv.includes('--dist')) {
  const dist = join(root, 'dist')
  if (!existsSync(join(dist, 'index.html'))) failures.push('dist/index.html is missing')
  const assets = existsSync(join(dist, 'assets')) ? readdirSync(join(dist, 'assets')) : []
  for (const name of Object.values(businessPages)) {
    if (assets.some(asset => asset.startsWith(`${name}-`) && /\.(js|css)$/.test(asset))) {
      failures.push(`obsolete page remains in build: ${name}`)
    }
  }
  if (!assets.some(asset => /^ReferencePage-.*\.js$/.test(asset))) failures.push('current ReferencePage chunk is missing')
}
if (failures.length) {
  console.error(`Current UI guard failed:\n${failures.map(message => `- ${message}`).join('\n')}`)
  process.exitCode = 1
} else {
  console.log(`Current UI guard passed: ${Object.keys(businessPages).length} current business routes; no obsolete duplicate UI${process.argv.includes('--dist') ? ' or build chunks' : ''}.`)
}
