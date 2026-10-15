import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, LoaderCircle, RefreshCw, Search } from 'lucide-react'
import { ApiError, getChatProviderModels, getKemoProviderModels, patchUserConfig } from '../api/client'
import type { OverviewResponse, ProviderSummary } from '../types/api'
import { ReferenceIcon } from '../reference/ReferenceIcon'
import styles from './ComposerModelSelect.module.css'

interface ComposerModelSelectProps {
  user: string
  provider?: ProviderSummary
  disabled?: boolean
}

export function ComposerModelSelect({ user, provider, disabled = false }: ComposerModelSelectProps) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const forceRefreshRef = useRef(false)
  const triggerRef = useRef<HTMLButtonElement>(null)
  const popoverRef = useRef<HTMLDivElement>(null)
  const searchRef = useRef<HTMLInputElement>(null)
  const listId = useId()
  const [position, setPosition] = useState({
    left: 12, bottom: 12 as number | undefined, top: undefined as number | undefined, width: 320, maxHeight: 420,
  })
  const protocol = provider?.type
  const available = Boolean(user && provider?.configured && (protocol === 'chat' || protocol === 'kemo'))
  const configSaving = useIsMutating({ mutationKey: ['provider-config', user] }) > 0
  const blocked = disabled || configSaving || !available
  const modelsQuery = useQuery({
    queryKey: ['composer-provider-models', user, protocol, provider?.base_url],
    queryFn: async () => {
      if (protocol === 'kemo') {
        const response = await getKemoProviderModels(user, forceRefreshRef.current)
        return response.data.filter((model) => model.task === 'llm')
          .map((model) => ({ id: model.id, detail: model.provider_id }))
      }
      const response = await getChatProviderModels(user)
      return response.data.map((model) => ({ id: model.id, detail: model.owned_by }))
    },
    enabled: open && available,
    staleTime: 300_000,
    retry: false,
  })
  const save = useMutation({
    mutationKey: ['provider-config', user],
    mutationFn: ({ targetUser, model }: { targetUser: string; model: string }) =>
      patchUserConfig(targetUser, { provider: { model } }),
    onSuccess: async (_, { targetUser, model }) => {
      // 只在保存成功后更新共享概览，顶栏、输入坞与新模型的能力查询保持一致。
      client.setQueriesData<OverviewResponse>({ queryKey: ['overview', targetUser] }, (current) => current
        ? { ...current, provider: { ...current.provider, model } } : current)
      await Promise.all(['overview', 'settings', 'user-config', 'runtime-status'].map((key) =>
        client.invalidateQueries({ queryKey: [key, targetUser] })))
      setOpen(false)
      triggerRef.current?.focus()
    },
  })
  const models = Array.from(new Map((modelsQuery.data ?? []).map((model) => [model.id, model])).values())
  const visibleModels = models.filter((model) => `${model.id} ${model.detail}`.toLocaleLowerCase()
    .includes(search.trim().toLocaleLowerCase()))
  const discoveryError = modelsQuery.error instanceof ApiError
    ? modelsQuery.error.status === 404 && modelsQuery.error.code === 'not_found'
      ? '当前后端尚未加载模型列表接口，请重启智能体后重试。'
      : modelsQuery.error.message
    : '请刷新重试或检查 Provider 配置。'

  useEffect(() => {
    // 切用户/协议/端点、开始运行时，不保留旧列表或错误。
    setOpen(false)
    setSearch('')
    forceRefreshRef.current = false
    save.reset()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, protocol, provider?.base_url, disabled])

  useLayoutEffect(() => {
    if (!open) return
    const updatePosition = () => {
      const rect = triggerRef.current?.getBoundingClientRect()
      if (!rect) return
      const width = Math.min(Math.max(rect.width, 320), window.innerWidth - 24)
      const above = rect.top - 20
      const below = window.innerHeight - rect.bottom - 20
      const upward = above >= 180 || above >= below
      setPosition({
        width,
        left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)),
        bottom: upward ? window.innerHeight - rect.top + 8 : undefined,
        top: upward ? undefined : rect.bottom + 8,
        maxHeight: Math.max(80, Math.min(420, upward ? above : below)),
      })
    }
    updatePosition()
    searchRef.current?.focus()
    const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(updatePosition)
    if (triggerRef.current) observer?.observe(triggerRef.current)
    const composer = triggerRef.current?.closest('.composer-inner')
    if (composer) observer?.observe(composer)
    window.addEventListener('resize', updatePosition)
    window.addEventListener('scroll', updatePosition, true)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', updatePosition)
      window.removeEventListener('scroll', updatePosition, true)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const dismiss = (event: PointerEvent) => {
      const target = event.target as Node
      if (!triggerRef.current?.contains(target) && !popoverRef.current?.contains(target)) setOpen(false)
    }
    const escape = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.preventDefault()
      event.stopPropagation()
      setOpen(false)
      triggerRef.current?.focus()
    }
    document.addEventListener('pointerdown', dismiss)
    document.addEventListener('keydown', escape, true)
    return () => {
      document.removeEventListener('pointerdown', dismiss)
      document.removeEventListener('keydown', escape, true)
    }
  }, [open])

  const openPicker = () => {
    if (blocked) return
    setSearch('')
    save.reset()
    setOpen((current) => !current)
  }
  const refreshModels = async () => {
    forceRefreshRef.current = true
    try { await modelsQuery.refetch() } finally { forceRefreshRef.current = false }
  }

  return <>
    <button ref={triggerRef} type="button" className={`model-pill ${styles.trigger}`}
      aria-label="输入框切换模型" aria-haspopup="dialog" aria-expanded={open}
      aria-controls={open ? listId : undefined} disabled={blocked}
      title={disabled ? '当前对话运行或操作中，结束后可切换模型' : available ? '切换模型' : '请先配置 Provider'}
      onClick={openPicker}
      onKeyDown={(event) => {
        if (event.key === 'ArrowDown' && !open && !blocked) { event.preventDefault(); openPicker() }
      }}>
      <ReferenceIcon name="cpu" small /><span>{provider?.model || '尚未配置模型'}</span>
      {save.isPending ? <LoaderCircle className={styles.spinner} size={13} /> : <ChevronDown size={13} />}
    </button>
    {open && createPortal(
      <div ref={popoverRef} id={listId} className={styles.popover} style={position}
        role="dialog" aria-label="切换对话模型">
        <header className={styles.header}>
          <div><strong>切换模型</strong><small>{protocol === 'kemo' ? 'KEMO · 内置模型目录' : 'Chat · 模型 API 列表'}</small></div>
          <button type="button" aria-label="刷新模型列表" title="刷新模型列表"
            disabled={modelsQuery.isFetching || configSaving} onClick={() => { void refreshModels() }}>
            <RefreshCw size={15} className={modelsQuery.isFetching ? styles.spinner : ''} />
          </button>
        </header>
        <label className={styles.search}><Search size={15} />
          <input ref={searchRef} value={search} onChange={(event) => setSearch(event.target.value)}
            aria-label="搜索可用模型" placeholder="搜索模型…"
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault()
                popoverRef.current?.querySelector<HTMLButtonElement>('[role="option"]:not(:disabled)')?.focus()
              }
            }} />
        </label>
        {modelsQuery.isFetching && <span className={styles.status} role="status">正在获取模型列表…</span>}
        {modelsQuery.isError && <div className={styles.error} role="alert">模型列表获取失败：{discoveryError}</div>}
        {save.isError && <div className={styles.error} role="alert">模型切换失败，已保留原模型，请重试。</div>}
        {save.isPending && <span className={styles.status} role="status">正在保存模型…</span>}
        <div className={styles.options} role="listbox" aria-label="可用对话模型"
          onKeyDown={(event) => {
            if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return
            event.preventDefault()
            const buttons = Array.from(popoverRef.current?.querySelectorAll<HTMLButtonElement>('[role="option"]:not(:disabled)') ?? [])
            if (!buttons.length) return
            const index = buttons.indexOf(document.activeElement as HTMLButtonElement)
            const next = event.key === 'Home' ? 0 : event.key === 'End' ? buttons.length - 1
              : (index + (event.key === 'ArrowDown' ? 1 : -1) + buttons.length) % buttons.length
            buttons[next]?.focus()
          }}>
          {visibleModels.map((model) => <button type="button" role="option" key={model.id}
            aria-selected={model.id === provider?.model} disabled={configSaving || modelsQuery.isFetching}
            className={model.id === provider?.model ? styles.selected : ''}
            onClick={() => {
              if (model.id === provider?.model) { setOpen(false); triggerRef.current?.focus(); return }
              save.mutate({ targetUser: user, model: model.id })
            }}>
            <span><strong>{model.id}</strong>{model.detail && <small>{model.detail}</small>}</span>
            {model.id === provider?.model && <Check size={15} />}
          </button>)}
          {!modelsQuery.isPending && !modelsQuery.isError && !visibleModels.length
            && <span className={styles.status}>{search ? '没有匹配的模型' : '当前接口未返回可用模型'}</span>}
        </div>
      </div>, document.body,
    )}
  </>
}
