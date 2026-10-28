import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function stylesheet(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

function lastRule(css: string, selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))]
  return matches.at(-1)?.[1] ?? ''
}

describe('dark theme surface contracts', () => {
  it('themes the complete chat task-plan bubble instead of leaving a light prototype card', () => {
    const css = stylesheet('src/components/TaskPlanBubble.module.css')

    expect(lastRule(css, '.taskPlanBubble')).toContain('background: var(--surface-elevated)')
    expect(lastRule(css, '.taskPlanBubble')).toContain('color: var(--text)')
    expect(lastRule(css, '.stepRow')).toContain('background: var(--surface)')
    expect(lastRule(css, '.stepRow')).toContain('border-color: var(--line)')
    expect(lastRule(css, '.progressSection')).toContain('background: var(--surface-2)')
    expect(lastRule(css, '.collapsedSummary')).toContain('background: var(--surface-2)')
    expect(lastRule(css, '.modifyButton')).toContain('background: var(--surface)')
    expect(lastRule(css, '.bottomPointer')).toContain('background: var(--surface-elevated)')
    expect(lastRule(css, '.stepList')).toContain('max-height: min(26dvh, 250px)')
    expect(lastRule(css, '.stepList')).toContain('overflow-y: auto')
  })

  it('uses the current light/dark palette and only the current UI stylesheet chain', () => {
    const tokens = stylesheet('src/reference/styles/tokens.css')
    expect(tokens).toContain('[data-theme="dark"]')
    for (const variable of ['--bg-surface:', '--text-primary:']) {
      expect(tokens.split(variable).length - 1).toBeGreaterThanOrEqual(2)
    }
    const main = stylesheet('src/main.tsx')
    expect(main).toContain("import './reference/styles/index.css'")
    expect(main).not.toMatch(/styles\/(app|prototype)\.css/)
    const entry = stylesheet('src/reference/styles/index.css')
    expect(entry).toContain("@import './tokens.css'")
    expect(entry).toContain("@import './production.css'")
  })
})
