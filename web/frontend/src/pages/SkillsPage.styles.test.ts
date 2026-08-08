import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/pages/SkillsPage.module.css'), 'utf8')

describe('SkillsPage editor styles', () => {
  it('overrides the global textarea height cap inside the Markdown editor', () => {
    const rule = stylesheet.match(/\.documentContent textarea\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(rule).toContain('height: 100%')
    expect(rule).toContain('max-height: none')
    expect(rule).toContain('box-sizing: border-box')
    expect(rule).toContain('overflow: auto')
  })

  it('keeps preview scrolling inside the same fixed document viewport', () => {
    const containerRule = stylesheet.match(/\.documentContent\s*\{([^}]*)\}/)?.[1] ?? ''
    const previewRule = stylesheet.match(/\.markdown\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(containerRule).toContain('overflow: hidden')
    expect(previewRule).toContain('height: 100%')
    expect(previewRule).toContain('overflow: auto')
  })
})
