const $ = selector => document.querySelector(selector)
const text = (selector, value) => { $(selector).textContent = value ?? '—' }
const field = (form, name) => form.elements.namedItem(name).value.trim()
const views = { overview: '总览', bindings: '本机绑定', outbound: '对外凭据', logs: '日志' }
const errors = {
  artifact_path_forbidden: '只能授权用户上传或下载目录里的文件。',
  artifact_file_required: '请选择一个存在的文件。',
  invalid_public_url: '请输入有效的 HTTP(S) 地址。',
  bridge_not_initialized: '请先在通用配置初始化。',
  peer_identity_immutable: '通道和绑定用户不能更改。',
  peer_revoked: '此绑定已吊销。',
  peer_id_reserved: '此 UUID 已绑定或曾被吊销。',
  self_binding: '不能绑定本机 UUID。',
  unknown_bound_user: '找不到绑定用户。',
  invalid_bridge_id: '请输入有效的 UUID。',
  exposed_user_required: '请先绑定对外用户。',
  external_user_in_use: '请先在对外凭据中删除仍绑定到其他用户的实例。',
  external_key_not_copyable: '旧密钥无法复制，请先刷新。',
  workspace_credentials_unavailable: '出站凭据存储不可用，请核对本地文件。',
  invalid_outbound_credential: '请检查对端地址、登记 UUID、密钥和本机用户名；用户名不能重复。',
}
const events = {
  message_received: '信息回传', device_action_ack: '信息回传',
  message_sent: '信息输出', artifact_upload: '文件上传', artifact_download: '文件下载',
}
const dateFormat = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })

function cell(row, value) {
  const td = row.insertCell()
  td.textContent = value ?? '—'
  return td
}
function rows(selector, items, render, colspan) {
  const table = $(selector)
  table.replaceChildren()
  for (const item of items) render(table.insertRow(), item)
  if (!items.length) cell(table.insertRow(), '暂无记录').colSpan = colspan
}
function options(select, values, selected = select.value) {
  select.replaceChildren(...values.map(([id, name]) => new Option(name, id)))
  if (values.some(([id]) => id === selected)) select.value = selected
}
function peerName(peer) { return peer ? (peer.label || peer.bound_user) : '—' }
function bytes(value) {
  if (value < 1024) return value + ' B'
  if (value < 1048576) return (value / 1024).toFixed(1) + ' KiB'
  return (value / 1048576).toFixed(1) + ' MiB'
}
function badge(label) {
  const node = document.createElement('span')
  node.className = 'badge'
  node.textContent = label
  return node
}

const question = $('#question')
const credential = $('#credential')
function ask(title, previous) {
  text('#question-title', title)
  $('#question-label').hidden = previous === undefined
  $('#question-input').value = previous ?? ''
  question.returnValue = ''
  question.showModal()
  return new Promise(resolve => question.addEventListener('close', () => {
    const value = previous === undefined ? true : $('#question-input').value.trim()
    $('#question-input').value = ''
    resolve(question.returnValue === 'accept' ? value : null)
  }, { once: true }))
}
$('#accept').addEventListener('click', () => question.close('accept'))
$('#decline').addEventListener('click', () => question.close())
function showCredential(title, value) {
  text('#credential-title', title)
  text('#credential-id', value.peer_id)
  $('#credential-secret').value = value.secret
  text('#copy-feedback', '')
  credential.showModal()
}
credential.addEventListener('close', () => {
  $('#credential-secret').value = ''
  text('#credential-id', '')
})
$('#dismiss').addEventListener('click', () => credential.close())

async function copy(value, input) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value)
      return true
    }
  } catch {}
  const temporary = input ? null : document.createElement('textarea')
  if (temporary) {
    temporary.value = value
    temporary.className = 'clipboard-buffer'
    document.body.append(temporary)
  }
  const target = input || temporary
  target.focus()
  target.select()
  try {
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    temporary?.remove()
  }
}
$('#copy-credential').addEventListener('click', async () => {
  const input = $('#credential-secret')
  text('#copy-feedback', await copy(input.value, input) ? '已复制' : '已选中，请按 Ctrl+C 或 ⌘C')
})

export function mountAdmin(kemo) {
  let view = 'overview'
  let busy = false
  let instanceUuid = ''
  const navigation = [...document.querySelectorAll('[data-view]')]

  function api(operation, data = {}) {
    return kemo.request('module.action.invoke', { action: 'bridge_admin', params: { operation, data } })
  }
  async function run(callback) {
    if (busy) return
    busy = true
    text('#message', '')
    $('.detail').setAttribute('aria-busy', 'true')
    try {
      await callback()
    } catch (error) {
      const message = error.message || '桥接操作失败'
      const code = Object.keys(errors).find(key => message.includes(key))
      text('#message', code ? errors[code] : message)
    } finally {
      busy = false
      $('.detail').removeAttribute('aria-busy')
    }
  }
  function listen(form, callback) {
    const submit = () => { if (form.reportValidity()) void run(() => callback(form)) }
    form.querySelector('[data-submit]').addEventListener('click', submit)
    form.addEventListener('submit', event => { event.preventDefault(); submit() })
  }
  function button(parent, label, callback) {
    const node = document.createElement('button')
    node.textContent = label
    node.type = 'button'
    node.addEventListener('click', () => void run(callback))
    parent.append(node)
  }
  async function copyKey(operation, peer_id, params = {}) {
    const { clipboard_text: secret } = await api(operation, { peer_id, ...params })
    if (await copy(secret)) {
      text('#message', '已复制密钥')
    } else {
      showCredential('复制密钥', { peer_id, secret })
      $('#credential-secret').select()
      text('#copy-feedback', '已选中，请按 Ctrl+C 或 ⌘C')
    }
  }

  async function loadOverview() {
    const data = await api('snapshot')
    text('#bridge-state', data.state.bridge_state)
    $('#bridge-state').dataset.online = data.state.bridge_state === '在线'
    text('#mobile-users', data.mobile_users.join('、') || '未绑定')
    text('#external-count', data.external_count)
    text('#traffic-count', data.traffic_count)
    instanceUuid = data.instance_uuid
    text('#instance-uuid', instanceUuid)
    $('#copy-uuid').disabled = $('#replace-uuid').disabled = false
  }

  function phoneCard(peer) {
    const card = document.createElement('article')
    card.className = 'phone'
    const header = document.createElement('div')
    header.className = 'phone-header'
    const title = document.createElement('strong')
    title.textContent = peer.label ? peer.label + ' · ' + peer.bound_user : peer.bound_user
    header.append(title, badge(peer.enabled ? '已绑定' : '已停用'))
    const id = document.createElement('code')
    id.textContent = peer.peer_id + ' · ' + peer.secret_hint
    const controls = document.createElement('div')
    controls.className = 'controls'
    const peer_id = peer.peer_id
    button(controls, '备注', async () => {
      const label = await ask('修改手机备注', peer.label ?? '')
      if (label === null) return
      await api('edit_peer', { peer_id, label })
      await loadBindings()
    })
    button(controls, peer.enabled ? '停用' : '启用', async () => {
      await api('edit_peer', { peer_id, enabled: !peer.enabled })
      await loadBindings()
    })
    button(controls, '刷新密钥', async () => {
      if (!await ask('刷新后，手机的旧密钥会失效。')) return
      showCredential('新手机密钥 · 仅显示一次', await api('rotate_peer', { peer_id }))
      await loadBindings()
    })
    button(controls, '解除绑定', async () => {
      if (!await ask('解除绑定并吊销这台手机的密钥？')) return
      await api('revoke_peer', { peer_id })
      await loadBindings()
    })
    card.append(header, id, controls)
    return card
  }

  async function loadBindings() {
    const data = await api('snapshot')
    const users = [['', '选择用户'], ...data.users.map(user => [user, user])]
    options($('#create-phone [name=bound_user]'), users)
    options($('#exposed-form [name=bound_user]'), users, data.exposed_user || '')
    const form = $('#public-url-form')
    form.elements.public_url.value = data.state.public_url === '未设置' ? '' : data.state.public_url
    for (const input of form.elements) input.disabled = data.state.bridge_state === '未初始化'

    const phones = data.peers.filter(peer => peer.channel === 'device' && !peer.revoked_at)
    $('#phones').replaceChildren(...phones.map(phoneCard))
    if (!phones.length) {
      const empty = document.createElement('p')
      empty.className = 'empty'
      empty.textContent = '暂无手机绑定'
      $('#phones').append(empty)
    }
    options($('#create-grant [name=peer_id]'), data.peers.filter(peer => peer.enabled).map(peer => [peer.peer_id, peerName(peer)]))
    rows('#grants', data.grants, (row, grant) => {
      cell(row, grant.display_name || grant.file_name)
      cell(row, peerName(data.peers.find(peer => peer.peer_id === grant.peer_id)))
      cell(row, bytes(grant.size_bytes))
      cell(row, grant.download_count)
      cell(row, grant.revoked_at ? '已撤销' : '已授权')
      const controls = cell(row, '')
      if (!grant.revoked_at) button(controls, '撤销', async () => {
        if (!await ask('撤销下载授权？原文件会保留。')) return
        await api('revoke_grant', { grant_id: grant.grant_id })
        await loadBindings()
      })
    }, 6)
  }

  function updateEmpty() { $('#no-externals').hidden = Boolean($('#external-cards').children.length) }
  function externalCard(peer = {}) {
    const form = $('#external-template').content.firstElementChild.cloneNode(true)
    const id = form.elements.peer_id
    const key = form.elements.secret
    form.elements.label.value = peer.label || ''
    id.value = peer.peer_id || ''
    id.readOnly = Boolean(peer.peer_id)
    key.placeholder = peer.peer_id ? '•••• ' + peer.secret_hint : '保存时分配'
    const rotate = form.querySelector('[data-rotate]')
    const copy = form.querySelector('[data-copy]')
    rotate.disabled = !peer.peer_id
    copy.disabled = !peer.can_copy
    if (peer.peer_id && !peer.can_copy) copy.title = '旧密钥需先刷新'
    rotate.addEventListener('click', () => void run(async () => {
      if (!await ask('刷新后，此实例的旧连接密钥会立即失效。')) return
      const updated = await api('rotate_peer', { peer_id: peer.peer_id })
      key.placeholder = '•••• ' + updated.secret_hint
      copy.disabled = false
      copy.title = '复制密钥'
      text('#message', '新密钥已生效')
    }))
    copy.addEventListener('click', () => void run(() => copyKey('copy_external_key', peer.peer_id)))
    listen(form, async () => {
      const payload = { peer_id: field(form, 'peer_id'), label: field(form, 'label') || null }
      const saved = await api(peer.peer_id ? 'edit_peer' : 'create_external', payload)
      form.replaceWith(externalCard({ ...saved, can_copy: Boolean(saved.secret || peer.can_copy || !copy.disabled) }))
      text('#message', '已保存')
    })
    form.querySelector('[data-delete]').addEventListener('click', () => void run(async () => {
      if (peer.peer_id) {
        if (!await ask('删除并吊销连接密钥？此 UUID 不能重新绑定。')) return
        await api('revoke_peer', { peer_id: peer.peer_id })
      }
      form.remove()
      updateEmpty()
    }))
    return form
  }

  async function loadOutbound() {
    const data = await api('snapshot', { view: 'outbound' })
    $('#add-instance').disabled = !data.exposed_user
    $('#external-user-needed').hidden = Boolean(data.exposed_user)
    $('#external-cards').replaceChildren(...data.peers.map(externalCard))
    updateEmpty()
    if (data.legacy_error) text('#message', errors[data.legacy_error] ?? data.legacy_error)
    $('#legacy-credentials').hidden = !data.legacy_items.length
    rows('#legacy-rows', data.legacy_items, (row, item) => {
      cell(row, item.label)
      cell(row, item.url)
      cell(row, item.peer_id)
      button(cell(row, ''), '复制密钥', () => copyKey('copy_legacy_key', item.peer_id, { url: item.url }))
    }, 4)
  }

  async function loadLogs() {
    const select = $('#logs-form [name=peer_id]')
    const data = await api('logs', { peer_id: select.value })
    options(select, [['', '全部对端'], ...data.peers.map(peer => [peer.peer_id, peerName(peer) + ' · ' + peer.peer_id.slice(0, 8)])])
    rows('#logs', data.items, (row, item) => {
      const peer = data.peers.find(p => p.peer_id === item.peer_id)
      cell(row, dateFormat.format(new Date(item.created_at)))
      cell(row, events[item.action])
      cell(row, peerName(peer) + ' · ' + item.peer_id.slice(0, 8))
      cell(row, item.channel === 'device' ? '手机' : '外部实例')
      cell(row, item.endpoint)
    }, 5)
  }

  const load = () => ({ overview: loadOverview, bindings: loadBindings, outbound: loadOutbound, logs: loadLogs })[view]()
  function selectView(next) {
    if (busy || next === view) return
    view = next
    for (const tab of navigation) {
      if (tab.dataset.view === view) tab.setAttribute('aria-current', 'page')
      else tab.removeAttribute('aria-current')
    }
    for (const panel of document.querySelectorAll('[data-panel]')) panel.hidden = panel.dataset.panel !== view
    text('#view-title', views[view])
    void run(load)
  }
  for (const tab of navigation) {
    tab.disabled = false
    tab.addEventListener('click', () => selectView(tab.dataset.view))
  }

  $('#refresh').disabled = false
  $('#refresh').addEventListener('click', () => void run(load))
  $('#copy-uuid').addEventListener('click', () => void run(async () => {
    text('#message', await copy(instanceUuid) ? '已复制 UUID' : '请选中 UUID 手动复制')
  }))
  $('#replace-uuid').addEventListener('click', () => void run(async () => {
    if (!await ask('更换 UUID？引用本机旧 UUID 的对端需要更新绑定。')) return
    await api('replace_instance_uuid')
    await loadOverview()
    text('#message', 'UUID 已更换')
  }))
  $('#add-instance').addEventListener('click', () => {
    if (busy) return
    const card = externalCard()
    $('#external-cards').append(card)
    updateEmpty()
    card.elements.label.focus()
  })
  $('#external-user-needed').addEventListener('click', () => selectView('bindings'))

  listen($('#outbound-credential-form'), async form => {
    const data = Object.fromEntries(['url', 'peer_id', 'label', 'secret'].map(name => [name, field(form, name)]))
    const users = field(form, 'allowed_users')
    if (users) data.allowed_users = users.split(',').map(user => user.trim())
    form.elements.secret.value = ''
    await api('import_outbound_credential', data)
    form.reset()
    await loadOutbound()
    text('#message', '出站凭据已保存')
  })
  listen($('#public-url-form'), async form => {
    await api('save_public_url', { public_url: field(form, 'public_url') || null })
    await loadBindings()
    text('#message', '地址已保存')
  })
  listen($('#exposed-form'), async form => {
    await api('set_exposed_user', { bound_user: field(form, 'bound_user') })
    await loadBindings()
    text('#message', '绑定已保存')
  })
  listen($('#create-phone'), async form => {
    showCredential('手机连接密钥 · 仅显示一次', await api('create_device', {
      bound_user: field(form, 'bound_user'), label: field(form, 'label') || null,
    }))
    form.elements.label.value = ''
    await loadBindings()
  })
  listen($('#create-grant'), async form => {
    await api('create_grant', {
      peer_id: field(form, 'peer_id'), file_path: field(form, 'file_path'),
      media_kind: field(form, 'media_kind'), display_name: field(form, 'display_name') || null,
    })
    form.reset()
    await loadBindings()
  })
  listen($('#logs-form'), loadLogs)
  void run(load)
}
