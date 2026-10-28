import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { getHistory } from '../api/client'
import { buildHistoryItems, reduceRunEvent } from '../pages/chatState'
import { useWorkspaceStore } from '../store/workspace'
import type { ChatItem, HistoryResponse, NativeMediaInput } from '../types/api'
import { ConversationFilesPopover } from './ConversationFilesPopover'
import { NativeMediaLinks } from './NativeMediaLinks'
import { parseNativeInputs, toolNativeInputs } from './nativeMedia'
import type { ChatRunSnapshot } from './appShellTypes'

vi.mock('../api/client', async importOriginal => ({
  ...await importOriginal<typeof import('../api/client')>(), getHistory: vi.fn(),
}))

const media: NativeMediaInput = {
  asset_id: 'asset_screen', name: 'screen.png', media_kind: 'image', mime_type: 'image/png',
  size: 120, checksum_sha256: 'a'.repeat(64), scope: 'download', relative_path: 'screens/screen.png',
  available: true, status: 'queued', tool_call_id: 'attach-one',
}
const tool = (overrides: Partial<Extract<ChatItem, { kind: 'tool' }>> = {}): Extract<ChatItem, { kind: 'tool' }> => ({
  id: 'tool-one', kind: 'tool', callId: 'attach-one', name: 'attach_media', status: 'success',
  result: { ok: true, result: { status: 'queued', delivery: 'native_main', assets: [media] } }, ...overrides,
})
const history = (overrides: Partial<HistoryResponse> = {}): HistoryResponse => ({
  user: 'alice', source: 'web', session_id: 's1', messages: [], round_metrics: [], round_traces: [], ...overrides,
})
const clients: QueryClient[] = []
beforeEach(() => {
  useWorkspaceStore.setState({ users: {} })
  vi.mocked(getHistory).mockResolvedValue(history())
})
afterEach(() => { clients.splice(0).forEach(client => client.clear()); vi.clearAllMocks() })

describe('native media descriptors and state', () => {
  it('rejects unsafe locations and never derives files from arguments or a failed receipt', () => {
    for (const path of ['../secret', '/secret', 'C:/secret', 'a\\b.png', 'a/../b', 'a//b', 'a:b', 'a\0b']) {
      expect(parseNativeInputs([{ ...media, relative_path: path }])[0]).toMatchObject({ scope: 'external', available: false, relative_path: '' })
    }
    expect(toolNativeInputs(tool({ result: { ok: false, result: { assets: [media] } } }))).toEqual([])
    expect(toolNativeInputs(tool({ name: 'shell' }))).toEqual([])
    expect(toolNativeInputs(tool({ result: undefined, arguments: { paths: ['screen.png'] } }))).toEqual([])
    expect(parseNativeInputs(Array(100).fill(media))).toHaveLength(32)
  })

  it('updates only the active round, preserves receipts, and terminates unsent queued media', () => {
    const old = tool({ id: 'old', callId: 'old-call' })
    const boundary: ChatItem = { id: 'user', kind: 'message', role: 'user', content: 'inspect' }
    const original = tool()
    const updated = reduceRunEvent([old, boundary, original], {
      type: 'media_input_update', metadata: { media_inputs: [{ ...media, status: 'included' }] },
    })
    expect(updated[0]).toBe(old)
    expect(toolNativeInputs(updated[2] as typeof original)[0].status).toBe('included')
    expect((updated[2] as typeof original).result).toBe(original.result)
    const done = reduceRunEvent([boundary, original], { type: 'done', metadata: { media_inputs: [] } })
    expect(toolNativeInputs(done.find(item => item.kind === 'tool') as typeof original)[0].status).toBe('not_sent')
    const legacy = reduceRunEvent([boundary, original], { type: 'done' })
    expect(toolNativeInputs(legacy.find(item => item.kind === 'tool') as typeof original)[0].status).toBe('unconfirmed')
    const failure = reduceRunEvent([boundary, original], {
      type: 'error', metadata: { committed: true, media_inputs: [{ ...media, status: 'failed', available: false, error: 'file changed' }] },
    })
    expect(toolNativeInputs(failure.find(item => item.kind === 'tool') as typeof original)[0]).toMatchObject({ status: 'failed', available: false })
    const provisional = reduceRunEvent([boundary, original], {
      type: 'error', metadata: { committed: false, retryable: true },
    })
    expect(toolNativeInputs(provisional.find(item => item.kind === 'tool') as typeof original)[0].status).toBe('queued')
    const included = reduceRunEvent(updated, { type: 'done', metadata: { media_inputs: [{ ...media, status: 'included' }] } })
    expect(toolNativeInputs(included[2] as typeof original)[0].status).toBe('included')
  })

  it('restores audited history into the tool card without parsing or altering model text', () => {
    const items = buildHistoryItems(history({
      messages: [{ role: 'user', content: 'inspect' }, { role: 'assistant', content: 'done' }],
      round_traces: [{ round: 1, reasoning: '', tools: [{
        call_id: 'attach-one', name: 'attach_media', arguments_text: '{}', arguments_truncated: false,
        result_text: '{}', result_truncated: false, elapsed_ms: 1, status: 'success',
        media_inputs: [{ ...media, status: 'included' }],
      }] }],
    }))
    const restored = items.find(item => item.kind === 'tool')
    expect(restored?.kind === 'tool' && toolNativeInputs(restored)[0].status).toBe('included')
  })
})

describe('native media links', () => {
  it('previews only on explicit click, updates status, and shows failures without unsafe links', () => {
    const view = render(<NativeMediaLinks user="alice" item={tool()} />)
    expect(screen.getByText('媒体已排队')).toBeInTheDocument()
    expect(useWorkspaceStore.getState().users.alice).toBeUndefined()
    fireEvent.click(screen.getByRole('button', { name: '在工作面板打开 screen.png' }))
    expect(useWorkspaceStore.getState().users.alice).toMatchObject({
      activeId: 'file:download:screens/screen.png', open: true,
    })
    view.rerender(<NativeMediaLinks user="alice" item={tool({ mediaInputs: [{ ...media, status: 'included' }] })} />)
    expect(screen.getByText('媒体已随请求发送')).toHaveAttribute('title', expect.stringContaining('不代表模型已正确理解'))
    view.rerender(<NativeMediaLinks user="alice" item={tool({ mediaInputs: [{ ...media, status: 'failed', available: false, error: 'file changed' }] })} />)
    expect(screen.getByText('file changed')).toBeInTheDocument()
    expect(screen.queryByRole('button')).not.toBeInTheDocument()
  })

  it('links live media immediately, deduplicates saved files, refreshes history and isolates users', async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    clients.push(client)
    const live: ChatRunSnapshot = { phase: 'streaming', runId: 'r1', historyUserMessages: 1, nextTurnQueue: [], items: [tool()] }
    vi.mocked(getHistory).mockResolvedValue(history({ round_metrics: [{
      round: 1, usage: {}, elapsed_ms: 1, tool_calls: 1, guidance: [], media_inputs: [{ ...media, status: 'included' }],
      artifacts: [{ asset_id: media.asset_id, type: 'image', scope: 'download', path: media.relative_path,
        name: media.name, size: media.size, mime_type: media.mime_type, checksum_sha256: media.checksum_sha256 }],
    }] }))
    const wrap = (user: string, liveRun = live) => <QueryClientProvider client={client}>
      <ConversationFilesPopover user={user} sessionId="s1" liveRun={liveRun} />
    </QueryClientProvider>
    const view = render(wrap('alice'))
    expect(screen.getByRole('button', { name: '当前对话文件历史' })).toHaveTextContent('1')
    fireEvent.click(screen.getByRole('button', { name: '当前对话文件历史' }))
    const panel = screen.getByRole('region', { name: '当前对话文件历史' })
    expect(within(panel).getByText('媒体已排队')).toBeInTheDocument()
    await waitFor(() => expect(getHistory).toHaveBeenCalled())
    expect(within(panel).getAllByText('screen.png')).toHaveLength(1)
    expect(within(panel).getByRole('link', { name: '下载 screen.png' })).toHaveAttribute('href', expect.stringContaining('download'))
    expect(within(panel).queryByRole('button', { name: '引用 screen.png' })).toBeNull()
    view.rerender(wrap('alice', { ...live, phase: 'idle', items: [] }))
    expect(await screen.findByText('媒体已随请求发送')).toBeInTheDocument()
    fireEvent.click(within(panel).getByRole('button', { name: '在工作面板打开 screen.png' }))
    expect(screen.queryByRole('region')).toBeNull()
    expect(useWorkspaceStore.getState().users.alice.activeId).toBe('file:download:screens/screen.png')
    vi.mocked(getHistory).mockResolvedValue(history({ user: 'bob' }))
    view.rerender(wrap('bob', { ...live, items: [] }))
    fireEvent.click(screen.getByRole('button', { name: '当前对话文件历史' }))
    await screen.findByText('此对话还没有关联文件')
    expect(screen.queryByText('screen.png')).toBeNull()
  })
})
