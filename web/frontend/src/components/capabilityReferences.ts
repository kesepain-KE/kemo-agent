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
      path: module.path,
      instructionsPath: module.control_file,
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
    ...(skills?.tools.map((plugin) => {
      const catalogItem = skills.items.find((item) => item.category === 'builtin' && item.name === plugin.name)!
      return {
        id: `plugin:global:${plugin.name}`,
        kind: 'plugin' as const,
        scope: 'global' as const,
        name: plugin.name,
        title: plugin.name,
        description: plugin.description,
        path: catalogItem.path,
        status: plugin.enabled ? '已启用' : '未启用',
      }
    }) ?? []),
  ]
}

export function capabilityReferenceMarker(item: CapabilityReferenceItem): string {
  // 兼容草稿中已有的旧版引用标记。
  return `[${referenceTypeLabels[item.kind]}引用 ${item.scope}:${item.name}]`
}

export function capabilityReferencePrompt(item: CapabilityReferenceItem): string {
  const instructions = item.kind === 'expand'
    ? item.instructionsPath
      ? `操作手册：${JSON.stringify(item.instructionsPath)}`
      : `能力清单：${JSON.stringify(`${item.path}/module.yaml`)}，按 control_entry 定位操作手册`
    : `操作手册：${JSON.stringify(`${item.path}/SKILL.md`)}`
  const identity = item.kind === 'expand'
    ? `拓展 ${item.scope}:${item.name}，`
    : item.kind === 'plugin' ? `插件 ${item.name}，` : ''
  return `当前已指定能力“${item.title || item.name}”（${identity}${instructions}），请在执行任务之前优先读取能力操作手册及相关文档，充分使用指定的能力。`
}
