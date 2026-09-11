/**
 * The Sync with BRAIN matrix: every market BRAIN offers as a Region × Universe matrix, each
 * cell split into Delay 0 | Delay 1, filling live as a sync works through it. Click a half
 * to open that market in the Data Explorer.
 */

import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { XIcon } from 'lucide-react'
import { toast } from 'sonner'
import { catalog } from '@/api/catalog'
import { errorMessage } from '@/api/http'
import type { Scope, SyncMarket } from '@/api/types'
import { fmt } from '@/lib/format'
import { useLive } from '@/lib/live'
import { Button, ErrorNotice, Notice, Panel, Progress, Skeleton, cx } from '@/ui/kit'
import { Confirm } from '@/ui/overlay'
import { SyncButton } from './shared'
import { STAT } from './state'

type TileState = SyncMarket['state']

/** Each Delay half, by state. Fetching sweeps a lavender shimmer across; synced settles solid. */
const HALF: Record<TileState, string> = {
  waiting: 'bg-surface-3',
  fetching:
    'animate-sweep bg-[length:200%_100%] bg-[linear-gradient(90deg,color-mix(in_srgb,var(--color-primary)_15%,transparent),color-mix(in_srgb,var(--color-primary)_70%,transparent),color-mix(in_srgb,var(--color-primary)_15%,transparent))]',
  fields: 'bg-ink-tertiary',
  details: 'animate-pulse bg-ink-tertiary ring-1 ring-primary ring-inset',
  done: 'bg-ink-muted',
  failed: 'bg-loss/70',
}

const LEGEND: [TileState, string][] = [
  ['waiting', 'Not synced'],
  ['fetching', 'Fetching fields'],
  ['fields', 'Fields ready'],
  ['details', 'Filling details'],
  ['done', 'Synced'],
  ['failed', 'Failed'],
]
const LABEL = Object.fromEntries(LEGEND) as Record<TileState, string>

const NO_DELAY = 'bg-[repeating-linear-gradient(135deg,var(--color-hairline-strong)_0_1px,transparent_1px_5px)]'

const keyOf = (region: string, delay: number, universe: string) => `${region}|${delay}|${universe}`

export function SyncHero({ scope, onPick }: { scope: Scope; onPick: (change: Partial<Scope>) => void }) {
  const queryClient = useQueryClient()
  const markets = useQuery({ queryKey: ['catalog', 'markets'], queryFn: catalog.markets, staleTime: 60 * 60 * 1000 })
  const scopes = useQuery({ queryKey: ['catalog', 'scopes'], queryFn: catalog.scopes })

  // Progress arrives on the socket; a finished run refreshes everything the catalog feeds.
  const run = useLive((s) => s.sync)
  const full = run?.all ? run : null
  const running = full?.status === 'RUNNING' ? full : null
  const settled = run && run.status !== 'RUNNING' ? `${run.id}:${run.status}` : null
  useEffect(() => {
    if (settled) void queryClient.invalidateQueries({ queryKey: ['catalog'] })
  }, [settled, queryClient])

  const [confirmCancel, setConfirmCancel] = useState(false)
  const cancel = useMutation({
    mutationFn: catalog.cancel,
    onSuccess: (result) => {
      toast.success(result.cancelled ? 'Sync cancelled' : 'The sync had already finished')
      void queryClient.invalidateQueries({ queryKey: ['catalog'] })
    },
    onError: (error) => toast.error(errorMessage(error)),
    onSettled: () => setConfirmCancel(false),
  })

  const layout = useMemo(() => {
    const list = markets.data ?? []
    const regions = [...new Set(list.map((m) => m.region))]
    const delays = [...new Set(list.map((m) => m.delay))].sort((a, b) => a - b)
    const universes = new Map<string, string[]>()
    for (const m of list) {
      const column = universes.get(m.region) ?? []
      if (!column.includes(m.universe)) column.push(m.universe)
      universes.set(m.region, column)
    }
    const exists = new Set(list.map((m) => keyOf(m.region, m.delay, m.universe)))
    return { total: list.length, regions, delays, universes, exists }
  }, [markets.data])

  // While a sync runs its own per-market states are the truth; otherwise what the catalog holds,
  // plus any market the last sync could not finish.
  const states = useMemo(() => {
    const map = new Map<string, { state: TileState; fields: number | null }>()
    for (const r of scopes.data ?? []) map.set(keyOf(r.region, r.delay, r.universe), { state: 'done', fields: r.fields })
    for (const m of full?.markets ?? []) {
      const key = keyOf(m.region, m.delay, m.universe)
      if (running || m.state === 'failed' || m.state === 'fields') map.set(key, { state: m.state, fields: m.fields ?? map.get(key)?.fields ?? null })
    }
    return map
  }, [scopes.data, full, running])

  const syncedMarkets = scopes.data?.length ?? 0
  const syncedFields = (scopes.data ?? []).reduce((sum, r) => sum + r.fields, 0)

  return (
    <Panel
      title="BRAIN Datasets"
      description={
        <span className="mt-1.5 flex flex-wrap items-center gap-2">
          {running ? (
            <>
              <span className={STAT}>{running.stage === 'details' ? 'Filling in dataset details' : 'Syncing data fields'}</span>
              <span className={STAT}>
                <span className="num text-ink">
                  {fmt.int(running.scopesDone)}/{fmt.int(running.scopesTotal)}
                </span>
                markets
              </span>
              <span className={STAT}>
                <span className="num text-ink">{fmt.pct(running.fraction, 0)}</span>
              </span>
            </>
          ) : (
            <>
              <span className={STAT}>
                <span className="num text-ink">{fmt.int(syncedMarkets)}</span>of<span className="num text-ink">{fmt.int(layout.total)}</span>markets synced
              </span>
              <span className={STAT}>
                <span className="num text-ink">{fmt.int(syncedFields)}</span>fields
              </span>
            </>
          )}
        </span>
      }
      actions={
        running ? (
          <Button variant="danger" size="sm" loading={cancel.isPending} onClick={() => setConfirmCancel(true)}>
            {!cancel.isPending && <XIcon />}
            Cancel sync
          </Button>
        ) : (
          <SyncButton variant="primary">Sync</SyncButton>
        )
      }
      bodyClassName="flex flex-col gap-3"
    >
      {markets.isError && <ErrorNotice error={markets.error} title="Could not list BRAIN's markets" />}
      {scopes.isError && <ErrorNotice error={scopes.error} title="Could not read which markets are synced" />}
      {full && !running && full.error && (
        <Notice
          tone={full.status === 'FAILED' ? 'error' : 'warn'}
          title={full.status === 'FAILED' ? 'The sync failed' : full.status === 'CANCELLED' ? 'The sync was cancelled' : 'Some markets did not sync, even after 3 tries'}
        >
          {full.error}
        </Notice>
      )}

      {markets.isPending ? (
        <Skeleton className="h-72" />
      ) : (
        <div className="overflow-x-auto">
          <div
            role="group"
            aria-label="Sync matrix: Region by Universe, each split into Delay 0 and Delay 1"
            className="grid min-w-max gap-2"
            style={{ gridTemplateColumns: `repeat(${layout.regions.length}, minmax(112px, 1fr))` }}
          >
            {layout.regions.map((region) => (
              <div key={region} className="flex flex-col gap-1.5">
                <div className="flex items-baseline justify-between px-1">
                  <span className="num text-[13px] font-medium text-ink">{region}</span>
                  <span className="num text-[10px] text-ink-tertiary">
                    {layout.delays.map((d) => `D${d}`).join(' · ')}
                  </span>
                </div>
                {(layout.universes.get(region) ?? []).map((universe) => (
                  <div key={universe} className="flex flex-col gap-1 rounded-md border border-hairline bg-surface-2/60 p-1.5">
                    <span className="num truncate px-0.5 text-[11px] text-ink-subtle" title={universe}>
                      {universe}
                    </span>
                    <div className="grid gap-1" style={{ gridTemplateColumns: `repeat(${layout.delays.length}, minmax(0, 1fr))` }}>
                      {layout.delays.map((delay) => {
                        const key = keyOf(region, delay, universe)
                        if (!layout.exists.has(key)) {
                          return <span key={delay} title={`${region} · ${universe} has no Delay ${delay}`} className={cx('h-5 rounded-[3px]', NO_DELAY)} />
                        }
                        const tile = states.get(key) ?? { state: 'waiting' as const, fields: null }
                        const selected = scope.region === region && scope.delay === delay && scope.universe === universe
                        const label = `${region} · Delay ${delay} · ${universe}: ${LABEL[tile.state]}${tile.fields != null ? ` · ${fmt.int(tile.fields)} fields` : ''}`
                        return (
                          <button
                            key={delay}
                            type="button"
                            title={label}
                            aria-label={label}
                            onClick={() => onPick({ region, delay, universe })}
                            className={cx(
                              'h-5 rounded-[3px] transition-colors duration-500 hover:brightness-125',
                              HALF[tile.state],
                              selected && 'ring-2 ring-primary-hover ring-offset-1 ring-offset-surface-2',
                            )}
                          />
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}

      {running && <Progress value={running.fraction} label="Sync progress" />}

      <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-ink-subtle">
        <div className="flex flex-wrap items-center gap-3">
          {LEGEND.map(([state, text]) => (
            <span key={state} className="flex items-center gap-1.5">
              <span className={cx('h-3 w-5 rounded-[3px]', HALF[state])} />
              {text}
            </span>
          ))}
          <span className="flex items-center gap-1.5">
            <span className={cx('h-3 w-5 rounded-[3px]', NO_DELAY)} />
            Not offered
          </span>
        </div>
        <span>Each cell: Delay 0 | Delay 1 · click one to explore that market</span>
      </div>

      <Confirm
        open={confirmCancel}
        onOpenChange={setConfirmCancel}
        title="Cancel this sync?"
        confirmLabel="Cancel sync"
        cancelLabel="Keep syncing"
        danger
        pending={cancel.isPending}
        onConfirm={() => running && cancel.mutate(running.id)}
      >
        Stops the sync. Markets that already arrived stay in the catalog; press Sync again to finish the rest.
      </Confirm>
    </Panel>
  )
}
