import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { MemoryRouter, Outlet, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { server } from '../test/server'
import type { ShellOutletContext } from '../components/appShellTypes'
import { FilesPage } from './FilesPage'

const writeText = vi.fn<() => Promise<void>>()
const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard')

beforeEach(() => {
  writeText.mockReset().mockResolvedValue(undefined)
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText } })
})
afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard)
  else Reflect.deleteProperty(navigator, 'clipboard')
})

describe('文件复制路径统一以智能体工作区为基准', () => {
  it.each([
    ['alice', 'file_upload', '用户上传', '12650.jpg'],
    ['bob', 'file_upload', '用户上传', '资料/我的 图片.jpg'],
    ['alice', 'download', '智能体产物', '12650.jpg'],
    ['bob', 'download', '智能体产物', '资料/我的 图片.jpg'],
    ['alice', 'tmp', '全局临时区', '12650.jpg'],
    ['bob', 'tmp', '全局临时区', '资料/我的 图片.jpg'],
  ] as const)('%s / %s / %s / %s 列表、详情、放大预览一致', async (user, area, label, path) => {
    const name = path.split('/').at(-1)!
    const root = area === 'tmp' ? 'tmp' : `users/${user}/${area}`
    const endpoint = area === 'tmp' ? '/api/tmp' : `/api/users/${user}/files/${area}`
    server.use(http.get(endpoint, () => HttpResponse.json({
      user, scope: area, root, path: '', search: '',
      summary: { total_files: 1, total_dirs: 0, total_size: 64 },
      pagination: { page: 1, page_size: 6, total_items: 1, total_pages: 1, has_previous: false, has_next: false },
      entries: [{ type: 'file', name, relative_path: path, parent_path: path.split('/').slice(0, -1).join('/'),
        extension: '.jpg', size: 64, child_count: 0, updated_at: 1 }],
    })))
    // The page initially loads uploads even when the tested area is another tab.
    if (area !== 'file_upload') {
      server.use(http.get(`/api/users/${user}/files/file_upload`, () => HttpResponse.json({
        entries: [], summary: { total_files: 0, total_dirs: 0, total_size: 0 },
        pagination: { page: 1, total_pages: 1 },
      })))
    }
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const context = { user, sessionId: 's1', chatRunning: false } as ShellOutletContext
    render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/files']}>
      <Routes><Route element={<Outlet context={context} />}><Route path="/files" element={<FilesPage />} /></Route></Routes>
    </MemoryRouter></QueryClientProvider>)
    fireEvent.click(screen.getByRole('button', { name: label }))
    fireEvent.click(await screen.findByRole('button', { name: `复制 ${name} 路径` }))
    await waitFor(() => expect(writeText).toHaveBeenLastCalledWith(`${root}/${path}`))
    fireEvent.click(screen.getByRole('button', { name: `${name} ${path}` }))
    fireEvent.click(screen.getByRole('button', { name: '复制路径' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(2))
    expect(writeText).toHaveBeenLastCalledWith(`${root}/${path}`)
    // Read/download APIs must keep their area-relative paths, not the copied workspace path.
    const downloadUrl = new URL(screen.getByRole('link', { name: '下载' }).getAttribute('href')!, 'http://localhost')
    expect(downloadUrl.searchParams.get('path')).toBe(path)
    fireEvent.click(screen.getByRole('button', { name: '放大预览' }))
    fireEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: '复制路径' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledTimes(3))
    expect(writeText).toHaveBeenLastCalledWith(`${root}/${path}`)
    client.clear()
  })
})
