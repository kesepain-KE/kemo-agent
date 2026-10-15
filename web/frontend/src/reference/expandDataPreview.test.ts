import { describe, expect, it } from 'vitest'
import { expandDataContent, expandDataFragment } from './expandDataPreview.js'

const collected = '# 数据\n\n## 操控能力\n操作说明：采集文本中也可以出现同名标题\n\n数据末尾😀'
const prefix = '[global:example]\n## 数据采集\n数据来源：`global_expand/example/input_data.md`；以下是状态数据。\n'
const dataPart = prefix + collected
const module = {
  scope: 'global', name: 'example', open_input: true, input_health: '正常',
  collected_markdown: collected,
  injected_markdown: dataPart + '\n\n## 操控能力\n操作说明：`global_expand/example/expand_control.md`\n真正的操控能力说明',
}

describe('拓展只读数据预览分类', () => {
  it('旧接口保留采集中的同名标题，仅移除框架附加的操控能力', () => {
    expect(expandDataFragment(module)).toBe(dataPart)
    expect(expandDataFragment(module)).toContain('数据末尾😀')
    expect(expandDataFragment(module)).not.toContain('真正的操控能力说明')
  })

  it.each([5, prefix.length - 15, prefix.length + 8, dataPart.length + 10])('旧接口预算截断 %s 字符时不补全未注入的数据', (limit) => {
    expect(expandDataFragment({ ...module, injected_markdown: module.injected_markdown.slice(0, limit) }))
      .toBe(dataPart.slice(0, limit))
  })

  it('关闭数据输入、只有控制说明、未注入或采集快照不一致时不冒充实际数据注入', () => {
    expect(expandDataFragment({ ...module, open_input: false })).toBe('')
    expect(expandDataFragment({ ...module, injected_markdown: '[global:example]\n## 操控能力\n控制说明' })).toBe('')
    expect(expandDataFragment({ ...module, injected_markdown: '' })).toBe('')
    expect(expandDataFragment({ ...module, collected_markdown: '更新后与已注入文本不同' })).toBe('')
  })

  it('新接口数据字段优先，包括明确为空的实际注入', () => {
    expect(expandDataFragment({ ...module, data_injected_markdown: '' })).toBe('')
    expect(expandDataFragment({ ...module, data_injected_markdown: '后端提供的实际数据' })).toBe('后端提供的实际数据')
    const data = { injection: { data_content: '', content: '操控能力' }, expands: [{ items: [module] }] }
    expect(expandDataContent(data)).toBe('')
    expect(expandDataContent({ ...data, injection: { content: '包含操控能力的全文' } })).toBe(dataPart)
  })
})
