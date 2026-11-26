import type { MutationFilters, QueryClient } from '@tanstack/react-query'
import { patchUserConfig } from './api/client'
import { normalizeKemoReasoningEffort, normalizeReasoningEffort } from './reasoningEffort'
import type { OverviewResponse, ProviderSummary } from './types/api'

export function providerSaveFilter(user: string): MutationFilters {
  return {
    mutationKey: ['provider-config', user],
    predicate: mutation => mutation.options.mutationKey?.[2] !== 'settings' || mutation.state.variables === true,
  }
}

export async function saveUserConfig(client: QueryClient, user: string, changes: Record<string, unknown>) {
  const overviewKey = ['overview', user]
  const configKey = ['user-config', user]
  const updatingProvider = Boolean(changes.provider)
  if (updatingProvider) {
    void client.cancelQueries({ queryKey: overviewKey })
    void client.cancelQueries({ queryKey: configKey })
  }

  const receipt = await patchUserConfig(user, changes)
  // Store the server's redacted document, never the submitted changes.
  void client.cancelQueries({ queryKey: configKey })
  client.setQueryData(configKey, receipt)
  const saved = receipt.config?.provider as Partial<ProviderSummary> | undefined
  if (updatingProvider && saved) {
    void client.cancelQueries({ queryKey: overviewKey })
    client.setQueriesData<OverviewResponse>({ queryKey: overviewKey }, current => {
      if (!current) return current
      const { model, type, base_url, stream, reasoning_effort } = saved
      const provider = { ...current.provider, model: model ?? current.provider.model,
        type: type ?? current.provider.type, base_url: base_url ?? current.provider.base_url,
        stream: stream ?? current.provider.stream }
      if (reasoning_effort !== undefined) provider.reasoning_effort = provider.type === 'kemo'
        ? normalizeKemoReasoningEffort(reasoning_effort) : normalizeReasoningEffort(reasoning_effort)
      provider.configured = Boolean(provider.model && provider.type && provider.base_url)
      return { ...current, provider }
    })
    // The overview endpoint can still serve its pre-write snapshot briefly.
    // Normal polling (or the next capability response) refreshes the other fields.
  } else {
    void client.invalidateQueries({ queryKey: overviewKey })
  }
  void client.invalidateQueries({ queryKey: ['settings', user] })
  void client.invalidateQueries({ queryKey: ['runtime-status', user] })
  return receipt
}
