import { useQuery } from '@tanstack/react-query'
import { getBackground } from '../api/background'
import type { BackgroundSettings } from '../api/background'

/** A user-scoped, unsaved appearance preview. Never written into saved status. */
export function useBackgroundUiPreview(user: string) {
  return useQuery<Pick<BackgroundSettings, 'ui_opacity' | 'ui_blur'> | null>({
    queryKey: ['background-ui-preview', user],
    queryFn: async () => null,
    initialData: null,
    enabled: false,
  })
}

export function useBackground(user: string) {
  return useQuery({
    queryKey: ['background', user],
    queryFn: () => getBackground(user),
    enabled: Boolean(user),
    staleTime: 60_000,
    retry: false,
  })
}

export function useBackgroundPlayback(user: string) {
  return useQuery<{ revision: string; failed: boolean } | null>({
    queryKey: ['background-playback', user],
    queryFn: async () => null,
    initialData: null,
    enabled: false,
  })
}
