import { useEffect, useRef, useState } from 'react'
import { GlobalWorkerOptions, getDocument, type PDFDocumentProxy, type RenderTask } from 'pdfjs-dist'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

GlobalWorkerOptions.workerSrc = workerUrl

/** Render nearby pages only; the original document is never rewritten. */
function PdfPage({ document, number, width, scroll }: {
  document: PDFDocumentProxy; number: number; width: number; scroll: HTMLDivElement | null
}) {
  const host = useRef<HTMLDivElement>(null)
  const canvas = useRef<HTMLCanvasElement>(null)
  const [visible, setVisible] = useState(number === 1)
  const [ratio, setRatio] = useState(Math.SQRT2)
  const [error, setError] = useState('')
  const [ready, setReady] = useState(false)

  useEffect(() => {
    if (!host.current || !scroll) return
    if (typeof IntersectionObserver === 'undefined') {
      setVisible(true)
      return
    }
    const observer = new IntersectionObserver(entries => setVisible(entries[0].isIntersecting),
      { root: scroll, rootMargin: '600px 0px' })
    observer.observe(host.current)
    return () => observer.disconnect()
  }, [scroll])

  useEffect(() => {
    if (!visible || !width) return
    let disposed = false
    let task: RenderTask | undefined
    setReady(false); setError('')
    void document.getPage(number).then(async page => {
      if (disposed || !canvas.current) return
      const native = page.getViewport({ scale: 1 })
      const view = page.getViewport({ scale: width / native.width })
      setRatio(native.height / native.width)
      // Limit each canvas allocation while preserving readable display sizing.
      const density = Math.max(.1, Math.min(window.devicePixelRatio || 1, 2,
        Math.sqrt(4_000_000 / (view.width * view.height))))
      canvas.current.width = Math.max(1, Math.floor(view.width * density))
      canvas.current.height = Math.max(1, Math.floor(view.height * density))
      task = page.render({
        canvas: canvas.current,
        viewport: view,
        transform: density === 1 ? undefined : [density, 0, 0, density, 0, 0],
        background: '#ffffff',
      })
      await task.promise
      if (!disposed) setReady(true)
    }).catch(cause => {
      if (!disposed) setError(cause instanceof Error ? cause.message : '页面渲染失败')
    })
    return () => { disposed = true; task?.cancel() }
  }, [document, number, width, visible])

  return <div ref={host} className="file-pdf-page" style={{ width, height: width * ratio }}>
    {visible && <canvas ref={canvas} role="img" aria-label={`第 ${number} 页`}
      data-rendered={ready ? 'true' : 'false'} style={{ width, height: width * ratio }} />}
    {error ? <div className="file-pdf-page-status" role="alert">第 {number} 页无法渲染：{error}</div>
      : !ready && <div className="file-pdf-page-status">{number} / {document.numPages}</div>}
  </div>
}

/** Same-origin PDF.js viewer, including converted Office documents. */
export default function PdfDocumentPreview({ url, name }: { url: string; name: string }) {
  const [document, setDocument] = useState<PDFDocumentProxy>()
  const [error, setError] = useState('')
  const [width, setWidth] = useState(0)
  const [scroll, setScroll] = useState<HTMLDivElement | null>(null)
  const host = useRef<HTMLDivElement>(null)

  useEffect(() => {
    setError(''); setDocument(undefined)
    let disposed = false
    const base = `${import.meta.env.BASE_URL}pdfjs/`
    const task = getDocument({
      url, cMapUrl: `${base}cmaps/`, cMapPacked: true,
      standardFontDataUrl: `${base}standard_fonts/`, wasmUrl: `${base}wasm/`,
      useSystemFonts: true, enableXfa: false,
    })
    void task.promise.then(value => { if (!disposed) setDocument(value) })
      .catch(cause => { if (!disposed) setError(cause instanceof Error ? cause.message : 'PDF 读取失败') })
    return () => { disposed = true; void task.destroy().catch(() => {}) }
  }, [url])

  useEffect(() => {
    if (!host.current) return
    const update = () => setWidth(Math.max(0, (host.current?.clientWidth || 0) - 32))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(host.current)
    return () => observer.disconnect()
  }, [])

  return <div className="file-pdf-viewer" ref={host} role="document" aria-label={`PDF 预览 ${name}`}>
    {error ? <div className="empty" role="alert"><div className="empty-title">无法显示 PDF</div>
      <div className="empty-desc">{error}，请下载原文件查看。</div></div>
      : !document ? <div className="reference-loading" role="status"><span className="spinner" />正在渲染文档…</div>
        : <><div className="file-pdf-summary">{document.numPages} 页</div>
          <div className="file-pdf-scroll" ref={setScroll}>
            {Array.from({ length: document.numPages }, (_, index) =>
              <PdfPage key={index} document={document} number={index + 1} width={width} scroll={scroll} />)}
          </div></>}
  </div>
}
