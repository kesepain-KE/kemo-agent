import { AlertCircle, BrainCircuit, Check } from 'lucide-react'
import type { CompressionStatus } from '../store/compressionStatus'

export function CompressionStatusIndicator({ status, className = '' }: { status: CompressionStatus; className?: string }) {
  const failed = status.status === 'failed'
  const complete = status.status === 'ready'
  const title = failed ? '压缩失败' : complete ? '压缩完成' : '正在压缩'
  const detail = status.absorbedCards > 0
    ? `整理了 ${status.absorbedCards} 张历史卡片`
    : failed ? '原有上下文保持不变' : '正在整理当前上下文'
  return (
    <div className={`compression-status ${status.status} ${className}`.trim()} role="status" aria-live="polite">
      <span className="compression-status-icon" aria-hidden="true">
        {failed ? <AlertCircle size={13} /> : complete ? <Check size={13} /> : <BrainCircuit className="compression-status-spin" size={13} />}
      </span>
      <span className="compression-status-label">{title}</span>
      <span className="compression-status-detail">{detail}</span>
    </div>
  )
}
