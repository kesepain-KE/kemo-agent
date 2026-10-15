import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import PdfDocumentPreview from './PdfDocumentPreview'

const pdf = vi.hoisted(() => ({ getDocument: vi.fn(), destroy: vi.fn(() => Promise.resolve()), render: vi.fn() }))
vi.mock('pdfjs-dist', () => ({ GlobalWorkerOptions: {}, getDocument: pdf.getDocument }))

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.clearAllMocks() })

describe('页面内 PDF 渲染', () => {
  it('使用本地 worker/resources，渲染可见页面，卸载销毁文档任务', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(480)
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
    vi.stubGlobal('IntersectionObserver', class { observe() {} disconnect() {} })
    const cancel = vi.fn()
    pdf.render.mockReturnValue({ promise: Promise.resolve(), cancel })
    pdf.getDocument.mockReturnValue({ promise: Promise.resolve({
      numPages: 2, getPage: async () => ({
        getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
        render: pdf.render,
      }),
    }), destroy: pdf.destroy })
    const { unmount } = render(<PdfDocumentPreview url="blob:local-document" name="file.pdf" />)
    await waitFor(() => expect(screen.getByRole('img', { name: '第 1 页' })).toHaveAttribute('data-rendered', 'true'))
    expect(pdf.render).toHaveBeenCalledOnce()
    expect(pdf.getDocument).toHaveBeenCalledWith(expect.objectContaining({
      url: 'blob:local-document', cMapUrl: '/pdfjs/cmaps/', standardFontDataUrl: '/pdfjs/standard_fonts/',
      wasmUrl: '/pdfjs/wasm/', enableXfa: false,
    }))
    expect(screen.getByRole('document')).toHaveTextContent('2 页')
    unmount()
    expect(pdf.destroy).toHaveBeenCalledOnce()
    expect(cancel).toHaveBeenCalledOnce()
  })

  it('损坏的 PDF 显示错误而不是空白查看器', async () => {
    vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(480)
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} })
    pdf.getDocument.mockReturnValue({ promise: Promise.reject(new Error('Invalid PDF structure')), destroy: pdf.destroy })
    render(<PdfDocumentPreview url="blob:broken" name="broken.pdf" />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid PDF structure')
    expect(screen.queryByRole('img')).not.toBeInTheDocument()
  })
})
