export const languages = [
  { code: 'zh-CN', name: '简体中文', direction: 'ltr' },
  { code: 'en', name: 'English', direction: 'ltr' },
  { code: 'es', name: 'Español', direction: 'ltr' },
  { code: 'fr', name: 'Français', direction: 'ltr' },
  { code: 'de', name: 'Deutsch', direction: 'ltr' },
  { code: 'pt-BR', name: 'Português (Brasil)', direction: 'ltr' },
  { code: 'ru', name: 'Русский', direction: 'ltr' },
  { code: 'ja', name: '日本語', direction: 'ltr' },
  { code: 'ko', name: '한국어', direction: 'ltr' },
] as const

export type Language = typeof languages[number]['code']
export const defaultLanguage: Language = 'zh-CN'
export const translatedLanguages = languages.slice(1).map(({ code }) => code)

export function normalizeLanguage(value: unknown): Language {
  return languages.find(({ code }) => code === value)?.code ?? defaultLanguage
}

export function applyDocumentLanguage(language: Language) {
  if (typeof document === 'undefined') return
  document.documentElement.lang = language
  document.documentElement.dir = 'ltr'
}
