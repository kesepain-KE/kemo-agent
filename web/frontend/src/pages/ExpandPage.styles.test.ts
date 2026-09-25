import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/pages/ExpandPage.module.css'), 'utf8')

describe('ExpandPage user configuration panel styles', () => {
  it('stretches the panel while preserving an inset between nested and outer borders', () => {
    const hostRule = stylesheet.match(/\.panelHost\s*\{([^}]*)\}/)?.[1] ?? ''
    const insetRule = stylesheet.match(/\.panelInset\s*\{([^}]*)\}/)?.[1] ?? ''
    const spacerRule = stylesheet.match(/\.panelInset::after\s*\{([^}]*)\}/)?.[1] ?? ''
    const childRule = stylesheet.match(/\.panelInset > \*\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(hostRule).toContain('display: grid')
    expect(hostRule).toContain('grid-template-rows: minmax(0, 1fr)')
    expect(hostRule).toContain('align-items: stretch')
    expect(hostRule).toContain('padding: 0')
    expect(hostRule).toContain('box-sizing: border-box')
    expect(insetRule).toContain('padding: .65rem .65rem 0')
    expect(insetRule).toContain('box-sizing: border-box')
    expect(insetRule).toContain('min-height: 100%')
    expect(insetRule).toContain('grid-template-rows: minmax(min-content, 1fr) .65rem')
    expect(spacerRule).toContain("content: ''")
    expect(spacerRule).toContain('min-height: .65rem')
    expect(childRule).toContain('min-width: 0')
    expect(childRule).toContain('min-height: 0')
    expect(childRule).toContain('align-self: stretch')
  })
})
