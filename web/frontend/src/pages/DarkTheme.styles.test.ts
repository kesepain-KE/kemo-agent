import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function stylesheet(path: string) {
  return readFileSync(resolve(process.cwd(), path), 'utf8')
}

function lastRule(css: string, selector: string) {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const matches = [...css.matchAll(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`, 'g'))]
  return matches.at(-1)?.[1] ?? ''
}

describe('dark theme surface contracts', () => {
  it('themes the complete chat task-plan bubble instead of leaving a light prototype card', () => {
    const css = stylesheet('src/components/TaskPlanBubble.module.css')

    expect(lastRule(css, '.taskPlanBubble')).toContain('background: var(--surface-elevated)')
    expect(lastRule(css, '.taskPlanBubble')).toContain('color: var(--text)')
    expect(lastRule(css, '.stepRow')).toContain('background: var(--surface)')
    expect(lastRule(css, '.stepRow')).toContain('border-color: var(--line)')
    expect(lastRule(css, '.progressSection')).toContain('background: var(--surface-2)')
    expect(lastRule(css, '.collapsedSummary')).toContain('background: var(--surface-2)')
    expect(lastRule(css, '.modifyButton')).toContain('background: var(--surface)')
    expect(lastRule(css, '.bottomPointer')).toContain('background: var(--surface-elevated)')
    expect(lastRule(css, '.stepList')).toContain('max-height: min(26dvh, 250px)')
    expect(lastRule(css, '.stepList')).toContain('overflow-y: auto')
  })

  it('keeps task page chips, details and read-only badges on theme tokens', () => {
    const css = stylesheet('src/pages/TasksPage.module.css')

    expect(lastRule(css, '.stepChip')).toContain('color: var(--text-2)')
    expect(lastRule(css, '.stepChip')).toContain('border-color: var(--line)')
    expect(lastRule(css, '.stepChip b')).toContain('background: var(--surface-elevated)')
    expect(lastRule(css, '.activeStep')).toContain('background: var(--brand-soft)')
    expect(lastRule(css, '.readonlyBadge')).toContain('background: var(--brand-soft)')
    expect(lastRule(css, '.detailBody dd')).toContain('color: var(--text-2)')
  })

  it('derives status and category tint backgrounds from the active surface', () => {
    const runtime = stylesheet('src/pages/RuntimeStatusPage.module.css')
    const expand = stylesheet('src/pages/ExpandPage.module.css')
    const sense = stylesheet('src/pages/SensePage.module.css')
    const messages = stylesheet('src/pages/MessagesPage.module.css')
    const skills = stylesheet('src/pages/SkillsPage.module.css')

    for (const rule of [
      lastRule(runtime, '.tone_warning'),
      lastRule(runtime, '.tone_danger'),
      lastRule(expand, '.summaryIcon.blue'),
      lastRule(expand, '.moduleIcon.shared'),
      lastRule(sense, '.summaryIcon.blue'),
      lastRule(messages, '.summaryIcon.blue'),
      lastRule(skills, '.categoryTag.builtin'),
      lastRule(skills, '.categoryTag.shared'),
      lastRule(skills, '.categoryTag.agent_generated'),
      lastRule(skills, '.categoryTag.user_created'),
    ]) {
      expect(rule).toContain('color-mix(in srgb')
      expect(rule).toContain('var(--surface)')
    }
  })
})
