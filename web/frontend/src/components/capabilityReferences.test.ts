import { describe, expect, it } from 'vitest'
import type { ExpandsResponse, SkillsResponse } from '../types/api'
import {
  buildCapabilityReferenceItems,
  capabilityReferencePrompt,
  capabilityReferenceMarker,
} from './capabilityReferences'

describe('capabilityReferences', () => {
  it('把拓展、技能和插件清单归一化到规定层级', () => {
    const expands = {
      expands: [{ scope: 'shared', items: [{
        scope: 'shared', name: 'calendar', display_name: '共享日历', description: '读取日历',
        relative_path: 'calendar', path: 'expand/share/calendar', valid: true, whitelisted: true,
        active_for_main_agent: true, control_file: 'expand/share/calendar/operations.md',
      }] }],
    } as unknown as ExpandsResponse
    const skills = {
      prompt_skills: [{ name: 'release', title: '发布检查', description: '检查发布', scope: 'user', path: 'users/demo/user_skills/release', active_for_main_agent: true }],
      tools: [{ name: 'clock', description: '读取时间', source: 'plugins', enabled: true }],
      items: [{ category: 'builtin', name: 'clock', path: 'plugins/clock' }],
    } as unknown as SkillsResponse

    const result = buildCapabilityReferenceItems(expands, skills)
    expect(result.map((item) => [item.kind, item.scope, item.name])).toEqual([
      ['expand', 'shared', 'calendar'],
      ['skill', 'user', 'release'],
      ['plugin', 'global', 'clock'],
    ])
    expect(result.every((item) => item.status === '已启用')).toBe(true)
    expect(result.map((item) => item.path)).toEqual([
      'expand/share/calendar', 'users/demo/user_skills/release', 'plugins/clock',
    ])
    const prompt = capabilityReferencePrompt(result[0])
    expect(prompt).toContain('当前已指定能力“共享日历”')
    expect(prompt).toContain('"expand/share/calendar/operations.md"')
    expect(prompt).toContain('拓展 shared:calendar')
    expect(prompt).toContain('优先读取能力操作手册及相关文档')
  })

  it('生成简短能力提示并保留旧草稿的引用标记识别', () => {
    const item = { id: 'skill:user:release', kind: 'skill', scope: 'user', name: 'release', title: '发布检查', description: '', path: 'users/demo/user_skills/release', status: '已启用' } as const
    expect(capabilityReferenceMarker(item)).toBe('[技能引用 user:release]')
    const prompt = capabilityReferencePrompt(item)
    expect(prompt).toMatch(/^当前已指定能力“发布检查”/)
    expect(prompt).toContain('"users/demo/user_skills/release/SKILL.md"')
    expect(prompt).toContain('充分使用指定的能力')
    expect(prompt).not.toContain('\n')
  })

  it('没有操控说明路径时要求读取清单确认入口，不猜测默认文件名', () => {
    const item = { id: 'expand:user:calendar', kind: 'expand', scope: 'user', name: 'calendar', title: '日历', description: '', path: 'users/demo/expand/calendar', status: '配置异常' } as const
    const prompt = capabilityReferencePrompt(item)
    expect(prompt).toContain('"users/demo/expand/calendar/module.yaml"')
    expect(prompt).toContain('control_entry')
    expect(prompt).toContain('按 control_entry 定位操作手册')
    expect(prompt).not.toContain('undefined')
  })
})
