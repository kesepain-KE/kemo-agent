import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/styles/app.css'), 'utf8')

describe('ChatPage guidance styles', () => {
  it('contains long guidance text inside the conversation width', () => {
    const rules = [...stylesheet.matchAll(/\.guidance-message\s*\{([^}]*)\}/g)]
    const rule = rules.map((match) => match[1]).find((value) => value.includes('min-width: 0')) ?? ''

    expect(rule).toContain('min-width: 0')
    expect(rule).toContain('max-width: 100%')
    expect(rule).toContain('overflow: hidden')
  })

  it('scrolls unbroken guidance content horizontally inside its own bubble', () => {
    const rule = stylesheet.match(/\.guidance-message > strong\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(rule).toContain('min-width: 0')
    expect(rule).toContain('max-width: 100%')
    expect(rule).toContain('overflow-x: auto')
    expect(rule).toContain('white-space: pre')
    expect(rule).toContain('scrollbar-width: thin')
  })
})

describe('ChatPage tool-call theme styles', () => {
  it('uses theme surfaces throughout the expanded tool card', () => {
    const bodyRule = stylesheet.match(/\.tool-call-body\s*\{([^}]*)\}/g)?.at(-1) ?? ''
    const panelRule = stylesheet.match(/\.tool-call-panel pre\s*\{([^}]*)\}/g)?.at(-1) ?? ''
    const labelRule = stylesheet.match(/\.tool-call-panel label\s*\{([^}]*)\}/g)?.at(-1) ?? ''

    expect(bodyRule).toContain('background: var(--surface)')
    expect(bodyRule).toContain('border-top-color: var(--line)')
    expect(bodyRule).not.toMatch(/#fff|white/i)
    expect(panelRule).toContain('background: var(--surface-2)')
    expect(panelRule).toContain('color: var(--text-2)')
    expect(panelRule).toContain('border-color: var(--line)')
    expect(labelRule).toContain('color: var(--muted)')
  })
})

describe('ChatPage active plan dock styles', () => {
  it('leaves scrolling to the task step list instead of clipping the whole plan card', () => {
    const dockRule = stylesheet.match(/\.composer-plan-dock\s*\{([^}]*)\}/g)?.at(-1) ?? ''

    expect(dockRule).toContain('max-height: none')
    expect(dockRule).toContain('align-items: flex-start')
    expect(dockRule).toContain('overflow: visible')
  })
})
