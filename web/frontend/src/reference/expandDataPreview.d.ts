import type { ExpandModuleSummary, ExpandsResponse } from '../types/api'

type PreviewModule = Pick<ExpandModuleSummary,
  'name' | 'open_input' | 'input_health' | 'collected_markdown' | 'injected_markdown' | 'data_injected_markdown'
> & { scope: string }

export function expandDataFragment(module: PreviewModule): string
export function expandDataContent(data: {
  injection: Pick<ExpandsResponse['injection'], 'content' | 'data_content'>
  expands: Array<{ items: PreviewModule[] }>
}): string
