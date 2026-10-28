import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { createMemoryRouter, RouterProvider } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { AppShell } from '../components/AppShell'
import { useUiStore } from '../store/ui'
import { server } from '../test/server'
import { languages } from '../i18n'
import { LanguageSetting } from './LanguageSetting'
import { SettingsPage } from './SettingsPage'

beforeEach(() => useUiStore.getState().setLanguage('zh-CN', 'kesepain'))
afterEach(() => {
  for (const user of ['kesepain', 'alice', 'bob']) localStorage.removeItem(`kemo-language:${user}`)
  useUiStore.getState().setLanguage('zh-CN', '')
})

function mountSetting(user = 'kesepain') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  client.setQueryData(['preferences', user], { user, appearance: { theme: 'dark', font_size: 'large', language: 'zh-CN' } })
  client.setQueryData(['user-config', user], { config: { provider: { model: 'draft-should-not-reset' } } })
  return { client, ...render(<QueryClientProvider client={client}><LanguageSetting user={user} /></QueryClientProvider>) }
}

describe('language selector and persistence', () => {
  it('offers native language names and saves only the user-owned language field', async () => {
    let body: unknown
    server.use(http.patch('/api/users/kesepain/config', async ({ request }) => {
      body = await request.json()
      return HttpResponse.json({ updated: true })
    }))
    const { client } = mountSetting()
    const select = screen.getByRole('combobox', { name: '网页语言' })
    expect(within(select).getAllByRole('option').map(option => option.textContent)).toEqual(languages.map(item => item.name))
    expect(within(select).getAllByRole('option')).toHaveLength(9)
    expect(select.querySelector('option[value="ar"]')).toBeNull()
    fireEvent.change(select, { target: { value: 'en' } })
    expect(await screen.findByText('Language saved')).toBeInTheDocument()
    expect(body).toEqual({ changes: { web_ui: { language: 'en' } } })
    expect(document.documentElement.lang).toBe('en')
    expect(localStorage.getItem('kemo-language:kesepain')).toBe('en')
    expect(client.getQueryData(['preferences', 'kesepain'])).toMatchObject({
      appearance: { theme: 'dark', font_size: 'large', language: 'en' },
    })
    expect(client.getQueryData(['user-config', 'kesepain'])).toEqual({ config: { provider: { model: 'draft-should-not-reset' } } })
  })

  it('rolls back the UI, document direction and cache on save failure', async () => {
    server.use(http.patch('/api/users/kesepain/config', () => HttpResponse.json({ error: { message: 'save failed' } }, { status: 500 })))
    const { client } = mountSetting()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'de' } })
    expect(await screen.findByRole('alert')).toHaveTextContent('语言保存失败，已恢复原语言，请重试。')
    expect(document.documentElement.lang).toBe('zh-CN')
    expect(document.documentElement.dir).toBe('ltr')
    expect(screen.getByRole('combobox')).toHaveValue('zh-CN')
    expect(client.getQueryData(['preferences', 'kesepain'])).toMatchObject({ appearance: { language: 'zh-CN' } })
    expect(localStorage.getItem('kemo-language:kesepain')).toBe('zh-CN')
  })

  it.each([true, false])('late completion after a user switch cannot change the new user (success=%s)', async success => {
    let finish!: () => void
    const gate = new Promise<void>(resolve => { finish = resolve })
    server.use(http.patch('/api/users/kesepain/config', async () => {
      await gate
      return HttpResponse.json(success ? { updated: true } : { error: { message: 'failed' } }, { status: success ? 200 : 500 })
    }))
    const { client } = mountSetting()
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'de' } })
    await waitFor(() => expect(screen.getByRole('combobox')).toBeDisabled())
    act(() => useUiStore.getState().setLanguage('ja', 'bob'))
    await act(async () => { finish(); await gate })
    await waitFor(() => expect(client.isMutating()).toBe(0))
    expect(useUiStore.getState().language).toBe('ja')
    expect(useUiStore.getState().languageUser).toBe('bob')
    expect(document.documentElement.lang).toBe('ja')
    expect(localStorage.getItem('kemo-language:bob')).toBe('ja')
    expect(localStorage.getItem('kemo-language:kesepain')).toBe(success ? 'de' : 'zh-CN')
  })

  it('hydrates the server locale on reload and switches navigation without reloading', async () => {
    let language = 'de'
    server.use(
      http.get('/api/users/kesepain/preferences', () => HttpResponse.json({ user: 'kesepain', appearance: { theme: 'light', font_size: 'medium', language } })),
      http.patch('/api/users/kesepain/config', async ({ request }) => {
        const body = await request.json() as { changes: { web_ui: { language: string } } }
        language = body.changes.web_ui.language
        return HttpResponse.json({ updated: true })
      }),
    )
    const router = createMemoryRouter([{ path: '/', element: <AppShell />, children: [{ path: 'settings', element: <SettingsPage /> }] }], {
      initialEntries: ['/settings?user=kesepain&session=s1'],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
    const select = await screen.findByRole('combobox', { name: 'Websprache' })
    expect(select).toHaveValue('de')
    expect(screen.getByRole('link', { name: 'Wissensdatenbank' })).toBeInTheDocument()
    fireEvent.change(select, { target: { value: 'en' } })
    expect(await screen.findByText('Language saved')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Knowledge' })).toBeInTheDocument()
    expect(screen.getByRole('radio', { name: 'Light' })).toBeInTheDocument()
    expect(document.documentElement.lang).toBe('en')
    expect(router.state.location.pathname).toBe('/settings')
  })

  it('hydrates a retired Arabic server preference as Chinese without RTL', async () => {
    localStorage.setItem('kemo-language:kesepain', 'ar')
    document.documentElement.lang = 'ar'
    document.documentElement.dir = 'rtl'
    server.use(http.get('/api/users/kesepain/preferences', () => HttpResponse.json({
      user: 'kesepain', appearance: { theme: 'light', font_size: 'medium', language: 'ar' },
    })))
    const router = createMemoryRouter([{ path: '/', element: <AppShell />, children: [{ path: 'settings', element: <SettingsPage /> }] }], {
      initialEntries: ['/settings?user=kesepain&session=s1'],
    })
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    render(<QueryClientProvider client={client}><RouterProvider router={router} /></QueryClientProvider>)
    const select = await screen.findByRole('combobox', { name: '网页语言' })
    await waitFor(() => expect(client.getQueryData(['preferences', 'kesepain'])).toMatchObject({
      appearance: { language: 'ar' },
    }))
    expect(select).toHaveValue('zh-CN')
    expect(select.querySelector('option[value="ar"]')).toBeNull()
    expect(document.documentElement.lang).toBe('zh-CN')
    expect(document.documentElement.dir).toBe('ltr')
    expect(localStorage.getItem('kemo-language:kesepain')).toBe('zh-CN')
  })
})
