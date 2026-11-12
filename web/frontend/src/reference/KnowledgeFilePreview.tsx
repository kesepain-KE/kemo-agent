import * as api from '../api/client'
import { t as tr } from '../i18n'
import type { FileListEntry, KnowledgeDocumentSummary } from '../types/api'
import { FilePreview } from './FilePreview'

export const isEditableKnowledgeDocument = (path: string) => /\.(md|txt)$/i.test(path)

/** Knowledge files keep their own path boundary while sharing the file viewer. */
export function KnowledgeFilePreview({ document, user, onClose, onEdit }: {
  document: KnowledgeDocumentSummary; user: string; onClose: () => void; onEdit?: () => void
}) {
  const path = document.relative_path
  const name = path.split('/').at(-1) || path
  const file: FileListEntry = {
    name, relative_path: path, parent_path: path.split('/').slice(0, -1).join('/'),
    extension: name.includes('.') ? `.${name.split('.').at(-1)}` : '',
    type: 'file', size: document.size, child_count: 0, updated_at: document.updated_at,
  }
  const root = document.scope === 'user' ? `users/${user}/knowledge`
    : document.scope === 'shared' ? 'knowledge/share' : 'knowledge/global'
  return <FilePreview file={file} user={user} onClose={onClose} toolbar={onEdit && (
    <div className="knowledge-preview-toolbar"><div className="segments">
      <button className="segment is-active" aria-pressed="true">{tr('预览')}</button>
      <button className="segment" aria-pressed="false" disabled={!isEditableKnowledgeDocument(path)} onClick={onEdit}>{tr('编辑')}</button>
    </div></div>
  )} source={{
    previewUrl: api.getKnowledgeFilePreviewUrl(user, document.scope, path),
    downloadUrl: api.getKnowledgeFileDownloadUrl(user, document.scope, path),
    workspacePath: `${root}/${path}`,
  }} />
}
