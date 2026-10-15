import { icon } from './icons.js'

/** SVG 仅来自样例中固定的本地图标表，不接收服务端 HTML。 */
export function ReferenceIcon({ name, small = false }: { name: string; small?: boolean }) {
  return <span aria-hidden="true" dangerouslySetInnerHTML={{ __html: icon(name, small ? 'icon-sm' : undefined) }} />
}
