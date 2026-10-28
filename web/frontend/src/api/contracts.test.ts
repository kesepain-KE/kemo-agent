import { describe, expect, it } from 'vitest'
import { validateApiResponse } from './contracts'

describe('runtime API response contracts', () => {
  it('rejects missing nested fields before rendering', () => {
    expect(() => validateApiResponse('/api/users/u/tasks',
      { user: 'u', summary: {}, plans: [], cron_tasks: [], executions: [] }))
      .toThrow('summary.active_plans')
    expect(() => validateApiResponse('/api/users/u/runtime/status',
      { user: 'u', api: null })).toThrow('api')
  })
  it('does not turn null collections into silently empty data', () => {
    expect(() => validateApiResponse('/api/users/u/memory/summary',
      { user: 'u', summary: {}, items: null })).toThrow('items')
  })
  it('preserves declared extension fields', () => {
    const payload = { user: 'u', summary: {}, items: [], extension: 'kept' }
    expect(validateApiResponse('/api/users/u/memory/summary', payload)).toEqual(payload)
  })
})
