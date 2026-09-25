import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/pages/FilesPage.module.css'), 'utf8')

function rule(selector: string): string {
  return stylesheet.match(new RegExp(`${selector}\\s*\\{([^}]*)\\}`))?.[1] ?? ''
}

describe('FilesPage sorting typography', () => {
  it('uses the same compact font scale as the surrounding file-list controls', () => {
    const trigger = rule('\\.sortTrigger')
    const value = rule('\\.sortTrigger strong')
    const option = rule('\\.sortPopover button')

    for (const declaration of [trigger, value, option]) {
      expect(declaration).toContain('font: inherit')
      expect(declaration).toContain('font-size: .625rem')
    }
    expect(trigger).toContain('font-weight: 500')
    expect(value).toContain('font-weight: 500')
    expect(option).toContain('font-weight: 500')
    expect(option).toContain('line-height: 1.25')
    expect(stylesheet).not.toMatch(/font:\s*\d+\s+[^;]+\s+inherit;/)
  })
})
