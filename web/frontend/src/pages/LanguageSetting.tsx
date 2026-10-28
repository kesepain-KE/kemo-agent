import { useMutation, useQueryClient } from '@tanstack/react-query'
import { patchUserConfig } from '../api/client'
import { languages, normalizeLanguage, t, useLanguage, type Language } from '../i18n'
import { cacheLanguage, useUiStore } from '../store/ui'
import type { PreferencesResponse } from '../types/api'

/** Only first-party chrome changes language; never reload the application or chat. */
export function LanguageSetting({ user }: { user: string }) {
  const language = useLanguage()
  const client = useQueryClient()
  const update = (owner: string, value: Language) => {
    cacheLanguage(value, owner)
    if (useUiStore.getState().languageUser === owner) {
      useUiStore.getState().setLanguage(value, owner)
    }
    client.setQueryData<PreferencesResponse>(['preferences', owner], previous => ({
      user: owner,
      appearance: {
        theme: useUiStore.getState().theme,
        font_size: useUiStore.getState().fontSize,
        ...previous?.appearance,
        language: value,
      },
    }))
  }
  const save = useMutation({
    mutationFn: ({ owner, target }: { owner: string; target: Language; previous: Language }) =>
      patchUserConfig(owner, { web_ui: { language: target } }),
    onMutate: async ({ owner, target }) => {
      // A late initial GET must not overwrite the optimistic selection.
      await client.cancelQueries({ queryKey: ['preferences', owner] })
      update(owner, target)
    },
    onSuccess: (_response, { owner, target }) => {
      update(owner, target)
      // Do not invalidate user-config: that would reset unsaved Provider drafts.
    },
    onError: (_error, { owner, previous }) => update(owner, previous),
  })
  return <article className="setting-section language-setting">
    <div className="setting-section-head"><strong>{t('界面语言')}</strong>
      <span>{t('选择当前用户的网页语言')}</span></div>
    <div className="setting-row language-setting-row">
      <label className="setting-copy" htmlFor="web-language"><strong>{t('网页语言')}</strong>
        <span>{t('语言保存在用户配置 web_ui.language 中，仅切换界面，不翻译对话、文件或模型输出。')}</span></label>
      <select id="web-language" className="select language-select" aria-label={t('网页语言')}
        value={language} disabled={!user || save.isPending} dir="auto"
        onChange={event => {
          const target = normalizeLanguage(event.target.value)
          if (target !== language && !save.isPending) save.mutate({ owner: user, target, previous: language })
        }}>
        {languages.map(item => <option key={item.code} value={item.code} lang={item.code} dir={item.direction}>{item.name}</option>)}
      </select>
    </div>
    {save.isPending && <div className="settings-inline-feedback" role="status">{t('保存中…')}</div>}
    {save.isSuccess && <div className="settings-inline-feedback" role="status">{t('语言已保存')}</div>}
    {save.isError && <div className="settings-inline-feedback" role="alert">{t('语言保存失败，已恢复原语言，请重试。')}</div>}
  </article>
}
