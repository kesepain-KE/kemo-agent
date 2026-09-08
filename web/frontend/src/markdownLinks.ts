import remarkGfmBase, { type Options as RemarkGfmOptions } from 'remark-gfm'
import type { Plugin } from 'unified'

interface MarkdownPosition {
  start?: { offset?: number }
  end?: { offset?: number }
}

interface MarkdownNode {
  type?: string
  value?: string
  url?: string
  children?: MarkdownNode[]
  position?: MarkdownPosition
}

const HTTP_URL_RE = /^https?:\/\//i
const BARE_WEB_LINK_RE = /^(?:https?:\/\/|www\.)/i
const NEXT_BARE_WEB_LINK_RE = /(?:https?:\/\/|www\.)/i
const INCOMPLETE_WWW_RE = /^(?:https?:\/\/)?www\.(?:[/?#]|$)/i
const CJK_LINK_BOUNDARY_RE = /[，。；：！？、（）［］｛｝《》〈〉【】「」『』〔〕〖〗〘〙〚〛“”‘’]/u

function bareWebLinkUrl(label: string): string | null {
  if (INCOMPLETE_WWW_RE.test(label)) return null
  const url = HTTP_URL_RE.test(label) ? label : `http://${label}`
  try {
    const parsed = new URL(url)
    return HTTP_URL_RE.test(url) && Boolean(parsed.hostname) ? url : null
  } catch {
    return null
  }
}

function findNextBareWebLink(value: string): number {
  let offset = 0
  while (offset < value.length) {
    const match = value.slice(offset).match(NEXT_BARE_WEB_LINK_RE)
    if (!match || typeof match.index !== 'number') return -1
    const index = offset + match.index
    if (index > 0 && CJK_LINK_BOUNDARY_RE.test(value[index - 1])) return index
    offset = index + match[0].length
  }
  return -1
}

function splitBareAutolink(node: MarkdownNode, source: string): MarkdownNode[] | null {
  if (node.type !== 'link' || !node.url || !HTTP_URL_RE.test(node.url) || node.children?.length !== 1) return null

  const text = node.children[0]
  const start = node.position?.start?.offset
  const end = node.position?.end?.offset
  if (text.type !== 'text' || typeof text.value !== 'string' || typeof start !== 'number' || typeof end !== 'number') {
    return null
  }

  const raw = source.slice(start, end)
  // A GFM bare URL occupies exactly its source range. Explicit [label](url)
  // and <url> forms do not, so their author-provided labels/URLs stay intact.
  if (raw !== text.value || !BARE_WEB_LINK_RE.test(raw)) return null

  const boundary = raw.search(CJK_LINK_BOUNDARY_RE)
  if (boundary < 0) return null

  const replacement: MarkdownNode[] = []
  let cursor = 0
  while (cursor < raw.length) {
    const remaining = raw.slice(cursor)
    if (!BARE_WEB_LINK_RE.test(remaining)) {
      replacement.push({ type: 'text', value: remaining })
      break
    }

    const nextBoundary = remaining.search(CJK_LINK_BOUNDARY_RE)
    const label = nextBoundary < 0 ? remaining : remaining.slice(0, nextBoundary)
    if (!label) {
      replacement.push({ type: 'text', value: remaining })
      break
    }

    const url = bareWebLinkUrl(label)
    if (!url) {
      replacement.push({ type: 'text', value: remaining })
      break
    }
    replacement.push({
      ...node,
      url,
      children: [{ ...text, value: label, position: undefined }],
      position: undefined,
    })
    cursor += label.length
    if (nextBoundary < 0 || cursor >= raw.length) break

    const suffix = raw.slice(cursor)
    const nextLink = findNextBareWebLink(suffix)
    if (nextLink < 0) {
      replacement.push({ type: 'text', value: suffix })
      break
    }
    if (nextLink > 0) replacement.push({ type: 'text', value: suffix.slice(0, nextLink) })
    cursor += nextLink
  }

  return replacement
}

function repairBareAutolinks(node: MarkdownNode, source: string): void {
  if (!node.children) return

  for (let index = 0; index < node.children.length; index += 1) {
    const child = node.children[index]
    const replacement = splitBareAutolink(child, source)
    if (replacement) {
      node.children.splice(index, 1, ...replacement)
      index += replacement.length - 1
      continue
    }
    repairBareAutolinks(child, source)
  }
}

/**
 * GFM with one additional boundary rule for bare HTTP(S) links next to
 * Chinese prose. Source ranges keep explicit [labels](urls) and <urls>
 * untouched while the generated autolink is repaired after parsing.
 */
const remarkGfm: Plugin<[RemarkGfmOptions?]> = function (options) {
  remarkGfmBase.call(this, options)
  return (tree, file) => repairBareAutolinks(tree as MarkdownNode, String(file.value))
}

export default remarkGfm
