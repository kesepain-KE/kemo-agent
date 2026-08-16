import type { ExpandsResponse, SkillsResponse } from '../types/api'
import type { CapabilityReferenceItem } from './CapabilityReferenceDrawer'

const referenceTypeLabels = {
  expand: '拓展',
  skill: '技能',
  plugin: '插件',
} as const

export function buildCapabilityReferenceItems(
  expands?: ExpandsResponse,
  skills?: SkillsResponse,
): CapabilityReferenceItem[] {
  return [
    ...(expands?.expands.flatMap((group) => group.items).map((module) => ({
      id: `expand:${module.scope}:${module.name}`,
      kind: 'expand' as const,
      scope: module.scope,
      name: module.name,
      title: module.display_name || module.name,
      description: module.description,
      path: module.relative_path || module.path,
      status: !module.valid
        ? '配置异常'
        : module.active_for_main_agent && module.whitelisted
          ? '已启用'
          : '未启用',
    })) ?? []),
    ...(skills?.prompt_skills.map((skill) => ({
      id: `skill:${skill.scope}:${skill.name}`,
      kind: 'skill' as const,
      scope: skill.scope,
      name: skill.name,
      title: skill.title || skill.name,
      description: skill.description,
      path: skill.path,
      status: skill.active_for_main_agent ? '已启用' : '未启用',
    })) ?? []),
    ...(skills?.tools.map((plugin) => ({
      id: `plugin:global:${plugin.name}`,
      kind: 'plugin' as const,
      scope: 'global' as const,
      name: plugin.name,
      title: plugin.name,
      description: plugin.description,
      path: plugin.source,
      status: plugin.enabled ? '已启用' : '未启用',
    })) ?? []),
  ]
}

export function capabilityReferenceMarker(item: CapabilityReferenceItem): string {
  return `[${referenceTypeLabels[item.kind]}引用 ${item.scope}:${item.name}]`
}

export function capabilityReferenceLine(item: CapabilityReferenceItem): string {
  return `${capabilityReferenceMarker(item)} ${item.title || item.name}`
}
