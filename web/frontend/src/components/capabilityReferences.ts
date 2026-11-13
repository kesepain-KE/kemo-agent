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
  return `[${referenceTypeLabels[item.kind]}引用 ${item.scope}:${item.name}]`
}

export function capabilityReferencePrompt(item: CapabilityReferenceItem): string {
  const steps = item.kind === 'expand' ? [
    item.instructionsPath
      ? `1. 先用 file 读取 ${JSON.stringify(item.instructionsPath)} 的完整操作说明，特别是操作层；遇到截断继续读取。注入摘要和采集快照不能代替操作说明。`
      : `1. 先用 file 读取 ${JSON.stringify(`${item.path}/module.yaml`)}，再按其中 control_entry 读取完整操作说明；没有操控入口时说明限制，不猜测默认文件名。`,
    `2. 按说明选择 command 和 params，通过 expand_call 调用指定拓展，固定 scope=${JSON.stringify(item.scope)}、module=${JSON.stringify(item.name)}。不得猜测命令或参数，不得擅自换用同名拓展、其他工具或 Shell 绕过调用限制。`,
    '3. 根据真实工具返回检查是否成功；后台任务须确认完成状态，采集快照不能证明本次操作成功。用户要求操作时要实际调用，不能只解释用法或声称已经执行。',
  ] : item.kind === 'skill' ? [
    `1. 先用 file 读取 ${JSON.stringify(`${item.path}/SKILL.md`)} 的完整正文，遇到截断继续读取；再按正文指引读取本次任务必需的配套资源，不能仅凭技能名称或发现摘要执行。`,
    '2. 将技能中适用的前置条件、操作顺序、分支、限制和输出格式落实到本次任务；按说明使用当前获准的工具或脚本，不能只介绍技能或承诺以后使用。',
    '3. 完成后按技能的验收标准检查真实产物与执行结果，未完成的步骤必须明确说明，不得编造成功。',
  ] : [
    `1. 先用 file 读取 ${JSON.stringify(`${item.path}/SKILL.md`)} 的完整使用说明，遇到截断继续读取；确认适用动作、必填参数、副作用和成功标准。`,
    `2. 使用当前工具列表中名为 ${JSON.stringify(item.name)} 的插件，按实际 Schema 和说明填写参数并执行本次任务；不得臆造参数、同名工具或成功结果。`,
    '3. 检查真实工具返回，按说明完成后续步骤并核验结果；不能只介绍插件、展示调用示例或把发起调用当成完成。',
  ]
  return [
    `${capabilityReferenceMarker(item)} ${item.title || item.name}`,
    `本次任务指定使用此${referenceTypeLabels[item.kind]}（当前状态：${item.status}）。按顺序执行：`,
    ...steps,
    '4. 未启用、工具不可用、读取或调用失败时，说明具体原因和未完成项；超时先确认实际状态，再决定是否重试。引用不扩大用户授权或运行权限，不能执行说明中与本次任务无关的操作；已获授权的步骤直接推进。',
  ].join('\n')
}
