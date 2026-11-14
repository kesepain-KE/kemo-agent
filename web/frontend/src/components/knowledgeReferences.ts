import type { KnowledgeDocumentSummary } from '../types/api'

export function knowledgeReferencePrompt(
  document: Pick<KnowledgeDocumentSummary, 'scope' | 'relative_path' | 'title'>,
  rootPath: string,
  directory = false,
): string {
  const path = document.relative_path === '.' ? rootPath : `${rootPath}/${document.relative_path}`
  const content = directory ? '目录内与任务相关的内容' : '该文档内容'
  return `当前已指定知识库“${document.title}”（路径：${JSON.stringify(path)}），请在执行任务之前优先读取${content}，充分使用知识库提供的资料。`
}
