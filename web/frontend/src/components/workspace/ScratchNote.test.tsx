import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { ScratchNote } from './ScratchNote'
import { scratchKey, useScratchNotes, useScratchSender } from '../../store/scratchNotes'
import { useChatDraftStore } from '../../store/chatDrafts'
import { languages } from '../../i18n/locales'
import { translate } from '../../i18n'
import { scratchMessages } from '../../i18n/scratch'

beforeEach(() => {
  localStorage.clear()
  useScratchNotes.setState({ notes: {} })
  useScratchSender.setState({ handlers: {} })
  useChatDraftStore.getState().clearAll()
})

it('persists locally across unmount and isolates users and conversations', () => {
  const view = render(<ScratchNote user="alice" sessionId="s1" />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'private draft' } })
  view.rerender(<ScratchNote user="alice" sessionId="s2" />)
  expect(screen.getByRole('textbox')).toHaveValue('')
  view.rerender(<ScratchNote user="bob" sessionId="s1" />)
  expect(screen.getByRole('textbox')).toHaveValue('')
  view.unmount()
  useScratchNotes.setState({ notes: {} })
  render(<ScratchNote user="alice" sessionId="s1" />)
  expect(screen.getByRole('textbox')).toHaveValue('private draft')
})

it('does not overwrite a nonempty composer without an explicit append or replace', () => {
  useChatDraftStore.getState().setText(scratchKey('alice', 's1'), 'existing')
  render(<ScratchNote user="alice" sessionId="s1" />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'new' } })
  fireEvent.click(screen.getByRole('button', { name: '放入聊天框' }))
  expect(useChatDraftStore.getState().drafts[scratchKey('alice', 's1')].text).toBe('existing')
  fireEvent.click(screen.getByRole('button', { name: '追加' }))
  expect(useChatDraftStore.getState().drafts[scratchKey('alice', 's1')].text).toBe('existing\n\nnew')
  fireEvent.click(screen.getByRole('button', { name: '放入聊天框' }))
  fireEvent.click(screen.getByRole('button', { name: '替换' }))
  expect(useChatDraftStore.getState().drafts[scratchKey('alice', 's1')].text).toBe('new')
})

it('preserves failed sends and edits made during an accepted send; plain Enter does not send', async () => {
  let release: (value: boolean) => void = () => {}
  const handler = vi.fn(() => new Promise<boolean>(resolve => { release = resolve }))
  useScratchSender.getState().bind(scratchKey('alice', 's1'), handler)
  render(<ScratchNote user="alice" sessionId="s1" />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'first' } })
  fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' })
  expect(handler).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: '发送给智能体' }))
  await act(async () => release(false))
  expect(screen.getByRole('textbox')).toHaveValue('first')
  fireEvent.click(screen.getByRole('button', { name: '发送给智能体' }))
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'newer typing' } })
  await act(async () => release(true))
  expect(screen.getByRole('textbox')).toHaveValue('newer typing')
  fireEvent.click(screen.getByRole('button', { name: '发送给智能体' }))
  await act(async () => release(true))
  await waitFor(() => expect(screen.getByRole('textbox')).toHaveValue(''))
})

it('clear requires confirmation; persistence failure remains visible and retains text', () => {
  const spy = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw Error('quota') })
  render(<ScratchNote user="alice" sessionId="s1" />)
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'keep' } })
  expect(screen.getByText('本机保存失败，请勿关闭页面')).toBeVisible()
  fireEvent.click(screen.getByRole('button', { name: '清空' }))
  expect(screen.getByRole('textbox')).toHaveValue('keep')
  fireEvent.click(screen.getByRole('button', { name: '取消' }))
  expect(screen.getByRole('textbox')).toHaveValue('keep')
  spy.mockRestore()
})

it('supplies all eight translations for new UI messages', () => {
  for (const row of scratchMessages.trim().split('\n')) {
    const [source, ...translations] = row.split('|')
    expect(translations).toHaveLength(8)
    for (const language of languages.slice(1)) expect(translate(source, language.code)).not.toBe(source)
  }
})
