export type ModulePanelScope = 'expand' | 'sense'
export type ModulePanelWidth = 'quarter' | 'third' | 'half' | 'full'
export type ModulePanelHeight = 'h1' | 'h2'
export type ModulePanelInputType = 'string' | 'number' | 'boolean' | 'enum' | 'text'
export type ModulePanelStatusType = 'text' | 'badge' | 'keyvalue' | 'markdown'
export type ModulePanelValue = string | number | boolean

export interface ModulePanelOption {
  value: string
  label: string
}

export interface ModulePanelField {
  key: string
  label: string
  type: ModulePanelInputType | ModulePanelStatusType
  placeholder?: string
  description?: string
  masked?: boolean
  required?: boolean
  max_length?: number
  min?: number
  max?: number
  options?: ModulePanelOption[]
  default?: ModulePanelValue
}

export interface ModulePanelPreset {
  name: string
  values: Record<string, ModulePanelValue>
}

export interface ModulePanelStatusContainer {
  kind: 'status'
  title: string
  width: ModulePanelWidth
  height: ModulePanelHeight
  source: string
  fields: ModulePanelField[]
  data?: Record<string, unknown>
  secret_set?: Record<string, boolean>
  data_error?: string
}

export interface ModulePanelConfigContainer {
  kind: 'config'
  title: string
  width: ModulePanelWidth
  height: ModulePanelHeight
  values: string
  fields: ModulePanelField[]
  presets: ModulePanelPreset[]
  current_values?: Record<string, ModulePanelValue>
  secret_set?: Record<string, boolean>
  data_error?: string
}

export interface ModulePanelControl {
  type: 'button' | 'send'
  label: string
  command: string
  inputs?: ModulePanelField[]
}

export interface ModulePanelActionContainer {
  kind: 'action'
  title: string
  width: ModulePanelWidth
  height: ModulePanelHeight
  controls: ModulePanelControl[]
}

export type ModulePanelContainer = ModulePanelStatusContainer | ModulePanelConfigContainer | ModulePanelActionContainer

export interface ModulePanelDefinition {
  schema_version: 1
  title: string
  containers: ModulePanelContainer[]
}

export interface ModulePanelResponse {
  user: string
  module: string
  scope?: 'global' | 'shared' | 'user'
  panel: ModulePanelDefinition | null
  panel_error: string
  saved?: boolean
  command?: string
  result?: unknown
}
