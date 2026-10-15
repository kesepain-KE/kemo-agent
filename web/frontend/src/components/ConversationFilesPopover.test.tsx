import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ConversationFilesPopover } from './ConversationFilesPopover'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

it('文件栏固定在视口右侧，不根据聊天内容或侧栏宽度重新计算位置', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const view = render(
    <QueryClientProvider client={client}>
    <ConversationFilesPopover user="kesepain" sessionId="s1" />
    </QueryClientProvider>,
  )
  fireEvent.click(screen.getByRole('button', { name: '当前对话文件历史' }))
  const panel = screen.getByRole('region', { name: '当前对话文件历史' })
  await waitFor(() => expect(panel).toBeInTheDocument())
  expect(panel).not.toHaveAttribute('style')
  view.unmount()
  client.clear()
})
