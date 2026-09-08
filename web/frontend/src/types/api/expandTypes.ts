import type { MainAgentSourcePolicySummary } from '../api'
import type { InventoryFile } from './fileTypes'

export type ExpandScope = 'global' | 'shared' | 'user'

export interface ExpandRuntimeSection {
  status?: 'completed' | 'failed'
  last_attempt?: string
  last_success?: string | null
  duration_ms?: number
  last_command?: string
  resource_count?: number
  resources?: Array<{
    path: string
    kind: string
    label: string
    mime_type: string
    size: number
    updated_at: number
  }>
  error?: { type?: string; message?: string } | null
}

export interface ExpandModuleSummary {
  id: string
  scope: ExpandScope
  name: string
  display_name: string
  description: string
  type: 'directory'
  root: string
  path: string
  relative_path: string
  has_register: boolean
  valid: boolean
  error: string
  whitelisted: boolean
  active_for_main_agent: boolean
  input_health: string
  open_input: boolean
  open_control: boolean
  input_data: string
  start_update: string
  start_expand: string
  start_control: string
  control_document: string
  control_injection_markdown: string
  control_operation_markdown: string
  collected_markdown: string
  injected_markdown: string
  injected_tokens: number
  runtime?: {
    schema_version: number
    update?: ExpandRuntimeSection
    control?: ExpandRuntimeSection
  }
  files: InventoryFile[]
  updated_at: number
}

export interface ExpandsResponse {
  user: string
  summary: { total: number; global: number; shared: number; user: number }
  status_summary: {
    enabled: number
    healthy: number
    invalid: number
  }
  expands: Array<{
    scope: ExpandScope
    root: string
    items: ExpandModuleSummary[]
  }>
  injection: {
    content: string
    source_files: string[]
    original_chars: number
    injected_chars: number
    original_items: number
    injected_items: number
    estimated_tokens: number
    truncated: boolean
    prompt_section: string
    prompt_position: string
  }
  source_policy: MainAgentSourcePolicySummary
}
