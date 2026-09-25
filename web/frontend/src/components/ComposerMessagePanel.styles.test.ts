import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/components/ComposerMessagePanel.module.css'), 'utf8')

describe('ComposerMessagePanel boundary styles', () => {
  it('draws a distinct container around follow-up and guidance content', () => {
    const panelRule = stylesheet.match(/\.panel\s*\{([^}]*)\}/)?.[1] ?? ''
    const contentRule = stylesheet.match(/\.content\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(panelRule).toContain('border: 1px solid')
    expect(panelRule).toContain('border-radius: 16px')
    expect(panelRule).toContain('background:')
    expect(panelRule).toContain('box-shadow: var(--shadow-soft)')
    expect(contentRule).toContain('border-top: 1px solid')
  })
})
