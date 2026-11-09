/** Browser SDK served inside each isolated module UI asset namespace. */
const protocol = 'kemo.module-ui.v1'
let nonce = ''
let context = {}
let sequence = 0
const pending = new Map()
const listeners = new Set()
let readyResolve
const ready = new Promise(resolve => { readyResolve = resolve })

window.addEventListener('message', event => {
  if (event.source !== parent || !event.data || event.data.protocol !== protocol) return
  const message = event.data
  if (message.kind === 'init') {
    if (nonce && nonce !== message.nonce) return
    nonce = message.nonce
    context = message.context || {}
    readyResolve(context)
    return
  }
  if (!nonce || message.nonce !== nonce) return
  if (message.kind === 'event') {
    for (const listener of listeners) listener(message.event, message.data)
    return
  }
  const request = pending.get(message.request_id)
  if (message.kind !== 'response' || !request) return
  clearTimeout(request.timer)
  pending.delete(message.request_id)
  if (message.error) {
    const error = new Error(message.error.message)
    error.code = message.error.code
    request.reject(error)
  } else request.resolve(message.result)
})

parent.postMessage({ protocol, kind: 'ready' }, '*')

export const kemo = {
  ready,
  async request(method, params = {}) {
    await ready
    const request_id = String(++sequence)
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        pending.delete(request_id)
        const error = new Error('操作等待超时；后端可能仍在执行，请先核对状态，勿直接重复提交')
        error.code = 'TIMEOUT'
        reject(error)
      }, 150000)
      pending.set(request_id, { resolve, reject, timer })
      parent.postMessage({ protocol, kind: 'request', nonce, request_id, method, params }, '*')
    })
  },
  onEvent(listener) {
    listeners.add(listener)
    return () => listeners.delete(listener)
  },
  setDirty(dirty) { return this.request('host.dirty.set', { dirty: Boolean(dirty) }) },
  get context() { return context },
}
