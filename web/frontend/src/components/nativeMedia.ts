import type { ChatItem, NativeMediaInput, RunEvent } from '../types/api'
import { t } from '../i18n'

const object = (value: unknown): Record<string, unknown> | null =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null

export function parseNativeInputs(value: unknown, callId = '', status = 'queued'): NativeMediaInput[] {
  if (!Array.isArray(value)) return []
  return value.slice(0, 32).flatMap(raw => {
    const item = object(raw)
    if (!item || typeof item.asset_id !== 'string' || !/^asset_[\w.:-]+$/.test(item.asset_id)
      || typeof item.name !== 'string' || !item.name) return []
    const path = typeof item.relative_path === 'string' ? item.relative_path : ''
    const managed = ['download', 'file_upload'].includes(String(item.scope))
      && Boolean(path) && !/^[\\/]|[:\\\x00]/.test(path)
      && !path.split('/').some(part => !part || part === '.' || part === '..')
    return [{
      asset_id: item.asset_id, name: item.name, media_kind: ['image', 'audio', 'video', 'file'].includes(String(item.media_kind)) ? item.media_kind : 'file',
      mime_type: typeof item.mime_type === 'string' ? item.mime_type : 'application/octet-stream',
      size: typeof item.size === 'number' && Number.isFinite(item.size) ? Math.max(0, item.size) : 0,
      checksum_sha256: typeof item.checksum_sha256 === 'string' ? item.checksum_sha256 : '',
      scope: managed ? item.scope : 'external', relative_path: managed ? path : '',
      available: managed && item.available === true,
      status: ['queued', 'prepared', 'included', 'failed', 'not_sent', 'unconfirmed'].includes(String(item.status)) ? item.status : status,
      tool_call_id: callId || (typeof item.tool_call_id === 'string' ? item.tool_call_id : ''),
      attachment_id: typeof item.attachment_id === 'string' ? item.attachment_id : '',
      error: typeof item.error === 'string' ? item.error : undefined,
    } as NativeMediaInput]
  })
}

export function toolNativeInputs(item: Extract<ChatItem, { kind: 'tool' }>): NativeMediaInput[] {
  if (item.name !== 'attach_media') return []
  if (item.mediaInputs) return parseNativeInputs(item.mediaInputs, item.callId)
  let value = item.result
  if (!value && item.resultText && item.resultText.length <= 250_000) {
    try { value = JSON.parse(item.resultText) } catch { return [] }
  }
  const wrapper = object(value)
  if (wrapper?.ok !== true) return []
  const receipt = object(wrapper.result)
  return receipt?.delivery === 'native_main' ? parseNativeInputs(receipt.assets, item.callId) : []
}

export function reduceNativeMediaInputs(items: ChatItem[], event: RunEvent, start: number, terminal: boolean): ChatItem[] {
  const updates = parseNativeInputs(event.metadata?.media_inputs)
  return items.map((item, index) => {
    if (index < start || item.kind !== 'tool' || item.name !== 'attach_media') return item
    const mediaInputs = toolNativeInputs(item).map(input => {
      const update = updates.find(value => value.asset_id === input.asset_id)
      const next = update ? { ...input, ...update, tool_call_id: item.callId } : input
      return terminal && ['queued', 'prepared'].includes(next.status)
        ? { ...next, status: Array.isArray(event.metadata?.media_inputs) ? 'not_sent' as const : 'unconfirmed' as const } : next
    })
    return { ...item, mediaInputs }
  })
}

export const nativeMediaStatus = (status: string) => t(({
  queued: '媒体已排队', prepared: '媒体已准备', included: '媒体已随请求发送',
  failed: '媒体装载失败', not_sent: '本轮未发送', unconfirmed: '发送状态未确认',
} as Record<string, string>)[status] || '媒体已排队')
