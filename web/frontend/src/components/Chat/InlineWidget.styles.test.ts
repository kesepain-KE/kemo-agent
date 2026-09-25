import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/components/Chat/InlineWidget.module.css'), 'utf8')

describe('InlineWidget timeline styles', () => {
  it('renders toned timeline rows as pill-shaped color layers', () => {
    const timelineRule = stylesheet.match(/\.timeline li\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(timelineRule).toContain('border-radius: 999px')
    expect(timelineRule).toContain('min-height: 42px')
    expect(timelineRule).toContain('align-content: center')
  })

  it('keeps the connector and marker aligned inside the rounded row', () => {
    const connectorRule = stylesheet.match(/\.timeline li::before\s*\{([^}]*)\}/)?.[1] ?? ''
    const markerRule = stylesheet.match(/\.timeline li > i\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(connectorRule).toContain('left: .72rem')
    expect(connectorRule).toContain('bottom: -.85rem')
    expect(markerRule).toContain('left: .3rem')
    expect(markerRule).toContain('top: .82rem')
  })
})
