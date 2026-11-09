/** Loaded when the request-record page is opened, using the real safe snapshot. */
export function renderRequestLogs(target, logs) {
  target.replaceChildren()
  const head = target.createTHead().insertRow()
  for (const value of ['时间', '模型', '状态', '耗时（ms）']) {
    const cell = document.createElement('th')
    cell.textContent = value
    head.append(cell)
  }
  const body = target.createTBody()
  for (const log of logs) {
    const row = body.insertRow()
    for (const value of [log.started_at, log.model, log.status, log.latency_ms]) {
      row.insertCell().textContent = String(value ?? '—')
    }
  }
  if (!logs.length) {
    const cell = body.insertRow().insertCell()
    cell.colSpan = 4
    cell.textContent = '暂无采集记录'
  }
}
