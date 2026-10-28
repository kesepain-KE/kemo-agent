import { useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { t, useLanguage } from '../i18n'
import styles from './InventoryPager.module.css'

/** Filter first, then page; scope changes reset immediately without a stale frame. */
export function useInventoryPage<T>(items: T[], scope: string, size = 6) {
  const [cursor, setCursor] = useState({ scope, page: 1 })
  const totalPages = Math.max(1, Math.ceil(items.length / size))
  const page = cursor.scope === scope ? Math.min(cursor.page, totalPages) : 1
  const setPage = (next: number) => setCursor({ scope, page: Math.max(1, Math.min(next, totalPages)) })
  return { page, totalPages, setPage, visible: items.slice((page - 1) * size, page * size) }
}

export function InventoryPager({ page, totalPages, total, label, onChange }: {
  page: number; totalPages: number; total: number; label: string; onChange: (page: number) => void
}) {
  useLanguage()
  return <nav className={styles.pager} aria-label={`${label}${t('分页')}`}>
    <span>{t('共 {count} 个{label} · 每页最多 {size} 个', { count: total, label, size: 6 })}</span>
    <div>
      <button type="button" aria-label={`${t('上一页')}${label}`} disabled={page <= 1} onClick={() => onChange(page - 1)}><ChevronLeft size={15} /></button>
      <strong>{page} / {totalPages}</strong>
      <button type="button" aria-label={`${t('下一页')}${label}`} disabled={page >= totalPages} onClick={() => onChange(page + 1)}><ChevronRight size={15} /></button>
    </div>
  </nav>
}
