import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Blob as NodeBlob } from 'node:buffer'
import { server } from '../test/server'
import { FilePreview, fileKind } from './FilePreview'
import type { FileListEntry } from '../types/api'

vi.mock('./PdfDocumentPreview', () => ({
  default: ({ url, name }: { url: string; name: string }) =>
    <div role="document" aria-label={`PDF 预览 ${name}`} data-url={url} />,
}))
const file = (name: string): FileListEntry => ({
  name, relative_path: name, parent_path: '', extension: `.${name.split('.').pop()}`,
  type: 'file', size: 64, child_count: 0, updated_at: 1728000000,
})

describe('文件空间右侧及放大预览', () => {
  beforeEach(() => {
    // jsdom's AbortSignal and Blob differ from Node's fetch/stream realm.
    const fetch = globalThis.fetch
    vi.spyOn(globalThis, 'fetch').mockImplementation((input, options) => fetch(input, { ...options, signal: undefined }))
    vi.stubGlobal('Blob', NodeBlob)
  })
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
  it.each(['png', 'svg', 'avif', 'mp4', 'webm', 'mp3', 'wav', 'pdf', 'ppt', 'pptx', 'doc', 'docx',
    'xls', 'xlsx', 'odt', 'ods', 'odp', 'rtf', 'json', 'md', 'txt', 'csv'])('%s 有对应预览类型', ext => {
    expect(fileKind(file(`sample.${ext}`))).not.toBe('file')
  })

  it('Markdown 完整渲染，放大与内联内容相同，Escape 关闭并恢复焦点', async () => {
    let reads = 0
    server.use(http.get('/api/users/kesepain/files/file_upload/preview', () => {
      reads++
      return HttpResponse.text('# 完整文档\n\n**正文格式**\n\n- 内容一\n- 内容二\n\n末尾完整内容')
    }))
    render(<FilePreview file={file('readme.md')} user="kesepain" area="file_upload" onClose={() => {}} />)
    expect(await screen.findByRole('heading', { name: '完整文档' })).toBeInTheDocument()
    expect(screen.getByText('正文格式').tagName).toBe('STRONG')
    expect(screen.getByText('末尾完整内容')).toBeInTheDocument()
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    const button = screen.getByRole('button', { name: '放大预览' })
    button.focus()
    fireEvent.click(button)
    const dialog = screen.getByRole('dialog', { name: '预览 readme.md' })
    expect(within(dialog).getByRole('heading', { name: '完整文档' })).toBeInTheDocument()
    expect(reads).toBe(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(button).toHaveFocus()
  })

  it('JSON 格式化而普通文本保留换行；HTML 不执行', async () => {
    server.use(http.get('/api/tmp/preview', () => HttpResponse.text('{"first":1,"second":[2,3]}')))
    const { unmount } = render(<FilePreview file={file('data.json')} user="kesepain" area="tmp" onClose={() => {}} />)
    await waitFor(() => expect(document.querySelector('pre code')).toHaveTextContent('"second"'))
    expect(document.querySelector('pre code')?.textContent).toContain('\n  "first": 1,')
    unmount()
    server.use(http.get('/api/tmp/preview', () => HttpResponse.text('<script>alert(1)</script>\nsecond line')))
    render(<FilePreview file={file('notes.txt')} user="kesepain" area="tmp" onClose={() => {}} />)
    await waitFor(() => expect(document.querySelector('pre code')?.textContent).toContain('\nsecond line'))
    expect(document.querySelector('.file-detail-body script')).toBeNull()
  })

  it('Office 转换后使用 PDF，放大不重复获取，卸载释放预览 URL', async () => {
    const create = vi.fn(() => 'blob:document-preview'), revoke = vi.fn()
    vi.stubGlobal('URL', class extends URL {
      static createObjectURL = create
      static revokeObjectURL = revoke
    })
    try {
      let reads = 0
      server.use(http.get('/api/users/kesepain/files/file_upload/preview', () => {
        reads++
        return new HttpResponse('%PDF-1.7\npreview', { headers: { 'Content-Type': 'application/pdf' } })
      }))
      const { unmount } = render(<FilePreview file={file('slides.pptx')} user="kesepain" area="file_upload" onClose={() => {}} />)
      expect(await screen.findByRole('document', { name: 'PDF 预览 slides.pptx' })).toHaveAttribute('data-url', 'blob:document-preview')
      fireEvent.click(screen.getByRole('button', { name: '放大预览' }))
      expect(await within(screen.getByRole('dialog')).findByRole('document', { name: 'PDF 预览 slides.pptx' })).toBeInTheDocument()
      expect(reads).toBe(1)
      unmount()
      expect(revoke).toHaveBeenCalledWith('blob:document-preview')
    } finally {
      vi.unstubAllGlobals()
    }
  })

  it('预览失败可重试，超大文件不请求并保留下载入口', async () => {
    server.use(http.get('/api/users/kesepain/files/file_upload/preview',
      () => HttpResponse.json({ error: { message: '文档损坏' } }, { status: 400 })))
    const { unmount } = render(<FilePreview file={file('broken.txt')} user="kesepain" area="file_upload" onClose={() => {}} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('文档损坏')
    server.use(http.get('/api/users/kesepain/files/file_upload/preview', () => HttpResponse.text('recovered')))
    fireEvent.click(screen.getByRole('button', { name: '重试预览' }))
    expect(await screen.findByText('recovered')).toBeInTheDocument()
    unmount()
    render(<FilePreview file={{ ...file('huge.txt'), size: 5 * 1024 * 1024 }}
      user="kesepain" area="file_upload" onClose={() => {}} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('超过预览上限')
    expect(screen.getByRole('link', { name: '下载' })).toHaveAttribute('download', 'huge.txt')
  })
})
