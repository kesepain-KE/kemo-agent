import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, expect, it } from 'vitest'
import { getRuntimeStatus } from '../api/client'
import { server } from '../test/server'
import { languages, translate } from '../i18n'
import { useUiStore } from '../store/ui'
import { RuntimeCongestion } from './RuntimeCongestion'

afterEach(() => useUiStore.getState().setLanguage('zh-CN', ''))

it('refreshes only congestion at 1.5 seconds, preserves card nodes and returns to zero after requests finish', async () => {
  const data = await getRuntimeStatus('kesepain', 's1')
  let active = 3
  const requests: URL[] = []
  server.use(http.get('/api/users/kesepain/runtime/status', ({ request }) => {
    requests.push(new URL(request.url))
    return HttpResponse.json({ ...data, congestion: { ...data.congestion,
      provider: { active_requests: active, max_requests: 10, available_requests: 10 - active, waiting_estimate: 2 } } })
  }))
  const client = new QueryClient()
  render(<QueryClientProvider client={client}><RuntimeCongestion user="kesepain" sessionId="s1" initial={data.congestion} /></QueryClientProvider>)
  const pool = screen.getByRole('group', { name: 'Provider 请求' })
  await within(pool).findByText('3 / 10')
  expect(within(pool).getByText('可用 7')).toBeVisible()
  expect(within(pool).getByText('排队 2')).toBeVisible()
  expect(within(pool).getByRole('progressbar')).toHaveAttribute('aria-valuenow', '3')
  active = 0
  await waitFor(() => expect(pool).toHaveTextContent('0 / 10'), { timeout: 3500 })
  expect(screen.getByRole('group', { name: 'Provider 请求' })).toBe(pool)
  expect(pool).toHaveTextContent('可用 10')
  expect(requests.length).toBeGreaterThanOrEqual(2)
  for (const url of requests) {
    expect(url.searchParams.get('sections')).toBe('congestion')
    expect(url.searchParams.get('session_id')).toBe('s1')
  }
})

it('keeps the last successful values on refresh failure and supports explicit retry', async () => {
  const data = await getRuntimeStatus('kesepain', 's1')
  let fail = true
  server.use(http.get('/api/users/kesepain/runtime/status', () => fail
    ? HttpResponse.json({ error: { message: 'unavailable' } }, { status: 503 })
    : HttpResponse.json(data)))
  const client = new QueryClient()
  render(<QueryClientProvider client={client}><RuntimeCongestion user="kesepain" sessionId="s1" initial={data.congestion} /></QueryClientProvider>)
  expect(await screen.findByRole('status')).toHaveTextContent('刷新失败，当前显示上次成功读取的数据。')
  expect(screen.getByRole('group', { name: 'Provider 请求' })).toHaveTextContent('2 / 10')
  fail = false
  fireEvent.click(screen.getByRole('button', { name: '刷新并发状态' }))
  await waitFor(() => expect(screen.queryByRole('status')).toBeNull())
})

it('cancels in-flight snapshots and removes its query when unmounted', async () => {
  const data = await getRuntimeStatus('kesepain', 's1')
  let requested = false
  let aborted = false
  server.use(http.get('/api/users/kesepain/runtime/status', async ({ request }) => {
    requested = true
    await new Promise<void>(resolve => request.signal.addEventListener('abort', () => { aborted = true; resolve() }, { once: true }))
    return HttpResponse.json(data)
  }))
  const client = new QueryClient()
  const view = render(<QueryClientProvider client={client}><RuntimeCongestion user="kesepain" sessionId="s1" initial={data.congestion} /></QueryClientProvider>)
  await waitFor(() => expect(requested).toBe(true))
  view.unmount()
  await waitFor(() => expect(aborted).toBe(true))
  await waitFor(() => expect(client.getQueryData(['runtime-congestion', 'kesepain', 's1'])).toBeUndefined())
})

it('isolates users and sessions in the query key and localizes the pool without translating values', async () => {
  const data = await getRuntimeStatus('kesepain', 's1')
  act(() => useUiStore.getState().setLanguage('en', 'kesepain'))
  const client = new QueryClient()
  render(<QueryClientProvider client={client}><RuntimeCongestion user="kesepain" sessionId="different-space" initial={data.congestion} /></QueryClientProvider>)
  expect(screen.getByRole('region', { name: 'Congestion and concurrency' })).toBeVisible()
  expect(screen.getByRole('group', { name: 'Provider requests' })).toHaveTextContent('2 / 10')
  expect(client.getQueryData(['runtime-congestion', 'kesepain', 's1'])).toBeUndefined()
  expect(client.getQueryData(['runtime-congestion', 'kesepain', 'different-space'])).toBeDefined()
  for (const { code } of languages) {
    for (const label of ['拥塞与并发', 'Provider 请求', 'Web 聊天槽位', '消息工作线程', '占用', '可用', '排队', '刷新并发状态',
      'Provider 请求池仅统计模型请求，工具执行期间不占用。', '刷新失败，当前显示上次成功读取的数据。']) {
      expect(translate(label, code)).toBeTruthy()
      if (code !== 'zh-CN') expect(translate(label, code)).not.toBe(label)
    }
  }
})
