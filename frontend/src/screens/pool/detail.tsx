/** One stored alpha: PnL, settings, submission checks, and correlations on request. */

import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, errorMessage } from '@/api/http'
import { pool } from '@/api/pool'
import { fmt, isNum } from '@/lib/format'
import { Badge, Button, Empty, ErrorNotice, KV, Metric, Notice, Skeleton, checkTone } from '@/ui/kit'
import { PnlChart } from '@/ui/charts'
import { Sheet } from '@/ui/overlay'
import { checkFigure, settingsItems } from './helpers'
import { OpenInBrain, RecheckButton } from './shared'

type Kind = 'self' | 'prod'
const KIND_LABEL: Record<Kind, string> = { self: 'Self-Correlation', prod: 'Production Correlation' }

export function DetailSheet({ alphaId, onClose }: { alphaId: string | null; onClose: () => void }) {
  return (
    <Sheet open={alphaId !== null} onOpenChange={(o) => !o && onClose()} title={<span className="num">{alphaId ?? 'Alpha'}</span>}>
      {alphaId && <Body key={alphaId} alphaId={alphaId} />}
    </Sheet>
  )
}

function Section({ title, description, actions, children }: { title: string; description?: string; actions?: React.ReactNode; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2.5 border-t border-hairline pt-4 first:border-t-0 first:pt-0">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex flex-col gap-0.5">
          <h3 className="title">{title}</h3>
          {description && <p className="text-xs text-ink-subtle">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
      </div>
      {children}
    </section>
  )
}

function Body({ alphaId }: { alphaId: string }) {
  const detail = useQuery({ queryKey: ['pool', 'detail', alphaId], queryFn: () => pool.detail(alphaId), retry: false })
  const [kinds, setKinds] = useState<Kind[]>([])

  if (detail.isPending) return <Skeleton className="h-60" />
  if (detail.isError) return <ErrorNotice error={detail.error} title="Could not load this Alpha" />
  const d = detail.data

  return (
    <div className="flex flex-col gap-4">
      <p className="num text-xs break-all text-ink-muted">{d.expression ?? '—'}</p>
      <div className="flex flex-wrap gap-2">
        <OpenInBrain url={d.brainUrl} />
        <RecheckButton alphaId={d.alphaId} />
      </div>

      <Section title="Cumulative PnL" description={`${fmt.int(d.days)} trading days stored`}>
        <div className="flex gap-6">
          <Metric size="sm" label="K-Ratio" value={fmt.ratio(d.kRatio)} />
        </div>
        {d.problem && <Notice tone="warn">{d.problem}</Notice>}
        {d.pnl.length > 1 ? (
          <PnlChart values={d.pnl} dates={d.dates} label={`Cumulative PnL of ${d.alphaId}`} />
        ) : (
          !d.problem && <Empty title="No daily PnL stored">BRAIN returned no daily PnL for this Alpha.</Empty>
        )}
      </Section>

      <Section title="Settings">
        <KV items={settingsItems(d.settings)} />
      </Section>

      <Section title="Submission Checks" description="As BRAIN last reported them. Pending checks resolve with Re-check on BRAIN.">
        {d.checks.length === 0 ? (
          <p className="text-xs text-ink-subtle">No checks stored.</p>
        ) : (
          <ul className="flex flex-col divide-y divide-hairline/60">
            {d.checks.map((c) => (
              <li key={c.name} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-1.5 text-[13px]">
                <Badge tone={checkTone(c.result ?? 'PENDING')}>{c.result ?? 'PENDING'}</Badge>
                <span className="num text-ink">{c.name}</span>
                {isNum(c.value) && (
                  <span className="num text-xs text-ink-subtle">
                    {checkFigure(c.name, c.value)}
                    {isNum(c.limit) && ` / ${checkFigure(c.name, c.limit)}`}
                  </span>
                )}
                {c.message && <span className="basis-full text-xs text-ink-subtle">{c.message}</span>}
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section
        title="Correlations"
        description="BRAIN rate-limits these checks hourly, so each one loads only when you ask."
        actions={(['self', 'prod'] as Kind[]).map((k) => (
          <Button key={k} size="sm" disabled={kinds.includes(k)} onClick={() => setKinds([...kinds, k])}>
            {KIND_LABEL[k]}
          </Button>
        ))}
      >
        {kinds.map((k) => (
          <CorrelationResult key={k} alphaId={alphaId} kind={k} />
        ))}
      </Section>
    </div>
  )
}

const cellText = (v: unknown) => (isNum(v) ? (Number.isInteger(v) ? fmt.int(v) : fmt.ratio(v, 4)) : v == null ? '—' : String(v))

function CorrelationResult({ alphaId, kind }: { alphaId: string; kind: Kind }) {
  const q = useQuery({ queryKey: ['pool', 'correlations', alphaId, kind], queryFn: () => pool.correlations(alphaId, kind), retry: false, staleTime: Infinity })
  const label = KIND_LABEL[kind]

  if (q.isPending) return <Skeleton className="h-24" />
  if (q.isError) {
    const e = q.error
    if (e instanceof ApiError && e.code === 'platform_error' && [410, 412].includes(Number(e.body.platformStatus))) {
      return <p className="text-xs text-ink-subtle">{label}: not applicable to this Alpha.</p>
    }
    const limited = e instanceof ApiError && e.code === 'rate_limited'
    const retry = (
      <Button size="sm" variant="ghost" onClick={() => q.refetch()}>
        Try again
      </Button>
    )
    return limited ? (
      <Notice tone="warn" title={`${label}: BRAIN's hourly limit`} action={retry}>
        {errorMessage(e)}
        {isNum(e.body.retryAfter) && (
          <>
            {' '}
            Try again in <span className="num">{fmt.duration(e.body.retryAfter)}</span>.
          </>
        )}
      </Notice>
    ) : (
      <div className="flex flex-col gap-2">
        <ErrorNotice error={e} title={label} />
        <div>{retry}</div>
      </div>
    )
  }

  const props = q.data.schema?.properties ?? []
  const rows = q.data.records ?? []

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-end gap-6">
        <span className="text-[13px] font-medium text-ink">{label}</span>
        {isNum(q.data.min) && <Metric size="sm" label="Min" value={fmt.ratio(q.data.min, 4)} />}
        {isNum(q.data.max) && <Metric size="sm" label="Max" value={fmt.ratio(q.data.max, 4)} />}
      </div>
      {rows.length === 0 ? (
        <p className="text-xs text-ink-subtle">BRAIN returned no rows.</p>
      ) : (
        <div className="max-h-64 overflow-auto rounded-md border border-hairline">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-surface-1">
              <tr>
                {props.map((p) => (
                  <th key={p.name} className="border-b border-hairline px-3 py-1.5 text-left text-xs font-medium text-ink-subtle">
                    {p.title ?? p.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((record, i) => (
                <tr key={i} className="border-b border-hairline/60 last:border-b-0">
                  {props.map((p, j) => (
                    <td key={p.name} className={`num px-3 py-1 ${isNum(record[j]) ? 'text-right' : ''}`}>
                      {cellText(record[j])}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
