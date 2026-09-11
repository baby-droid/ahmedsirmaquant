/**
 * The live 8×10 simulation matrix: one row per BRAIN slot ("core"), one cell per Alpha
 * inside its multi-simulation. Each core shows the five-part batch key every Alpha in its
 * multi-simulation must share. QUEUED work is counted, never drawn.
 */

import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ClockIcon, EllipsisIcon } from 'lucide-react'
import { toast } from 'sonner'
import { simulations } from '@/api/core'
import { errorMessage } from '@/api/http'
import type { SimulationRow } from '@/api/types'
import { fmt, secondsSince, DASH } from '@/lib/format'
import { useCores, useLive } from '@/lib/live'
import type { Cell, Core } from '@/lib/matrix'
import { useRefetchOn } from '@/lib/ws'
import { Button, ErrorNotice, Notice, Panel, Progress, Skeleton, cx } from '@/ui/kit'
import { Confirm, Menu, Tooltip } from '@/ui/overlay'

/** CLAUDE.md §4.1: the fields BRAIN requires every child of one multi-simulation to share. */
const BATCH_KEY: { label: string; value: (row: SimulationRow) => string }[] = [
  { label: 'Type', value: (r) => r.simType },
  { label: 'Region', value: (r) => r.region },
  { label: 'Delay', value: (r) => String(r.delay) },
  { label: 'Instrument Type', value: (r) => r.instrumentType },
  { label: 'Language', value: (r) => r.language },
]

/** Same status box as the top bar: DESIGN.md brand-secure tint. */
const STAT = 'flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md border border-brand-secure/35 bg-brand-secure/10 px-2.5 text-brand-secure'

function useNow(): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return now
}

export function SimulationMatrix() {
  const queryClient = useQueryClient()
  const live = useLive((s) => s.simulations)
  // Until the socket's first snapshot lands, read the active set once over REST.
  const fallback = useQuery({ queryKey: ['simulations', 'active'], queryFn: () => simulations.active(), enabled: live === null })
  const engine = useQuery({ queryKey: ['simulations', 'engine'], queryFn: () => simulations.engine() })
  useRefetchOn('simulations', ['simulations', 'engine'], 2000)
  const now = useNow()

  const active = live ?? fallback.data
  const status = engine.data
  const slots = status?.slots ?? 8
  const maxBatch = status?.maxBatch ?? 10
  const { cores, overflow } = useCores(active, slots, maxBatch)

  const [target, setTarget] = useState<SimulationRow | null>(null)
  const cancel = useMutation({
    // Always the parent record: a batch child cannot be cancelled on BRAIN.
    mutationFn: (row: SimulationRow) => simulations.cancel(row.id),
    onSuccess: (result, row) => {
      const what = `${row.isBatch ? 'batch' : 'simulation'} ${row.platformId ?? row.id}`
      if (result.acknowledged) toast.success(`BRAIN acknowledged the cancel of ${what}.`)
      else toast.error(`BRAIN did not acknowledge the cancel of ${what}. It may still be running.`)
      for (const queryKey of [['simulations'], ['bar'], ['today']]) void queryClient.invalidateQueries({ queryKey })
    },
    onError: (error) => toast.error(errorMessage(error)),
    onSettled: () => setTarget(null),
  })

  return (
    <Panel
      title={`${slots} cores × ${maxBatch} Alphas`}
      description="Every Alpha in one batch shares the same Type, Region, Delay, Instrument Type and Language. Only simulations that have started are shown here. Waiting ones start as soon as a core is free; the queued count shows how many are waiting."
      actions={
        status && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className={STAT}>
              <span className="num text-ink">
                {fmt.int(status.slotsUsed)}/{fmt.int(status.slots)}
              </span>
              cores busy
            </span>
            <span className={STAT}>
              <span className="num text-ink">{fmt.int(status.queuedTotal)}</span>
              queued
            </span>
            <span className={STAT}>
              <span className="num text-ink">{fmt.int(status.maxBatch)}</span>
              per batch
            </span>
          </div>
        )
      }
      bodyClassName="flex flex-col gap-3"
    >
      {status?.dailyLimitHit && (
        <Notice tone="warn" title="BRAIN's daily simulation limit is reached">
          Nothing more will be sent until the allowance resets at midnight US Eastern. Queued work stays queued.
        </Notice>
      )}
      {engine.isError && <ErrorNotice error={engine.error} title="The engine status could not load" />}
      {live === null && fallback.isError && <ErrorNotice error={fallback.error} title="Running simulations could not load" />}
      {overflow > 0 && (
        <Notice tone="warn">
          <span className="num">{fmt.int(overflow)}</span> more running {overflow === 1 ? 'slot holder' : 'slot holders'} than the{' '}
          <span className="num">{slots}</span> cores; only the oldest {slots} are drawn.
        </Notice>
      )}

      {!active ? (
        <div className="flex flex-col gap-2">
          {Array.from({ length: slots }, (_, i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      ) : (
        <div className="flex flex-col">
          {cores.map((core, i) => (
            <CoreRow key={i} index={i} core={core} maxBatch={maxBatch} elapsed={secondsSince(core.holder?.submittedAt, now)} onCancel={setTarget} />
          ))}
        </div>
      )}

      <Confirm
        open={target !== null}
        onOpenChange={(open) => !open && !cancel.isPending && setTarget(null)}
        title={target?.isBatch ? 'Cancel this batch?' : 'Cancel this simulation?'}
        confirmLabel="Cancel on BRAIN"
        cancelLabel="Keep running"
        danger
        pending={cancel.isPending}
        onConfirm={() => target && cancel.mutate(target)}
      >
        {target?.isBatch ? (
          <>
            Asks BRAIN to cancel batch <span className="num text-ink">{target.platformId ?? target.id}</span>
            {target.expression ? ` (${target.expression})` : ''}. None of its Alphas will finish.
          </>
        ) : (
          <>
            Asks BRAIN to cancel simulation <span className="num text-ink">{target?.platformId ?? target?.id}</span>. It will not finish.
          </>
        )}
      </Confirm>
    </Panel>
  )
}

function CoreRow({
  index,
  core,
  maxBatch,
  elapsed,
  onCancel,
}: {
  index: number
  core: Core
  maxBatch: number
  elapsed: number | null
  onCancel: (row: SimulationRow) => void
}) {
  const { holder } = core
  return (
    <div className="flex flex-col gap-2 border-b border-hairline/60 py-3 first:pt-0 last:border-b-0 last:pb-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-xs">
        <span className="num w-6 shrink-0 text-ink-tertiary">C{index + 1}</span>

        <div
          aria-label={`Core ${index + 1} batch key`}
          className={cx('flex h-7 flex-wrap items-center gap-x-4 rounded-md border px-2.5', holder ? 'border-hairline-strong bg-surface-2' : 'border-hairline')}
        >
          {BATCH_KEY.map((key) => (
            <span key={key.label} className="flex items-center gap-1.5 whitespace-nowrap">
              <span className="text-ink-tertiary">{key.label}</span>
              <span className={cx('num', holder ? 'text-ink' : 'text-ink-tertiary')}>{holder ? key.value(holder) : DASH}</span>
            </span>
          ))}
        </div>

        {holder ? (
          <div className="ml-auto flex min-w-0 items-center gap-3">
            <span className="max-w-56 truncate text-ink-muted" title={holder.task}>
              {holder.task}
            </span>
            <span className="num shrink-0 text-ink-subtle" title={holder.platformId ?? undefined}>
              {holder.platformId ? (holder.platformId.length > 8 ? `${holder.platformId.slice(0, 8)}…` : holder.platformId) : 'pending'}
            </span>
            <span
              title="Progress reported by BRAIN"
              className="flex h-7 shrink-0 items-center gap-2 rounded-md border border-brand-secure/55 bg-brand-secure/20 px-2.5"
            >
              <Progress value={holder.progress} className="w-16" label="Progress" />
              <span className="num w-10 text-right text-[13px] font-medium text-ink">{fmt.pct(holder.progress, 0)}</span>
            </span>
            <span
              title="Time since this batch started"
              className="flex h-7 shrink-0 items-center gap-1.5 rounded-md border border-brand-secure/55 bg-brand-secure/20 px-2.5 text-brand-secure"
            >
              <ClockIcon className="size-3.5" aria-hidden />
              <span className="num min-w-12 text-right text-[13px] font-medium text-ink">{fmt.duration(elapsed)}</span>
            </span>
            <Menu
              trigger={
                <Button variant="ghost" size="icon-sm" aria-label={`Core ${index + 1} actions`}>
                  <EllipsisIcon />
                </Button>
              }
              items={[{ label: holder.isBatch ? 'Cancel batch' : 'Cancel simulation', danger: true, onClick: () => onCancel(holder) }]}
            />
          </div>
        ) : (
          <span className="ml-auto text-ink-tertiary">Idle</span>
        )}
      </div>

      <div className="grid min-w-0 gap-1.5" style={{ gridTemplateColumns: `repeat(${maxBatch}, minmax(0, 1fr))` }}>
        {core.cells.map((cell, j) => (
          <MatrixCell key={j} cell={cell} holder={holder} />
        ))}
      </div>
    </div>
  )
}

/** No per-cell clock: every Alpha in a batch starts and ends with it, so the time lives once on the core row. */
function MatrixCell({ cell, holder }: { cell: Cell; holder: SimulationRow | null }) {
  const className = cx(
    'num flex h-9 min-w-0 items-center justify-center truncate rounded-sm px-1 text-xs',
    cell.state === 'RUNNING' && 'bg-primary/12 text-ink',
    cell.state === 'PENDING' && 'bg-surface-2 text-ink-subtle',
    cell.state === 'EMPTY' && 'border border-hairline',
  )
  if (cell.state === 'EMPTY' || !holder) return <div className={className} aria-hidden />

  const row = cell.row
  return (
    <Tooltip
      content={
        <div className="flex flex-col gap-1">
          <span className="num break-all text-ink">{row?.expression ?? 'Not linked to its batch yet'}</span>
          <span>
            {cell.state === 'RUNNING' ? 'Running' : 'Pending'} · Universe <span className="num">{row?.universe ?? row?.settings?.universe ?? DASH}</span> ·
            Neutralization <span className="num">{row?.settings?.neutralization ?? DASH}</span>
          </span>
          <span>
            Task <span className="num">{holder.task}</span> · batch <span className="num">{holder.platformId ?? 'pending'}</span>
          </span>
        </div>
      }
    >
      <div className={className} tabIndex={0} aria-label={row?.expression ?? (cell.state === 'RUNNING' ? 'Running Alpha' : 'Pending Alpha')} />
    </Tooltip>
  )
}
