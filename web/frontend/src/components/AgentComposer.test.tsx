import type { ComponentProps } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentComposer } from './AgentComposer'

afterEach(() => {
  delete (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
})

function renderComposer(overrides: Partial<ComponentProps<typeof AgentComposer>> = {}) {
  const props: ComponentProps<typeof AgentComposer> = {
    value: '',
    placeholder: '给 kemo-agent 发送消息…',
    onChange: vi.fn(),
    onOpenKnowledge: vi.fn(),
    onOpenCapabilities: vi.fn(),
    onSubmit: vi.fn(),
    ...overrides,
  }
  return { ...render(<AgentComposer {...props} />), props }
}

describe('AgentComposer', () => {
  it('uses the single stop action to stop the run and cancel subagents', () => {
    const onStopAndCancelSubagents = vi.fn()
    renderComposer({ running: true, onStop: vi.fn(), onStopAndCancelSubagents })
    expect(screen.getAllByRole('button', { name: '停止生成' })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '停止生成' }))
    expect(onStopAndCancelSubagents).toHaveBeenCalledOnce()
  })

  it('运行中 Enter 默认消息跟进，Ctrl+Enter 直接本轮引导，Shift+Enter 保留换行', () => {
    const onSubmit = vi.fn()
    const onGuide = vi.fn()
    const { rerender, props } = renderComposer({
      value: '跟进任务', running: true, onSubmit, onGuide, onStop: vi.fn(),
    })
    const input = screen.getByRole('textbox', { name: '消息内容' })
    expect(input).toHaveAttribute('aria-keyshortcuts', 'Control+Enter')
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true })
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true, isComposing: true })
    expect(onGuide).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledOnce()
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })
    expect(onGuide).toHaveBeenCalledOnce()
    expect(onSubmit).toHaveBeenCalledOnce()
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(onGuide).toHaveBeenCalledOnce()
    expect(onSubmit).toHaveBeenCalledOnce()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '停止生成' })).toBeInTheDocument()
    rerender(<AgentComposer {...props} uploading />)
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '停止生成' })).toBeInTheDocument()
  })

  it('空闲时 Enter 发送，Shift+Enter 和 Ctrl+Enter 均保留编辑行为', () => {
    const onSubmit = vi.fn()
    const onGuide = vi.fn()
    renderComposer({
      value: '检查状态',
      contextUsage: <button aria-label="查看上下文用量" />,
      onSubmit,
      onGuide,
    })
    expect(screen.getByRole('button', { name: '查看上下文用量' })).toBeInTheDocument()
    expect(document.querySelector('.reference-round-stat, .reference-round-bubble')).toBeNull()

    const input = screen.getByRole('textbox', { name: '消息内容' })
    expect(input).not.toHaveAttribute('aria-keyshortcuts')
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter', ctrlKey: true })
    expect(onSubmit).not.toHaveBeenCalled()
    expect(onGuide).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('运行中隐藏发送控件并保留停止能力，未接入的上传按钮保持禁用', () => {
    const onStop = vi.fn()
    renderComposer({ value: '补充要求', running: true, onStop })
    expect(screen.getByRole('button', { name: '上传文件' })).toBeDisabled()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '停止生成' }))
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('输入斜杠后显示命令列表，按文本过滤并可用回车执行', () => {
    const onChange = vi.fn()
    const onSlashCommand = vi.fn()
    const { props, rerender } = renderComposer({ value: '/', onChange, onSlashCommand })
    const input = screen.getByRole('textbox', { name: '消息内容' })
    expect(screen.getByRole('listbox', { name: '对话操作' })).toBeInTheDocument()
    expect(screen.getAllByRole('option')).toHaveLength(5)
    expect(screen.queryByRole('button', { name: /斜杠指令/ })).not.toBeInTheDocument()

    fireEvent.change(input, { target: { value: '/comp' } })
    rerender(<AgentComposer {...props} value="/comp" />)
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(screen.getByRole('option')).toHaveTextContent('/compress')

    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onChange).toHaveBeenCalledWith('')
    expect(onSlashCommand).toHaveBeenCalledWith('compress')
  })

  it('斜杠菜单锚定整个输入卡片而不是正文输入区域', () => {
    renderComposer({
      value: '/',
      modelBar: <div data-testid="composer-modelbar">模型选择</div>,
      uploadFeedback: <div>待发送附件</div>,
    })
    const composer = screen.getByRole('region', { name: '消息输入区域' })
    const menu = screen.getByRole('listbox', { name: '对话操作' })
    expect(menu.parentElement).toBe(composer)
    expect(menu.closest('.composer-input-wrap')).toBeNull()
    expect(screen.getByTestId('composer-modelbar').parentElement).toBe(composer)
    expect(screen.getByRole('textbox', { name: '消息内容' }).closest('.composer-input-wrap')).not.toBeNull()
  })

  it('斜杠菜单支持上下键选择和点击执行', () => {
    const onSlashCommand = vi.fn()
    renderComposer({ value: '/', onSlashCommand })
    const input = screen.getByRole('textbox', { name: '消息内容' })
    fireEvent.keyDown(input, { key: 'ArrowDown' })
    expect(screen.getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    fireEvent.click(screen.getByRole('option', { name: /清空此对话/ }))
    expect(onSlashCommand).toHaveBeenCalledWith('clear')
  })

  it('输入 /new 可直接执行新建而不是保存并新建', () => {
    const onSlashCommand = vi.fn()
    const onChange = vi.fn()
    renderComposer({ value: '/new', onSlashCommand, onChange })
    expect(screen.getAllByRole('option')).toHaveLength(1)
    expect(screen.getByRole('option')).toHaveTextContent('直接新建对话')
    fireEvent.keyDown(screen.getByRole('textbox', { name: '消息内容' }), { key: 'Enter' })
    expect(onSlashCommand).toHaveBeenCalledExactlyOnceWith('new')
    expect(onChange).toHaveBeenCalledWith('')
  })

  it('点击发送按钮也执行 /new，不把指令作为普通消息发送给智能体', () => {
    const onSlashCommand = vi.fn()
    const onSubmit = vi.fn()
    renderComposer({ value: '/new', onSlashCommand, onSubmit })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(onSlashCommand).toHaveBeenCalledExactlyOnceWith('new')
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it('Escape closes the slash menu without executing an item', () => {
    const onSlashCommand = vi.fn()
    renderComposer({ value: '/', onSlashCommand })
    fireEvent.keyDown(screen.getByRole('textbox', { name: '消息内容' }), { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(onSlashCommand).not.toHaveBeenCalled()
  })

  it('运行时只显示一个停止按钮，并统一取消当前子任务', () => {
    const onStop = vi.fn()
    const onStopAndCancelSubagents = vi.fn()
    renderComposer({ running: true, value: '补充要求', onStop, onStopAndCancelSubagents })
    expect(screen.getAllByRole('button', { name: '停止生成' })).toHaveLength(1)
    fireEvent.click(screen.getByRole('button', { name: '停止生成' }))
    expect(onStopAndCancelSubagents).toHaveBeenCalledOnce()
    expect(onStop).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
  })

  it('停止过渡期间把新文本作为下一轮提交，不会误投给旧运行', () => {
    const onSubmit = vi.fn()
    const onGuide = vi.fn()
    renderComposer({ value: '停止后继续处理', running: true, stopping: true, onSubmit, onGuide, onStop: vi.fn() })
    fireEvent.keyDown(screen.getByRole('textbox', { name: '消息内容' }), { key: 'Enter', ctrlKey: true })
    expect(onGuide).not.toHaveBeenCalled()
    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('textbox', { name: '消息内容' }), { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '停止生成' })).toBeDisabled()
  })

  it('通过隐藏文件选择器把用户选择的文件交给上传处理器', () => {
    const onUploadFiles = vi.fn()
    const { container } = renderComposer({ onUploadFiles })
    const uploadButton = screen.getByRole('button', { name: '上传文件' })
    expect(uploadButton).toBeEnabled()

    fireEvent.click(uploadButton)
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')
    const first = new File(['kemo upload'], 'browser-upload.txt', { type: 'text/plain' })
    const second = new File(['archive'], 'files.zip', { type: 'application/zip' })
    expect(input).not.toBeNull()
    expect(input).toHaveAttribute('multiple')
    fireEvent.change(input!, { target: { files: [first, second] } })
    expect(onUploadFiles).toHaveBeenCalledWith([first, second])
  })

  it('粘贴截图或资源管理器文件时交给上传处理器，纯文本保持浏览器默认行为', () => {
    const onUploadFiles = vi.fn()
    renderComposer({ onUploadFiles })
    const input = screen.getByRole('textbox', { name: '消息内容' })
    const screenshot = new File(['png'], 'image.png', { type: 'image/png' })
    const archive = new File(['zip'], 'files.zip', { type: 'application/zip' })

    const filePaste = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(filePaste, 'clipboardData', {
      value: {
        items: [
          { kind: 'file', getAsFile: () => screenshot },
          { kind: 'file', getAsFile: () => archive },
        ],
        files: [screenshot, archive],
      },
    })
    input.dispatchEvent(filePaste)
    expect(filePaste.defaultPrevented).toBe(true)
    expect(onUploadFiles).toHaveBeenCalledWith([screenshot, archive])

    const textPaste = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(textPaste, 'clipboardData', { value: { items: [], files: [] } })
    input.dispatchEvent(textPaste)
    expect(textPaste.defaultPrevented).toBe(false)
    expect(onUploadFiles).toHaveBeenCalledTimes(1)
  })

  it('只有待发送附件时允许发送，上传进行中保持禁用', () => {
    const onSubmit = vi.fn()
    const { rerender, props } = renderComposer({ pendingFileCount: 1, onSubmit })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(onSubmit).toHaveBeenCalledOnce()

    rerender(<AgentComposer {...props} pendingFileCount={1} uploading />)
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
  })

  it('运行中只有待发送附件时也允许提交引导', () => {
    const onSubmit = vi.fn()
    renderComposer({ running: true, pendingFileCount: 1, onSubmit })

    expect(screen.queryByRole('button', { name: '发送' })).not.toBeInTheDocument()
    fireEvent.keyDown(screen.getByRole('textbox', { name: '消息内容' }), { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('Boxes 按钮打开统一能力引用面板', () => {
    const onOpenCapabilities = vi.fn()
    renderComposer({ onOpenCapabilities })
    fireEvent.click(screen.getByRole('button', { name: '打开能力引用' }))
    expect(onOpenCapabilities).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: '打开技能' })).not.toBeInTheDocument()
  })

  it('在上传与知识库按钮之间使用浏览器中文语音识别并追加到草稿', () => {
    let recognition: FakeSpeechRecognition | null = null
    class FakeSpeechRecognition {
      lang = ''
      interimResults = true
      continuous = true
      onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null = null
      onerror: ((event: { error: string }) => void) | null = null
      onend: (() => void) | null = null
      start = vi.fn()
      stop = vi.fn()
      abort = vi.fn()
      constructor() { recognition = this }
    }
    Object.defineProperty(window, 'webkitSpeechRecognition', { configurable: true, writable: true, value: FakeSpeechRecognition })
    const onChange = vi.fn()
    renderComposer({ value: '已有内容', onChange })

    const uploadButton = screen.getByRole('button', { name: '上传文件' })
    const speechButton = screen.getByRole('button', { name: '语音识别' })
    const knowledgeButton = screen.getByRole('button', { name: '打开知识库' })
    expect(uploadButton.compareDocumentPosition(speechButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(speechButton.compareDocumentPosition(knowledgeButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(speechButton)
    expect(recognition).not.toBeNull()
    expect(recognition!.lang).toBe('zh-CN')
    expect(recognition!.interimResults).toBe(false)
    expect(recognition!.continuous).toBe(false)
    expect(recognition!.start).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '停止语音识别' })).toHaveAttribute('aria-pressed', 'true')

    act(() => {
      recognition!.onresult?.({ results: { 0: { 0: { transcript: '你好世界' }, length: 1 }, length: 1 } })
    })
    expect(onChange).toHaveBeenCalledWith('已有内容 你好世界')

    fireEvent.click(screen.getByRole('button', { name: '停止语音识别' }))
    expect(recognition!.stop).toHaveBeenCalledTimes(1)
  })
})
