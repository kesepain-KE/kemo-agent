import { MarkdownMessage, type MarkdownMessageProps } from './Chat/MarkdownMessage'

/** 业务文档统一采用知识库放大预览的排版与安全渲染能力。 */
export function DocumentMarkdown(props: MarkdownMessageProps) {
  return <div className="prose document-markdown"><MarkdownMessage {...props} /></div>
}
