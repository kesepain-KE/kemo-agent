import { execFileSync } from 'node:child_process'
import { copyFileSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'

const fixtures: string[] = []

function fixture() {
  const root = mkdtempSync(join(tmpdir(), 'kemo-ui-guard-'))
  fixtures.push(root)
  mkdirSync(join(root, 'scripts'))
  mkdirSync(join(root, 'src/pages'), { recursive: true })
  copyFileSync('scripts/check-ui-entry.mjs', join(root, 'scripts/check-ui-entry.mjs'))
  for (const name of ['router.tsx', 'main.tsx']) copyFileSync(`src/${name}`, join(root, 'src', name))
  return root
}

function runGuard(root: string, ...args: string[]) {
  return execFileSync(process.execPath, ['scripts/check-ui-entry.mjs', ...args], {
    cwd: root, encoding: 'utf8', stdio: 'pipe',
  })
}

afterEach(() => {
  for (const directory of fixtures.splice(0)) {
    const target = resolve(directory)
    // Remove only this test's uniquely-created immediate child of the OS temp directory.
    if (dirname(target) !== resolve(tmpdir()) || !target.split(/[\\/]/).at(-1)?.startsWith('kemo-ui-guard-')) {
      throw new Error(`Refusing unsafe fixture cleanup: ${target}`)
    }
    rmSync(target, { recursive: true, force: true })
  }
})

describe('current UI source boundary', () => {
  it('rejects obsolete duplicate pages and keeps the current production entry', () => {
    const output = runGuard(process.cwd())
    expect(output).toContain('Current UI guard passed')
  })

  it('fails if an obsolete page implementation is reintroduced', () => {
    const root = fixture()
    writeFileSync(join(root, 'src/pages/TasksPage.tsx'), 'export const TasksPage = () => null')
    expect(() => runGuard(root)).toThrow(/obsolete duplicate must not be restored/)
  })

  it('fails if a production business route is redirected away from the current UI', () => {
    const root = fixture()
    const path = join(root, 'src/router.tsx')
    writeFileSync(path, readFileSync(path, 'utf8').replace("import('./reference/ReferencePage')", "import('./pages/TasksPage')"))
    expect(() => runGuard(root)).toThrow(/tasks: production must load the current/)
  })

  it('fails if the obsolete global stylesheet is restored', () => {
    const root = fixture()
    mkdirSync(join(root, 'src/styles'))
    writeFileSync(join(root, 'src/styles/app.css'), 'body { color: red }')
    expect(() => runGuard(root)).toThrow(/obsolete UI must not be restored/)
  })

  it('fails if obsolete page chunks survive in the production build', () => {
    const root = fixture()
    mkdirSync(join(root, 'dist/assets'), { recursive: true })
    writeFileSync(join(root, 'dist/index.html'), '<html></html>')
    writeFileSync(join(root, 'dist/assets/ReferencePage-current.js'), '')
    writeFileSync(join(root, 'dist/assets/TasksPage-obsolete.js'), '')
    expect(() => runGuard(root, '--dist')).toThrow(/obsolete page remains in build/)
  })
})
