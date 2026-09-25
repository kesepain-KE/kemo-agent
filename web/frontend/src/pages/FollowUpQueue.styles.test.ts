import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const stylesheet = readFileSync(resolve(process.cwd(), 'src/pages/FollowUpQueue.module.css'), 'utf8')

describe('FollowUpQueue height budget styles', () => {
  it('caps the queue at the shared 2.5-card budget', () => {
    const rule = stylesheet.match(/\.queue ol\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(rule).toContain('max-height: var(--composer-follow-up-max, 26.25rem)')
    expect(rule).toContain('overflow-y: auto')
    expect(rule).toContain('overscroll-behavior: contain')
    expect(rule).toContain('scrollbar-gutter: stable')
  })

  it('caps one card without introducing a nested vertical scroller', () => {
    const bubbleRule = stylesheet.match(/\.bubble\s*\{([^}]*)\}/)?.[1] ?? ''
    const bodyRule = stylesheet.match(/\.body\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(bubbleRule).toContain('max-height: var(--composer-follow-up-max, 26.25rem)')
    expect(bubbleRule).toContain('overflow: hidden')
    expect(bodyRule).toContain('min-height: 0')
    expect(bodyRule).toContain('overflow: hidden')
    expect(bodyRule).not.toContain('overflow-y: auto')
  })
})
