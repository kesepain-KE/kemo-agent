import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect, it } from 'vitest'

it('侧边栏悬停和键盘聚焦不显示链接下划线，不影响正文链接', () => {
  const production = readFileSync(resolve(process.cwd(), 'src/reference/styles/production.css'), 'utf8')
  const rule = production.match(/\.sidebar \.nav-item:hover, \.sidebar \.nav-item:focus-visible\s*\{([^}]*)\}/)?.[1] ?? ''
  expect(rule).toContain('text-decoration: none')
  const base = readFileSync(resolve(process.cwd(), 'src/reference/styles/base.css'), 'utf8')
  expect(base).toContain('a:hover { text-decoration: underline')
})
