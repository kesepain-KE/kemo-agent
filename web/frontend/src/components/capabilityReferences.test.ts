import { describe, expect, it } from 'vitest'
import type { ExpandsResponse, SkillsResponse } from '../types/api'
import {
  buildCapabilityReferenceItems,
  capabilityReferenceLine,
  capabilityReferenceMarker,
} from './capabilityReferences'

describe('capabilityReferences', () => {
  it('把拓展、技能和插件清单归一化到规定层级', () => {
    const expands = {
      expands: [{ scope: 'shared', items: [{
        scope: 'shared', name: 'calendar', display_name: '共享日历', description: '读取日历',
        relative_path: 'calendar', path: 'shared_expand/calendar', valid: true, whitelisted: true,
        active_for_main_agent: true,
      }] }],
    } as unknown as ExpandsResponse
    const skills = {
      prompt_skills: [{ name: 'release', title: '发布检查', description: '检查发布', scope: 'user', path: 'users/demo/user_skills/release', active_for_main_agent: true }],
      tools: [{ name: 'clock', description: '读取时间', source: 'plugins/clock', enabled: true }],
    } as unknown as SkillsResponse

    const result = buildCapabilityReferenceItems(expands, skills)
    expect(result.map((item) => [item.kind, item.scope, item.name])).toEqual([
      ['expand', 'shared', 'calendar'],
      ['skill', 'user', 'release'],
      ['plugin', 'global', 'clock'],
    ])
    expect(result.every((item) => item.status === '已启用')).toBe(true)
  })

  it('为三类能力生成稳定且互不冲突的引用标记', () => {
    const item = { id: 'skill:user:release', kind: 'skill', scope: 'user', name: 'release', title: '发布检查', description: '', path: '', status: '已启用' } as const
    expect(capabilityReferenceMarker(item)).toBe('[技能引用 user:release]')
    expect(capabilityReferenceLine(item)).toBe('[技能引用 user:release] 发布检查')
  })
})
