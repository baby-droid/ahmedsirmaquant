/** Stored alphas: the local vault, sorted and filtered in DuckDB, served a page at a time. */

import { useDeferredValue, useMemo, useState } from 'react'
import { keepPreviousData, useMutation, useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { GaugeIcon, SearchIcon } from 'lucide-react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { pool, type AlphaMetricKey, type AlphaPageRequest, type AlphaRow, type AlphaSortKey } from '@/api/pool'
import type { Scope } from '@/api/types'
import { fmt } from '@/lib/format'
import { MAX_SEEDS, MIN_SEEDS, useSeedPick } from '@/lib/seed-pick'
import { useRefetchOn } from '@/lib/ws'
import { Badge, Button, Chips, Disclosure, ErrorNotice, Field, Input, Metric, Panel, Segmented, TEXT_TONE, signTone } from '@/ui/kit'
import { DataTable, Pager, type Column, type Sort } from '@/ui/table'
import { parseNum } from './helpers'

/** Bounds as the user types them; `div` converts the display unit to the wire fraction. */
const BOUNDS: { key: AlphaMetricKey; label: string; div: number }[] = [
  { key: 'sharpe', label: 'Sharpe', div: 1 },
  { key: 'fitness', label: 'Fitness', div: 1 },
  { key: 'turnover', label: 'Turnover %', div: 100 },
  { key: 'returns', label: 'Returns %', div: 100 },
  { key: 'drawdown', label: 'Drawdown %', div: 100 },
  { key: 'margin', label: 'Margin bps', div: 10_000 },
  { key: 'k_ratio', label: 'K-Ratio', div: 1 },
  { key: 'operator_count', label: 'Operators', div: 1 },
]

const ratio = (v: number | null) => fmt.ratio(v)
const signed = (render: (v: number | null) => string) => (v: number | null) => <span className={TEXT_TONE[signTone(v)]}>{render(v)}</span>

const COLUMNS: Column<AlphaRow>[] = [
  { key: 'alpha_id', header: 'Alpha', width: '96px', cell: (r) => <span className="num text-ink-muted">{r.alphaId}</span> },
  {
    key: 'expression',
    header: 'Expression',
    width: 'minmax(240px,2fr)',
    cell: (r) => (
      <span className="num truncate" title={r.expression ?? undefined}>
        {r.expression ?? '—'}
      </span>
    ),
  },
  {
    key: 'market',
    header: 'Region · Universe',
    width: '170px',
    cell: (r) => (
      <span className="num text-ink-subtle">
        {r.region ?? '—'} · {r.universe ?? '—'} · D{r.delay ?? '—'}
      </span>
    ),
  },
  { key: 'sharpe', header: 'Sharpe', width: '80px', align: 'right', sortable: true, cell: (r) => signed(ratio)(r.sharpe) },
  { key: 'fitness', header: 'Fitness', width: '80px', align: 'right', sortable: true, cell: (r) => fmt.ratio(r.fitness) },
  { key: 'turnover', header: 'Turnover', width: '88px', align: 'right', sortable: true, cell: (r) => fmt.pct(r.turnover) },
  { key: 'returns', header: 'Returns', width: '88px', align: 'right', sortable: true, cell: (r) => signed((v) => fmt.pct(v))(r.returns) },
  { key: 'drawdown', header: 'Drawdown', width: '92px', align: 'right', sortable: true, cell: (r) => fmt.pct(r.drawdown) },
  { key: 'margin', header: 'Margin', width: '92px', align: 'right', sortable: true, cell: (r) => fmt.bps(r.margin) },
  { key: 'k_ratio', header: 'K-Ratio', width: '80px', align: 'right', sortable: true, cell: (r) => fmt.ratio(r.kRatio) },
  { key: 'operator_count', header: 'Operators', width: '88px', align: 'right', sortable: true, cell: (r) => fmt.int(r.operatorCount) },
  { key: 'date_created', header: 'Created', width: '112px', align: 'right', sortable: true, cell: (r) => fmt.date(r.dateCreated) },
  { key: 'pnl', header: 'Daily PnL', width: '88px', cell: (r) => (r.hasPnl ? <Badge tone="muted">stored</Badge> : <span className="text-ink-tertiary">—</span>) },
]

/** Selection actions: clickable, so the lavender interaction tint (DESIGN.md), as on selected chips. */
const SELECTION_ACTION = 'border-primary/40 bg-primary/10 text-primary-hover hover:border-primary/70 hover:bg-primary/20'

const unique = <T,>(values: (T | null)[]) => [...new Set(values.filter((v): v is T => v != null))]

export function Stored({ onOpen }: { onOpen: (alphaId: string) => void }) {
  const overview = useQuery({ queryKey: ['pool', 'overview'], queryFn: pool.overview })
  const [submitted, setSubmitted] = useState<'no' | 'yes'>('no')
  const [search, setSearch] = useState('')
  const [regions, setRegions] = useState<string[]>([])
  const [delays, setDelays] = useState<string[]>([])
  const [universes, setUniverses] = useState<string[]>([])
  const [bounds, setBounds] = useState<Record<string, { min: string; max: string }>>({})
  const [sort, setSort] = useState<Sort>({ key: 'sharpe', desc: true })
  const [limit, setLimit] = useState(100)
  const [offset, setOffset] = useState(0)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const deferredSearch = useDeferredValue(search)
  // Picking seeds for Evolution Lab: only its market's unsubmitted Alphas, ticked into the pick.
  const pick = useSeedPick()
  const market = pick.active ? pick.scope : null
  const picked = useMemo(() => new Set(pick.ids), [pick.ids])

  /** Any filter change starts again from the first page. */
  const reset = <T,>(set: (v: T) => void) => (v: T) => {
    set(v)
    setOffset(0)
  }

  const body: AlphaPageRequest = {
    submitted: market ? false : submitted === 'yes',
    sort_by: sort.key as AlphaSortKey,
    sort_desc: sort.desc,
    regions: market ? [market.region] : regions.length ? regions : null,
    delays: market ? [market.delay] : delays.length ? delays.map(Number) : null,
    universes: market ? [market.universe] : universes.length ? universes : null,
    search: deferredSearch.trim() || null,
    minimum: Object.fromEntries(BOUNDS.flatMap((b) => { const v = parseNum(bounds[b.key]?.min ?? ''); return v === undefined ? [] : [[b.key, v / b.div]] })),
    maximum: Object.fromEntries(BOUNDS.flatMap((b) => { const v = parseNum(bounds[b.key]?.max ?? ''); return v === undefined ? [] : [[b.key, v / b.div]] })),
    limit,
    offset,
  }
  const page = useQuery({ queryKey: ['pool', 'stored', body], queryFn: () => pool.query(body), placeholderData: keepPreviousData })
  useRefetchOn('tasks', ['pool', 'stored'], 5000)
  // An Alpha simulated from the app is signalled here once it is stored locally.
  useRefetchOn('simulations', ['pool', 'stored'], 5000)
  const rows = useMemo(() => page.data?.results ?? [], [page.data])

  const scopes = overview.data?.scopes ?? []
  const regionItems = unique(scopes.map((s) => s.region)).map((v) => ({ value: v, label: v }))
  const delayItems = unique(scopes.map((s) => (s.delay == null ? null : String(s.delay)))).map((v) => ({ value: v, label: `D${v}` }))
  const universeItems = unique(scopes.map((s) => s.universe)).map((v) => ({ value: v, label: v }))

  const kRatio = useMutation({
    mutationFn: (ids: string[]) => pool.kRatio(ids),
    onSuccess: (r) => toast.success(`Computing K-Ratio for ${fmt.int(r.alphas)} Alphas in the background`),
    onError: (e) => toast.error(errorMessage(e)),
  })
  const kRatioIds = (selected.size ? [...selected] : rows.map((r) => r.alphaId)).slice(0, 100)

  const chosen = market ? picked : selected
  const toggle = (ids: string[], on: boolean) => {
    if (market) {
      pick.toggle(ids, on)
      return
    }
    setSelected((prev) => {
      const next = new Set(prev)
      ids.forEach((id) => (on ? next.add(id) : next.delete(id)))
      return next
    })
  }
  const activeBounds = BOUNDS.filter((b) => bounds[b.key]?.min || bounds[b.key]?.max).length + (market ? 0 : regions.length + delays.length + universes.length)

  return (
    <>
      {market && <SeedPickBar market={market} />}
      <Panel title="Stored Alphas" bodyClassName="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <div className="relative w-full max-w-sm">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-ink-tertiary" aria-hidden />
            <Input aria-label="Search Alpha id, name or expression" placeholder="Search id, name or expression" className="num pl-8" value={search} onChange={(e) => reset(setSearch)(e.target.value)} />
          </div>
          {!market && (
            <Segmented
              label="Submitted"
              value={submitted}
              onChange={reset(setSubmitted)}
              items={[
                { value: 'no', label: 'Unsubmitted' },
                { value: 'yes', label: 'Submitted' },
              ]}
            />
          )}
        </div>

        <Disclosure summary={activeBounds ? `Filters (${activeBounds} active)` : 'Filters'}>
          <div className="flex flex-col gap-4">
            {!market && (
              <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <Field label="Region">
                  <Chips label="Region" items={regionItems} value={regions} onChange={reset(setRegions)} />
                </Field>
                <Field label="Delay">
                  <Chips label="Delay" items={delayItems} value={delays} onChange={reset(setDelays)} />
                </Field>
                <Field label="Universe">
                  <Chips label="Universe" items={universeItems} value={universes} onChange={reset(setUniverses)} />
                </Field>
              </div>
            )}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {BOUNDS.map((b) => (
                <Field key={b.key} label={b.label}>
                  <div className="flex gap-1.5">
                    {(['min', 'max'] as const).map((side) => (
                      <Input
                        key={side}
                        type="number"
                        inputMode="decimal"
                        step="any"
                        aria-label={`${side === 'min' ? 'Minimum' : 'Maximum'} ${b.label}`}
                        placeholder={side}
                        value={bounds[b.key]?.[side] ?? ''}
                        onChange={(e) => reset(setBounds)({ ...bounds, [b.key]: { ...(bounds[b.key] ?? { min: '', max: '' }), [side]: e.target.value } })}
                      />
                    ))}
                  </div>
                </Field>
              ))}
            </div>
          </div>
        </Disclosure>

        <div className="flex flex-wrap items-center gap-2">
          {!market && (
            <span className="inline-flex h-7 items-center gap-1.5 rounded-md border border-brand-secure/35 bg-brand-secure/10 px-2.5 text-xs whitespace-nowrap text-brand-secure">
              <span className="num text-ink">{fmt.int(selected.size)}</span> selected
            </span>
          )}
          <Button size="sm" className={SELECTION_ACTION} disabled={rows.length === 0} onClick={() => toggle(rows.map((r) => r.alphaId), true)}>
            Select page
          </Button>
          {chosen.size > 0 && (
            <Button size="sm" className={SELECTION_ACTION} onClick={() => toggle([...chosen], false)}>
              Clear
            </Button>
          )}
          {!market && (
            <>
              <span className="flex-1" />
              <Button size="sm" loading={kRatio.isPending} disabled={kRatioIds.length === 0} onClick={() => kRatio.mutate(kRatioIds)}>
                {!kRatio.isPending && <GaugeIcon />}
                Compute K-Ratio for {selected.size ? `${fmt.int(kRatioIds.length)} selected` : `Top ${fmt.int(kRatioIds.length)}`}
              </Button>
            </>
          )}
        </div>
        {!market && selected.size > 100 && <p className="text-xs text-warn">K-Ratio runs on the first 100 selected Alphas.</p>}
        {page.isError && <ErrorNotice error={page.error} title="Could not read stored Alphas" />}

        <DataTable
          label="Stored Alphas"
          rows={rows}
          columns={COLUMNS}
          rowKey={(r) => r.alphaId}
          onRowClick={(r) => onOpen(r.alphaId)}
          sort={sort}
          onSort={(s) => {
            setSort(s)
            setOffset(0)
          }}
          selected={chosen}
          onSelect={(key, on) => toggle([key], on)}
          loading={page.isPending}
          empty={overview.data?.counts.alphas === 0 ? 'No Alphas stored yet. Press Sync from BRAIN above.' : 'No Alphas match these filters.'}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <Segmented label="Rows per page" value={limit} onChange={reset(setLimit)} items={[100, 250, 500].map((v) => ({ value: v, label: String(v) }))} />
          <Pager total={page.data?.total ?? 0} offset={offset} limit={limit} onChange={setOffset} />
        </div>
      </Panel>
    </>
  )
}

/** While Evolution Lab picks seeds: how many are ticked, in which market, and the way back to it. */
function SeedPickBar({ market }: { market: Scope }) {
  const navigate = useNavigate()
  const count = useSeedPick((s) => s.ids.length)
  const back = (done: boolean) => {
    const pick = useSeedPick.getState()
    if (done) pick.finish()
    else pick.cancel()
    void navigate({ to: '/labs/evolution' })
  }

  return (
    <div className="sticky top-0 z-10 flex flex-wrap items-center gap-3 rounded-lg border border-hairline bg-surface-1 p-3">
      <Metric boxed size="sm" label="Seeds Selected" value={fmt.int(count)} tone={count > MAX_SEEDS ? 'warn' : 'neutral'} />
      <Metric boxed size="sm" label="Market" value={`${market.region} · D${market.delay} · ${market.universe}`} />
      <span className="flex-1" />
      {count > MAX_SEEDS && <span className="text-xs text-warn">At most {fmt.int(MAX_SEEDS)} seeds.</span>}
      <Button variant="ghost" onClick={() => back(false)}>
        Cancel
      </Button>
      <Button variant="primary" disabled={count < MIN_SEEDS || count > MAX_SEEDS} onClick={() => back(true)}>
        Done
      </Button>
    </div>
  )
}
