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
