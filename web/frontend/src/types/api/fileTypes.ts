export interface FileListEntry {
  type: 'directory' | 'file'
  name: string
  relative_path: string
  parent_path: string
  size: number
  updated_at: number
  extension: string
  child_count: number
}

export interface FileTreeSummary {
  total_files: number
  total_dirs: number
  total_size: number
}

export interface FileListPagination {
  page: number
  page_size: number
  total_items: number
  total_pages: number
  has_previous: boolean
  has_next: boolean
}

export interface FileListResponseBase {
  root: string
  summary: FileTreeSummary
  entries: FileListEntry[]
  path: string
  search: string
  pagination: FileListPagination
}

export interface UserFilesResponse extends FileListResponseBase {
  user: string
  scope: 'file_upload' | 'download'
}

export interface TmpFilesResponse extends FileListResponseBase {
  root: 'tmp' | string
}

export interface FileDeleteResponse {
  user?: string
  scope?: 'file_upload' | 'download'
  path: string
  deleted: boolean
}

export interface FilesDeleteResponse {
  user?: string
  scope?: 'file_upload' | 'download'
  deleted_paths: string[]
  deleted_count: number
}

export type TmpFilesDeleteResponse = FilesDeleteResponse

export interface FileMutationResponse {
  user?: string
  scope?: 'file_upload' | 'download'
  root?: string
  path?: string
  new_path?: string
  size?: number
  updated?: boolean
  created?: boolean
  moved?: boolean
  renamed?: boolean
  checksum_sha256?: string
  media_kind?: 'image' | 'audio' | 'video' | 'file'
  mime_type?: string
  thumbnail_available?: boolean
}

export interface PreferencesResponse {
  user: string
  appearance: { theme: 'light' | 'dark'; font_size: 'small' | 'medium' | 'large' }
}

export interface AvatarUploadResponse {
  user: string
  avatar_path: string
  size: number
  format: string
}

export interface CompletionSoundStatus {
  user: string
  enabled: boolean
  available: boolean
  filename: string
  mime_type: string
  size: number
  updated_at: string
  terminal_fallback_supported?: boolean
}

export interface CompletionSoundUploadResponse {
  ok: boolean
  status: CompletionSoundStatus
}

export interface CompletionSoundDeleteResponse {
  ok: boolean
  deleted: boolean
}

export interface CompletionSoundFallbackResponse {
  user: string
  played: boolean
  mode: 'user_wav' | 'system_notification' | '' | string
  reason: 'browser_fallback' | 'not_configured' | 'unsupported_host' | 'playback_failed' | string
}

export type FailureSoundStatus = CompletionSoundStatus
export type FailureSoundUploadResponse = CompletionSoundUploadResponse
export type FailureSoundDeleteResponse = CompletionSoundDeleteResponse
export type FailureSoundFallbackResponse = CompletionSoundFallbackResponse

export interface InventoryFile {
  name: string
  relative_path: string
  size: number
  updated_at: number
}

export interface AgentsResponse {
  user: string
  summary: { total: number; enabled: number; global: number; user: number }
  agents: Array<{
    name: string
    version: string
    description: string
    enabled: boolean
    source: 'global' | 'user'
    trigger: string
    rules: string
    executor: string
    execution: string
    model_profile: string
    exposure: string
    root: string
    files: InventoryFile[]
  }>
}

export interface AgentDeleteResponse {
  user: string
  name: string
  path: string
  deleted: boolean
}
