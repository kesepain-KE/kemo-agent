import type { ProviderReasoningControl } from './appShellTypes'
import { ReasoningEffortSelect } from './ReasoningEffortSelect'

export function ComposerReasoningSelect({ control }: { control: ProviderReasoningControl }) {
  return <>
    <div className="composer-reasoning-control" title={control.notice}>
      <ReasoningEffortSelect ariaLabel="输入框思考档位" variant="composer"
        value={control.value} options={control.options} disabled={control.disabled}
        emptyLabel={control.emptyLabel} onChange={control.onChange} />
    </div>
    {control.error ? <span className="composer-reasoning-error" role="alert">{control.error}</span> : null}
  </>
}
