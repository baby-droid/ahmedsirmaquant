/** Alphas that pass every resolved submission check. Submission itself happens on BRAIN. */

import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { CopyIcon } from 'lucide-react'
import { toast } from 'sonner'
import { pool, type SubmittableAlpha } from '@/api/pool'
import { scopeLabel } from '@/api/types'
import { fmt, isNum } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { useRefetchOn } from '@/lib/ws'
import { Button, Empty, ErrorNotice, Metric, Panel, Skeleton, type Tone } from '@/ui/kit'
import { PnlChart } from '@/ui/charts'
import { ScopePicker } from '@/ui/scope-picker'
import { limitOf } from './helpers'
import { CheckBadge, OpenInBrain, RecheckButton, SettingsLine } from './shared'

const pass = (ok: boolean | null): Tone => (ok === null ? 'neutral' : ok ? 'profit' : 'loss')

export function Submittable({ onOpen }: { onOpen: (alphaId: string) => void }) {
  const [scope, setScope] = useScope('pool-submittable')
  const q = useQuery({ queryKey: ['pool', 'submittable', scope], queryFn: () => pool.submittable(scope), placeholderData: keepPreviousData })
  useRefetchOn('simulations', ['pool', 'submittable'], 5000)
  const data = q.data

  return (
    <Panel
      title="Submittable"
      actions={<ScopePicker scope={scope} onChange={setScope} />}
      bodyClassName="flex flex-col gap-4"
    >
      {data && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <Metric boxed label="Submittable" value={fmt.int(data.total)} tone={data.total > 0 ? 'profit' : 'neutral'} />
          <Metric boxed label="Still being checked" value={fmt.int(data.pending)} />
          <Metric boxed label="Near misses" value={fmt.int(data.nearMisses)} tone={data.nearMisses > 0 ? 'warn' : 'neutral'} />
        </div>
      )}
      {q.isError && <ErrorNotice error={q.error} title="Could not read submittable Alphas" />}

      {q.isPending ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-56" />
          ))}
        </div>
      ) : data && data.alphas.length === 0 ? (
        <Empty title={`No submittable Alphas in ${scopeLabel(scope)}`}>
          {data.pending > 0 && (
            <>
              <span className="num">{fmt.int(data.pending)}</span> Alphas are still being checked.
            </>
          )}
        </Empty>
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {data?.alphas.map((a) => <Card key={a.alphaId} alpha={a} onOpen={onOpen} />)}
        </div>
      )}
    </Panel>
  )
}

function Card({ alpha: a, onOpen }: { alpha: SubmittableAlpha; onOpen: (alphaId: string) => void }) {
  const d0 = a.settings.delay === 0
  const sharpeBar = limitOf(a.checks, 'LOW_SHARPE') ?? (d0 ? 2.69 : 1.58)
  const fitnessBar = limitOf(a.checks, 'LOW_FITNESS') ?? (d0 ? 1.5 : 1.0)
  const passed = a.checks.filter((c) => c.result === 'PASS')

  const copy = () =>
    navigator.clipboard.writeText(a.expression ?? '').then(
      () => toast.success(`Copied the expression of ${a.alphaId}`),
      () => toast.error('Could not write to the clipboard.'),
    )

  return (
    <article className="flex min-w-0 flex-col gap-3 rounded-lg border border-hairline bg-surface-2 p-4">
      <div className="flex flex-wrap items-center gap-2 text-xs text-ink-subtle">
        <button type="button" className="num text-[13px] text-ink hover:underline" onClick={() => onOpen(a.alphaId)}>
          {a.alphaId}
        </button>
        <span className="num">{fmt.dateTime(a.dateCreated)}</span>
      </div>
      <p className="num text-xs break-all text-ink-muted">{a.expression ?? '—'}</p>
      <SettingsLine settings={a.settings} />
      <div className="grid grid-cols-3 gap-3 sm:grid-cols-6">
        <Metric size="sm" label="Sharpe" hint={`≥ ${fmt.ratio(sharpeBar)}`} value={fmt.ratio(a.sharpe)} tone={pass(isNum(a.sharpe) ? a.sharpe >= sharpeBar : null)} />
        <Metric size="sm" label="Fitness" hint={`≥ ${fmt.ratio(fitnessBar)}`} value={fmt.ratio(a.fitness)} tone={pass(isNum(a.fitness) ? a.fitness >= fitnessBar : null)} />
        <Metric size="sm" label="Turnover" hint="1%–70%" value={fmt.pct(a.turnover)} tone={pass(isNum(a.turnover) ? a.turnover >= 0.01 && a.turnover <= 0.7 : null)} />
        <Metric size="sm" label="Returns" value={fmt.pct(a.returns)} tone={isNum(a.returns) && a.returns !== 0 ? pass(a.returns > 0) : 'neutral'} />
        <Metric size="sm" label="Drawdown" value={fmt.pct(a.drawdown)} />
        <Metric size="sm" label="Margin" value={fmt.bps(a.margin)} />
      </div>
      {a.pnl.length > 1 && <PnlChart values={a.pnl} compact label={`Cumulative PnL of ${a.alphaId}`} />}
      {passed.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {passed.map((c) => (
            <CheckBadge key={c.name} check={c} />
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2">
        <OpenInBrain url={a.brainUrl} />
        <RecheckButton alphaId={a.alphaId} />
        <Button size="sm" variant="ghost" disabled={!a.expression} onClick={copy}>
          <CopyIcon />
          Copy expression
        </Button>
      </div>
    </article>
  )
}
