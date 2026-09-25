import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/pages/SensePage.module.css'), 'utf8')

describe('SensePage module panel spacing', () => {
  it('keeps the user configuration host away from both outer container walls', () => {
    const hostRule = stylesheet.match(/\.panelHost\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(hostRule).toContain('width: calc(100% - 1.6rem)')
    expect(hostRule).toContain('max-width: calc(100% - 1.6rem)')
    expect(hostRule).toContain('margin: 0 .8rem .8rem')
    expect(hostRule).toContain('box-sizing: border-box')
  })
})
