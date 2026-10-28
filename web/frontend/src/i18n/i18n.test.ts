import { afterEach, describe, expect, it, vi } from 'vitest'
import { coreMessages } from './core'
import { appearanceMessages } from './appearance'
import { workspaceMessages } from './workspace'
import { backgroundMessages } from './background'
import { workbenchMessages } from './workbench'
import { agentDetailMessages } from './agentDetails'
import { conversationRecordMessages } from './conversationRecords'
import { historyMarkMessages } from './historyMarks'
import { scratchMessages } from './scratch'
import { agentBudgetMessages } from './agentBudgets'
import { presenceDispatchMessages } from './presenceDispatch'
import { languages, messages, normalizeLanguage, translate } from './index'
import { useUiStore } from '../store/ui'
import { h } from '../reference/shared.js'

afterEach(() => {
  vi.restoreAllMocks()
  useUiStore.getState().setLanguage('zh-CN', '')
})

describe('explicit UI localization', () => {
  it('provides exactly nine languages with complete, unique catalog rows and matching placeholders', () => {
    expect(languages).toHaveLength(9)
    const seen = new Set<string>()
    const placeholders = (value: string) => [...value.matchAll(/\{(\w+)\}/g)].map(match => match[1]).sort()
    for (const group of [coreMessages, appearanceMessages, workspaceMessages, backgroundMessages, workbenchMessages, agentDetailMessages, conversationRecordMessages, historyMarkMessages, scratchMessages, agentBudgetMessages, presenceDispatchMessages]) {
      for (const row of group.trim().split('\n')) {
        const columns = row.split('|')
        expect(columns, row).toHaveLength(9)
        expect(seen.has(columns[0]), `duplicate ${columns[0]}`).toBe(false)
        seen.add(columns[0])
        for (const value of columns.slice(1)) {
          expect(value.trim(), row).not.toBe('')
          expect(placeholders(value), row).toEqual(placeholders(columns[0]))
        }
      }
    }
    expect(messages.size).toBe(seen.size)
  })

  it.each(languages)('translates configured UI keys for $code with source fallback', ({ code }) => {
    expect(translate('网页语言', code)).toBeTruthy()
    expect(translate('not-in-catalog', code)).toBe('not-in-catalog')
    expect(translate('引用 {title}', code, { title: '保存 <script>{count}' })).toContain('保存 <script>{count}')
  })

  it('normalizes unknown values and does not translate arbitrary DOM or user content', () => {
    expect(normalizeLanguage('unsupported')).toBe('zh-CN')
    expect(normalizeLanguage(null)).toBe('zh-CN')
    useUiStore.getState().setLanguage('en', 'alice')
    expect(translate('保存', 'en')).toBe('Save')
    // A filename/message equal to an existing UI key remains verbatim.
    expect(h('div', '保存').textContent).toBe('保存')
    expect((h('input', { value: '保存', title: '永久记忆' }) as HTMLInputElement).value).toBe('保存')
    expect(h('div', '用户层').textContent).toBe('用户层')
  })

  it('isolates the cache by user and restores defaults', () => {
    localStorage.removeItem('kemo-language:bob')
    useUiStore.getState().setLanguage('de', 'alice')
    expect(document.documentElement).toHaveAttribute('lang', 'de')
    expect(document.documentElement).toHaveAttribute('dir', 'ltr')
    useUiStore.getState().activateLanguage('bob')
    expect(useUiStore.getState().language).toBe('zh-CN')
    expect(document.documentElement.dir).toBe('ltr')
    useUiStore.getState().activateLanguage('alice')
    expect(useUiStore.getState().language).toBe('de')
    expect(useUiStore.getState().languageUser).toBe('alice')
  })

  it('falls back from retired Arabic caches and resets stale RTL layout', () => {
    expect(normalizeLanguage('ar')).toBe('zh-CN')
    localStorage.setItem('kemo-language:alice', 'ar')
    document.documentElement.lang = 'ar'
    document.documentElement.dir = 'rtl'
    useUiStore.getState().activateLanguage('alice')
    expect(useUiStore.getState().language).toBe('zh-CN')
    expect(document.documentElement).toHaveAttribute('lang', 'zh-CN')
    expect(document.documentElement).toHaveAttribute('dir', 'ltr')
    localStorage.removeItem('kemo-language:alice')
  })

  it('survives disabled browser storage', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('disabled') })
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('disabled') })
    expect(() => useUiStore.getState().setLanguage('ja', 'alice')).not.toThrow()
    expect(useUiStore.getState().language).toBe('ja')
    useUiStore.getState().activateLanguage('bob')
    expect(useUiStore.getState().language).toBe('zh-CN')
  })
})
