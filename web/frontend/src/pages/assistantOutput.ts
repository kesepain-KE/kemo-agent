const PARENTHETICAL = /（[^（）\r\n]*）|\([^()\r\n]*\)/g
const BACKEND_JUDGMENT = /(?:系统|后台).{0,24}(?:判定|拦截|分类|阻止)|(?:重复调用|相同调用).{0,24}(?:判定|拦截|拒绝|阻止|复用)|(?:duplicate_blocked|duplicate_reused|identical_call_blocked|retry_reuse_blocked)|(?:system|backend).{0,32}(?:classified|judged|blocked)|(?:duplicate|identical) (?:tool )?call.{0,24}(?:blocked|reused|rejected)/iu

export function stripBackendJudgmentAsides(text: string) {
  return text
    .replace(PARENTHETICAL, (aside) => BACKEND_JUDGMENT.test(aside) ? '' : aside)
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]*\n(?:[ \t]*\n)+/g, '\n\n')
    .trim()
}
