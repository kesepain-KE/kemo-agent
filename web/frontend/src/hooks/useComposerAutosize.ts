import { useLayoutEffect, type RefObject } from 'react'

/** 生产输入框由 JS 独占高度计算，不能再与 CSS field-sizing: content 混用。 */
function resizeComposer(textarea: HTMLTextAreaElement) {
  const rootSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
  const minimum = rootSize * 3.2
  const maximum = Math.max(minimum, Math.min(rootSize * 12, window.innerHeight * 0.4))

  // 空草稿不读取可能包含旧高度的 scrollHeight，发送清空后确定性回到单行高度。
  if (!textarea.value) {
    textarea.style.height = `${minimum}px`
    textarea.scrollTop = 0
    return
  }

  // 先撤掉旧高度再测内容，确保删除、变宽时可以向下回缩。
  textarea.style.height = '0px'
  const style = getComputedStyle(textarea)
  const border = (Number.parseFloat(style.borderTopWidth) || 0)
    + (Number.parseFloat(style.borderBottomWidth) || 0)
  textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight + border, minimum), maximum)}px`
}

export function useComposerAutosize(ref: RefObject<HTMLTextAreaElement | null>, value: string) {
  // 在绘制前回缩，避免发送后空输入框仍闪现上一份长草稿的高度。
  useLayoutEffect(() => {
    if (ref.current) resizeComposer(ref.current)
  }, [ref, value])

  useLayoutEffect(() => {
    const textarea = ref.current
    if (!textarea) return
    const update = () => resizeComposer(textarea)
    let width = textarea.getBoundingClientRect().width
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(() => {
      const nextWidth = textarea.getBoundingClientRect().width
      // 自己写入高度会触发 observer；只响应宽度变化，防止反馈循环。
      if (Math.abs(nextWidth - width) < 0.5) return
      width = nextWidth
      update()
    })
    observer?.observe(textarea)

    const fontObserver = new MutationObserver(update)
    fontObserver.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['data-fontsize', 'style', 'class'],
    })
    window.addEventListener('resize', update)
    document.fonts?.addEventListener('loadingdone', update)
    return () => {
      observer?.disconnect()
      fontObserver.disconnect()
      window.removeEventListener('resize', update)
      document.fonts?.removeEventListener('loadingdone', update)
    }
  }, [ref])
}
