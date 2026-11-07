import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function stylesheet(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

function hasDeclarations(css: string, selector: string, declarations: string[]) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))]
  return matches.some((match) => declarations.every((declaration) => match[1].includes(declaration)))
}

describe('dark theme surface contracts', () => {
  it('themes the complete chat task-plan bubble instead of leaving a light prototype card', () => {
    const css = stylesheet('src/components/TaskPlanBubble.module.css')

    expect(hasDeclarations(css, '.taskPlanBubble', [
      'background: var(--surface-elevated)', 'color: var(--text)',
    ])).toBe(true)
    expect(hasDeclarations(css, '.stepRow', [
      'background: var(--surface)', 'border-color: var(--line)',
    ])).toBe(true)
    expect(hasDeclarations(css, '.progressSection', ['background: var(--surface-2)'])).toBe(true)
    expect(hasDeclarations(css, '.collapsedSummary', ['background: var(--surface-2)'])).toBe(true)
    expect(hasDeclarations(css, '.modifyButton', ['background: var(--surface)'])).toBe(true)
    expect(hasDeclarations(css, '.bottomPointer', ['background: var(--surface-elevated)'])).toBe(true)
    expect(hasDeclarations(css, '.stepList', [
      'max-height: min(26dvh, 250px)', 'overflow-y: auto',
    ])).toBe(true)
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
