// 优先使用后端记录的真实数据片段。旧服务没有分类字段时，只识别框架的固定前缀，
// 并与采集正文逐字核对；不能按用户数据里的 Markdown 标题猜测操作文档边界。
export function expandDataFragment(module) {
  if (typeof module.data_injected_markdown === 'string') return module.data_injected_markdown;
  if (!module.open_input || module.input_health !== '正常') return '';
  const injected = module.injected_markdown || '';
  const collected = module.collected_markdown || '';
  if (!injected || !collected) return '';
  const prefix = `[${module.scope}:${module.name}]\n## 数据采集\n`;
  if (injected.length < prefix.length) return prefix.startsWith(injected) ? injected : '';
  if (!injected.startsWith(prefix)) return '';
  const sourceStart = prefix.length;
  const sourceEnd = injected.indexOf('\n', sourceStart);
  if (sourceEnd < 0) return '数据来源：`'.startsWith(injected.slice(sourceStart))
    || injected.slice(sourceStart).startsWith('数据来源：`') ? injected : '';
  if (!injected.slice(sourceStart, sourceEnd).startsWith('数据来源：`')) return '';
  const contentStart = sourceEnd + 1;
  const actualData = injected.slice(contentStart, contentStart + collected.length);
  if (!collected.startsWith(actualData)) return '';
  return injected.slice(0, contentStart + actualData.length);
}

export function expandDataContent(data) {
  if (typeof data.injection.data_content === 'string') return data.injection.data_content;
  return data.expands.flatMap(group => group.items).map(expandDataFragment).filter(Boolean).join('\n\n');
}
