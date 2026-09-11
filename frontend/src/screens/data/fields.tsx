/** Every field in the market: server-sorted, offset-paged, filtered; a row opens its detail. */

import { useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { catalog, type DataFieldRow, type FieldAvailabilityRow, type FieldFilter, type FieldSortKey } from '@/api/catalog'
import { scopeLabel, type Scope } from '@/api/types'
import { useDatasetPick } from '@/lib/dataset-pick'
import { DASH, fmt } from '@/lib/format'
import { Badge, Button, Chips, Disclosure, ErrorNotice, Field, Input, KV, Panel, Skeleton, cx } from '@/ui/kit'
import { Sheet } from '@/ui/overlay'
import { DataTable, Pager, type Column, type Sort } from '@/ui/table'
import { DatasetTree, Text } from './shared'
import { STAT, isActive, multiplier, parseThemes, sortRows, useDatasetChoice, useDebounced, useFieldFilter, type FieldFilterState } from './state'

const LIMIT = 100
const TYPES = ['MATRIX', 'VECTOR', 'GROUP']

const COLUMNS: Column<DataFieldRow>[] = [
  { key: 'field_id', header: 'Field', width: 'minmax(180px,1.4fr)', sortable: true, cell: (r) => <Text value={r.field_id} mono className="text-ink" /> },
  { key: 'description', header: 'Description', width: 'minmax(240px,2.4fr)', cell: (r) => <Text value={r.description} className="text-ink-muted" /> },
  { key: 'dataset_id', header: 'Dataset', width: 'minmax(120px,1fr)', sortable: true, cell: (r) => <Text value={r.dataset_id} mono className="text-ink-muted" /> },
  {
    key: 'category_id',
    header: 'Category',
    width: 'minmax(160px,1.2fr)',
    sortable: true,
    cell: (r) => <Text value={[r.category_name, r.subcategory_name].filter(Boolean).join(' / ') || null} className="text-ink-muted" />,
  },
  { key: 'field_type', header: 'Type', width: '88px', sortable: true, cell: (r) => <span className="num text-xs text-ink-subtle">{r.field_type ?? DASH}</span> },
  { key: 'coverage', header: 'Coverage', width: '96px', align: 'right', sortable: true, cell: (r) => fmt.pct(r.coverage) },
  { key: 'user_count', header: 'Users', width: '88px', align: 'right', sortable: true, cell: (r) => fmt.int(r.user_count) },
  { key: 'alpha_count', header: 'Alphas', width: '88px', align: 'right', sortable: true, cell: (r) => fmt.int(r.alpha_count) },
  { key: 'pyramid_multiplier', header: 'Pyramid', width: '88px', align: 'right', sortable: true, cell: (r) => multiplier(r.pyramid_multiplier) },
]

const ADVANCED: (keyof FieldFilterState)[] = [
  'dataset_ids',
  'coverage_min',
  'coverage_max',
  'alpha_count_min',
  'alpha_count_max',
  'user_count_min',
  'user_count_max',
  'pyramid_multiplier_min',
]

export function FieldsTab({ scope }: { scope: Scope }) {
  const { filter, sort, offset, setSort, page } = useFieldFilter()
  const [datasetIds] = useDatasetChoice()
  const active: FieldFilterState = { ...filter, dataset_ids: datasetIds }
  const [openId, setOpenId] = useState<string | null>(null)

  // A new market starts at its first page.
  const label = scopeLabel(scope)
  const seen = useRef(label)
  useEffect(() => {
    if (seen.current !== label) {
      seen.current = label
      page(0)
    }
  }, [label, page])

  const body: FieldFilter = { ...active, sort_by: sort.key as FieldSortKey, sort_desc: sort.desc, limit: LIMIT, offset }
  const query = useQuery({
    queryKey: ['catalog', 'fields', scope, body],
    queryFn: () => catalog.fields(scope, body),
    placeholderData: keepPreviousData,
  })
  const filtered = Object.values(active).some(isActive)

  return (
    <Panel
      title="Fields"
      actions={
        query.data && (
          <span className={STAT}>
            <span className="num text-ink">{fmt.int(query.data.total)}</span>
            fields
          </span>
        )
      }
    >
      <div className="flex flex-col gap-3">
        <FieldFilters scope={scope} />
        {query.isError && <ErrorNotice error={query.error} title="Could not load fields" />}
        <DataTable
          label="Data fields"
          rows={query.data?.results ?? []}
          columns={COLUMNS}
          rowKey={(r) => r.field_id}
          sort={sort}
          onSort={setSort}
          onRowClick={(r) => setOpenId(r.field_id)}
          loading={query.isPending}
          empty={filtered ? 'No fields match these filters.' : 'No fields in this market yet. Download it in Sync with BRAIN.'}
        />
        {query.data && <Pager total={query.data.total} offset={offset} limit={LIMIT} onChange={page} />}
      </div>
      <FieldSheet scope={scope} id={openId} onClose={() => setOpenId(null)} />
    </Panel>
  )
}

function FieldFilters({ scope }: { scope: Scope }) {
  const { filter, set, replace } = useFieldFilter()
  const [datasetIds, setDatasetIds] = useDatasetChoice()
  const picking = useDatasetPick((s) => s.active)
  const active: FieldFilterState = { ...filter, dataset_ids: datasetIds }
  const facets = useQuery({ queryKey: ['catalog', 'facets', scope, active], queryFn: () => catalog.facets(scope, active), placeholderData: keepPreviousData })
  // The market's whole tree whatever else is filtered, so ticking a category takes every dataset in it.
  const tree = useQuery({ queryKey: ['catalog', 'facets', scope, {}], queryFn: () => catalog.facets(scope, {}) })
  const datasets = useQuery({ queryKey: ['catalog', 'datasets', scope, ''], queryFn: () => catalog.datasets(scope) })
  const names = useMemo(() => new Map((datasets.data ?? []).map((d) => [d.dataset_id, d.name ?? d.dataset_id])), [datasets.data])
  const stats = useQuery({ queryKey: ['catalog', 'stats', scope], queryFn: () => catalog.stats(scope) })

  // The search box types freely; the query follows a beat later.
  const [search, setSearch] = useState(filter.search ?? '')
  const term = useDebounced(search.trim(), 250)
  useEffect(() => {
    if ((useFieldFilter.getState().filter.search ?? '') !== term) set({ search: term || null })
  }, [term, set])

  // Counts follow every other filter; a chosen type stays listed even when nothing else matches it.
  const typeCounts = new Map(facets.data?.types.map((t) => [t.id, t.n]))
  const types = [...new Set([...(facets.data ? facets.data.types.map((t) => t.id) : TYPES), ...(filter.field_types ?? [])])]
  const advancedOn = ADVANCED.filter((k) => isActive(active[k])).length
  const s = stats.data

  // A lab choosing datasets lands here: More filters opens, lit, and scrolls into view.
  const more = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (picking) more.current?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [picking])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <Input className="w-full sm:w-80" placeholder="Search field id or description" aria-label="Search fields" value={search} onChange={(e) => setSearch(e.target.value)} />
        <Chips
          label="Field type"
          value={filter.field_types ?? []}
          onChange={(v) => set({ field_types: v })}
          items={types.map((t) => ({
            value: t,
            label: (
              <span className="num">
                {t} <span className="text-ink-tertiary">{fmt.int(facets.data ? (typeCounts.get(t) ?? 0) : null)}</span>
              </span>
            ),
          }))}
        />
        <Button
          variant="ghost"
          size="sm"
          disabled={!Object.values(active).some(isActive) && !search}
          onClick={() => {
            setSearch('')
            replace({})
            setDatasetIds([])
          }}
        >
          Reset filters
        </Button>
      </div>
      {facets.isError && <ErrorNotice error={facets.error} title="Could not load filter choices" />}
      {stats.isError && <ErrorNotice error={stats.error} title="Could not load field statistics" />}
      {tree.isError && <ErrorNotice error={tree.error} title="Could not load this market's categories and datasets" />}

      <div ref={more} className="scroll-mt-24">
        <Disclosure
          defaultOpen={picking || undefined}
          className={cx(picking && 'border-primary/60 ring-1 ring-primary/30')}
          summary={
            <>
              More filters
              {advancedOn > 0 && <Badge className="num">{advancedOn}</Badge>}
            </>
          }
        >
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div className="min-w-0 md:col-span-2 xl:col-span-3">
              {tree.data ? (
                <DatasetTree source={tree.data} counts={facets.data} names={names} value={datasetIds} onChange={setDatasetIds} />
              ) : (
                !tree.isError && <Skeleton className="h-40" />
              )}
            </div>
            <Range
              label="Coverage (%)"
              hint={s && `In this market: ${fmt.pct(s.coverage_min)} – ${fmt.pct(s.coverage_max)}`}
              scale={100}
              min={filter.coverage_min}
              max={filter.coverage_max}
              onChange={(coverage_min, coverage_max) => set({ coverage_min, coverage_max })}
            />
            <Range
              label="Alpha Count"
              hint={s && `Highest in this market: ${fmt.int(s.alpha_count_max)}`}
              min={filter.alpha_count_min}
              max={filter.alpha_count_max}
              onChange={(alpha_count_min, alpha_count_max) => set({ alpha_count_min, alpha_count_max })}
            />
            <Range
              label="User Count"
              hint={s && `Highest in this market: ${fmt.int(s.user_count_max)}`}
              min={filter.user_count_min}
              max={filter.user_count_max}
              onChange={(user_count_min, user_count_max) => set({ user_count_min, user_count_max })}
            />
            <Field label="Pyramid Multiplier at least" hint={s && `Highest in this market: ${multiplier(s.pyramid_multiplier_max)}`}>
              <NumberBox label="Pyramid Multiplier at least" step={0.1} value={filter.pyramid_multiplier_min} onChange={(v) => set({ pyramid_multiplier_min: v })} />
            </Field>
          </div>
        </Disclosure>
      </div>
    </div>
  )
}

function NumberBox({
  label,
  value,
  onChange,
  scale = 1,
  step,
  placeholder,
}: {
  label: string
  value: number | null | undefined
  onChange: (value: number | null) => void
  scale?: number
  step?: number
  placeholder?: string
}) {
  return (
    <Input
      type="number"
      min={0}
      step={step}
      aria-label={label}
      placeholder={placeholder}
      value={value == null ? '' : +(value * scale).toFixed(6)}
      onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value) / scale)}
    />
  )
}

function Range({
  label,
  hint,
  min,
  max,
  onChange,
  scale,
}: {
  label: string
  hint?: string | null
  min: number | null | undefined
  max: number | null | undefined
  onChange: (min: number | null, max: number | null) => void
  scale?: number
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="text-xs font-medium text-ink-muted">{label}</span>
      <div className="grid grid-cols-2 gap-2">
        <NumberBox label={`${label} minimum`} placeholder="min" scale={scale} value={min} onChange={(v) => onChange(v, max ?? null)} />
        <NumberBox label={`${label} maximum`} placeholder="max" scale={scale} value={max} onChange={(v) => onChange(min ?? null, v)} />
      </div>
      {hint && <span className="text-xs text-ink-tertiary">{hint}</span>}
    </div>
  )
}

const AVAILABILITY_COLUMNS: Column<FieldAvailabilityRow>[] = [
  { key: 'region', header: 'Region', width: '80px', cell: (r) => <span className="num">{r.region}</span> },
  { key: 'delay', header: 'Delay', width: '64px', cell: (r) => <span className="num">{r.delay}</span> },
  { key: 'universe', header: 'Universe', width: 'minmax(110px,1fr)', cell: (r) => <span className="num">{r.universe}</span> },
  { key: 'coverage', header: 'Coverage', width: '96px', align: 'right', cell: (r) => fmt.pct(r.coverage) },
  { key: 'alpha_count', header: 'Alpha Count', width: '112px', align: 'right', sortable: true, cell: (r) => fmt.int(r.alpha_count) },
]

function FieldSheet({ scope, id, onClose }: { scope: Scope; id: string | null; onClose: () => void }) {
  const detail = useQuery({ queryKey: ['catalog', 'field', scope, id], queryFn: () => catalog.field(scope, id!), enabled: id != null })
  const availability = useQuery({ queryKey: ['catalog', 'availability', id], queryFn: () => catalog.availability(id!), enabled: id != null })
  const [availabilitySort, setAvailabilitySort] = useState<Sort>({ key: 'alpha_count', desc: true })
  const availabilityRows = useMemo(() => sortRows(availability.data ?? [], availabilitySort), [availability.data, availabilitySort])
  const d = detail.data
  const themes = parseThemes(d?.themes ?? null)

  return (
    <Sheet
      open={id != null}
      onOpenChange={(open) => !open && onClose()}
      title={<span className="num">{id}</span>}
      description={d?.description ?? (detail.isPending ? 'Loading…' : 'No description.')}
    >
      <div className="flex flex-col gap-6">
        {detail.isError ? (
          <ErrorNotice error={detail.error} title="Could not load this field" />
        ) : !d ? (
          <Skeleton className="h-56" />
        ) : (
          <div className="flex flex-col gap-3">
            <KV
              items={[
                ['Dataset', d.dataset_id ?? DASH],
                ['Category', d.category_name ?? DASH],
                ['Subcategory', d.subcategory_name ?? DASH],
                ['Type', d.field_type ?? DASH],
                ['Coverage', fmt.pct(d.coverage)],
                ['Alpha Count', fmt.int(d.alpha_count)],
                ['User Count', fmt.int(d.user_count)],
                ['Pyramid Multiplier', multiplier(d.pyramid_multiplier)],
                ['Scope', `${d.region} · D${d.delay} · ${d.universe}`],
                ['Downloaded', fmt.dateTime(d.synced_at)],
              ]}
            />
            {themes.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {themes.map((t) => (
                  <Badge key={t} tone="outline">
                    {t}
                  </Badge>
                ))}
              </div>
            )}
          </div>
        )}

        <section className="flex flex-col gap-2">
          <h3 className="title">Available in</h3>
          {availability.isError ? (
            <ErrorNotice error={availability.error} title="Could not check availability" />
          ) : (
            <DataTable
              label="Field availability"
              rows={availabilityRows}
              columns={AVAILABILITY_COLUMNS}
              sort={availabilitySort}
              onSort={setAvailabilitySort}
              rowKey={(r) => `${r.instrument_type}/${r.region}/${r.delay}/${r.universe}`}
              loading={availability.isPending}
              maxHeight="40vh"
              empty="No downloaded market has this field."
            />
          )}
        </section>
      </div>
    </Sheet>
  )
}
