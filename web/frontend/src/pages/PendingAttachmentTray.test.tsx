import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { PendingAttachmentTray } from './chatPresentation'
import type { PendingUploadedFile } from '../store/chatDrafts'

const image = { name: 'votx-agent.png', path: 'votx-agent.png', size: 1024, mimeType: 'image/png', mediaKind: 'image' } satisfies PendingUploadedFile

describe('待发送附件简洁预览', () => {
  it('图片只展示预览与有边框类名的名字，不显示状态、MIME 或大小', () => {
    render(<PendingAttachmentTray user="test" files={[image]} onRemove={vi.fn()} />)
    const card = screen.getByRole('listitem', { name: '待发送附件：votx-agent.png' })
    expect(within(card).getByRole('img', { name: image.name })).toBeInTheDocument()
    expect(within(card).getByText(image.name)).toHaveClass('pending-attachment-name')
    expect(card).not.toHaveTextContent('已上传')
    expect(card).not.toHaveTextContent('image/png')
    expect(card).not.toHaveTextContent('KB')
  })

  it('不可预览文件只显示缩略图和名称，保留文件名的可访问标识与取消引用', () => {
    const onRemove = vi.fn()
    render(<PendingAttachmentTray user="test" files={[{ ...image, name: 'bundle.zip', path: 'bundle.zip', mediaKind: 'file', mimeType: 'application/zip' }]} onRemove={onRemove} />)
    const card = screen.getByRole('listitem', { name: '待发送附件：bundle.zip' })
    expect(within(card).getByRole('img', { name: '文件缩略图' })).toBeInTheDocument()
    expect(within(card).getByText('bundle.zip')).toHaveClass('pending-attachment-name')
    expect(card).toHaveAttribute('title', 'bundle.zip')
    fireEvent.click(within(card).getByRole('button', { name: '取消引用 bundle.zip' }))
    expect(onRemove).toHaveBeenCalledExactlyOnceWith(0)
  })

  it('图片缩略加载失败降级为图标，不留下破图', () => {
    render(<PendingAttachmentTray user="test" files={[image]} onRemove={vi.fn()} />)
    fireEvent.error(screen.getByRole('img', { name: image.name }))
    expect(screen.getByRole('img', { name: '图片缩略图' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: `预览图片 ${image.name}` })).not.toBeInTheDocument()
  })

  it('图片预览不显示放大、缩小和重置按钮，且不改变发送附件', () => {
    render(<PendingAttachmentTray user="test" files={[image]} onRemove={vi.fn()} />)
    const card = screen.getByRole('listitem', { name: '待发送附件：votx-agent.png' })
    expect(within(card).getByRole('img', { name: image.name })).toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: '放大图片 votx-agent.png' })).not.toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: '缩小图片 votx-agent.png' })).not.toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: '重置图片大小 votx-agent.png' })).not.toBeInTheDocument()
  })
})
