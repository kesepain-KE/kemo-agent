import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AdvancedSettings } from './AdvancedSettings'

describe('AdvancedSettings', () => {
  it('saves only changed global fields with nested paths, explicit null and ordered retry delays', () => {
    const onSave = vi.fn()
    render(<AdvancedSettings config={{ memory: { profile: 'companion' }, message: { max_workers: 8 } }} pending={false} saved={false} onSave={onSave} />)
    fireEvent.change(screen.getByRole('combobox', { name: '默认记忆模式' }), { target: { value: 'work' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '关系记忆晋升阈值' }), { target: { value: '' } })
    fireEvent.change(screen.getByRole('spinbutton', { name: '媒体资源存活时间（秒）' }), { target: { value: '180' } })
    fireEvent.change(screen.getByRole('textbox', { name: '标题摘要重试间隔（秒）' }), { target: { value: '2, 5, 10' } })
    fireEvent.change(screen.getByRole('textbox', { name: '额外允许的 Provider 主机' }), { target: { value: 'models.example.com\napi.example.com' } })
    fireEvent.click(screen.getByRole('button', { name: '保存高级配置' }))
    expect(onSave).toHaveBeenCalledExactlyOnceWith({ label: '保存高级配置', globalChanges: {
      memory: { profile: 'work', domain_rules: { relation: { upgrade_threshold: null } } },
      media_assets: { ttl_seconds: 180 }, history_summary: { retry_delays_seconds: [2, 5, 10] },
      provider_security: { allowed_hosts: ['models.example.com', 'api.example.com'] },
    } })
    expect(screen.queryByRole('spinbutton', { name: '周记忆下一层' })).not.toBeInTheDocument()
  })

  it('rejects invalid numeric input before issuing a patch', () => {
    const onSave = vi.fn()
    render(<AdvancedSettings config={{}} pending={false} saved={false} onSave={onSave} />)
    fireEvent.change(screen.getByRole('spinbutton', { name: '消息工作线程数' }), { target: { value: '0' } })
    fireEvent.click(screen.getByRole('button', { name: '保存高级配置' }))
    expect(onSave).toHaveBeenCalledExactlyOnceWith({ label: '保存高级配置' }, expect.stringContaining('消息工作线程数'))
  })

  it('loads actual values after save and does not overwrite untouched fields', () => {
    const onSave = vi.fn()
    const app = render(<AdvancedSettings config={{ media_assets: { ttl_seconds: 180 }, tools: { enabled: false } }} pending={false} saved={false} onSave={onSave} />)
    expect(screen.getByRole('switch', { name: '允许调用工具' })).not.toBeChecked()
    fireEvent.change(screen.getByRole('spinbutton', { name: '媒体资源存活时间（秒）' }), { target: { value: '240' } })
    app.rerender(<AdvancedSettings config={{ media_assets: { ttl_seconds: 240 }, tools: { enabled: false } }} pending={false} saved onSave={onSave} />)
    expect(screen.getByRole('spinbutton', { name: '媒体资源存活时间（秒）' })).toHaveValue(240)
    fireEvent.click(screen.getByRole('button', { name: '保存高级配置' }))
    expect(onSave).not.toHaveBeenCalled()
  })
})
