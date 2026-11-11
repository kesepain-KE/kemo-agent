import type { ModulePanelDefinition } from '../types/api'

export type ModuleKind = 'expand' | 'sense' | 'message'
export type Scope = 'global' | 'shared' | 'user'
export interface WorkspacePage {
  id: string
  label: string
  type: 'builtin' | 'panel' | 'markdown' | 'web'
  view?: 'overview' | 'collection' | 'injection' | 'docs' | 'logs'
  source?: string
  entry?: string
  permissions?: string[]
}
export interface WorkspaceAction {
  id: string
  label: string
  input_schema: Record<string, unknown>
  permission: string
  handler: string
}
export interface WorkspaceItem {
  key: string
  kind: ModuleKind
  scope: Scope
  module: string
  title: string
  description: string
  health: string
  valid: boolean
  enabled: boolean
  active_for_main_agent: boolean
  open_control?: boolean
  open_input?: boolean
  recent_update?: string
  updated_at?: number
  injected_tokens?: number
  error: string
  ui_error: string
  ui_revision: string
  authorization_revision: string
  permission_revision: string
  requested_permissions: string[]
  granted_permissions: string[]
  default_page: string
  pages: WorkspacePage[]
  actions: WorkspaceAction[]
  collected_markdown?: string
  injected_markdown?: string
  control_operation_markdown?: string
  runtime?: Record<string, unknown>
  panel?: ModulePanelDefinition | null
  panel_error?: string
}
export interface WorkspaceInventory {
  user: string
  kind: ModuleKind
  items: WorkspaceItem[]
  summary: { total: number; enabled: number; invalid: number; injected_tokens: number }
  registry_revision: string
}
export interface WebInstance {
  nonce: string
  asset_url: string
  ui_revision: string
  permissions: string[]
}
export interface ImportPreview {
  import_id: string
  module: string
  title: string
  kind: ModuleKind
  scope: Scope
  updating: boolean
  files: string[]
  permissions: string[]
  entrypoints: string[]
}
