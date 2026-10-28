import { beforeEach, describe, expect, it } from 'vitest'
import { openWorkspaceBlank, openWorkspaceJob, openWorkspaceJobs, openWorkspacePath, useWorkspaceStore } from './workspace'

beforeEach(() => useWorkspaceStore.setState({ users: {} }))

describe('workspace tabs', () => {
  it('deduplicates resources, preserves hidden tabs, and isolates users', () => {
    openWorkspacePath('alice', 'download', 'reports/a.md', 'a.md')
    openWorkspaceJobs('alice')
    useWorkspaceStore.getState().patch('alice', { open: false })
    openWorkspacePath('alice', 'download', 'reports/a.md', 'a.md')
    openWorkspaceBlank('bob')
    const alice = useWorkspaceStore.getState().users.alice
    expect(alice.tabs).toHaveLength(2)
    expect(alice.activeId).toBe('file:download:reports/a.md')
    expect(alice.open).toBe(true)
    expect(useWorkspaceStore.getState().users.bob.tabs.map(tab => tab.type)).toEqual(['blank'])
  })

  it('closing an active tab selects its neighbor and closing the last hides the panel', () => {
    openWorkspaceJobs('alice')
    openWorkspaceBlank('alice')
    openWorkspaceJob('alice', { job_id: `job_${'a'.repeat(32)}`, session_id: 's1', source: 'web' })
    const store = useWorkspaceStore.getState()
    store.close('alice', 'blank')
    expect(useWorkspaceStore.getState().users.alice.activeId).toContain('job:')
    store.close('alice', `job:job_${'a'.repeat(32)}`)
    expect(useWorkspaceStore.getState().users.alice.activeId).toBe('jobs')
    store.patch('alice', { maximized: true })
    store.close('alice', 'jobs')
    expect(useWorkspaceStore.getState().users.alice).toMatchObject({ tabs: [], activeId: '', open: false, maximized: false })
  })

  it('never silently evicts existing tabs when the bounded tab limit is reached', () => {
    for (let i = 0; i < 17; i++) openWorkspacePath('alice', 'download', `${i}.txt`, `${i}.txt`)
    const state = useWorkspaceStore.getState().users.alice
    expect(state.tabs).toHaveLength(16)
    expect(state.tabs[0].title).toBe('0.txt')
    expect(state.activeId).toBe('file:download:15.txt')
    expect(state.notice).toContain('16')
    useWorkspaceStore.getState().pin('alice', state.tabs[0].id)
    expect(useWorkspaceStore.getState().users.alice.tabs[0].pinned).toBe(true)
  })
})
