/** Components the Data Explorer tabs share. */

import { useMemo, useState, type ReactNode } from 'react'
import { ChevronRightIcon, RefreshCwIcon, XIcon } from 'lucide-react'
import type { CatalogFacets } from '@/api/catalog'
import { DASH, fmt } from '@/lib/format'
import { Button, Input, cx } from '@/ui/kit'
import { Confirm } from '@/ui/overlay'
import { buildTree, summarize } from './dataset-tree'
import { useDownload } from './state'

/** One-line text cut to width, full text on hover. */
export function Text({ value, mono, className }: { value: string | null | undefined; mono?: boolean; className?: string }) {
  if (!value) return <span className="text-ink-tertiary">{DASH}</span>
  return (
    <span title={value} className={cx('truncate', mono && 'num', className)}>
      {value}
    </span>
  )
}

/** The one sync button: every market's data fields, then dataset details. Asks first, since it runs against BRAIN for minutes. */
export function SyncButton({
  children,
  variant = 'secondary',
  size = 'sm',
}: {
  children: ReactNode
  variant?: 'primary' | 'secondary'
  size?: 'sm' | 'md'
}) {
  const [open, setOpen] = useState(false)
  const sync = useDownload()
  return (
    <>
      <Button variant={variant} size={size} loading={sync.isPending} onClick={() => setOpen(true)}>
        {!sync.isPending && <RefreshCwIcon />}
        {children}
      </Button>
      <Confirm
        open={open}
        onOpenChange={setOpen}
        title="Sync BRAIN Datasets"
        confirmLabel="Sync"
        pending={sync.isPending}
        onConfirm={() => sync.mutate(undefined, { onSettled: () => setOpen(false) })}
      >
        Downloads the data fields of every market BRAIN offers, then fills in dataset details. It spends{' '}
        <span className="num text-ink">0</span> simulations. Fields are browsable in the Data Explorer as soon as they arrive; progress shows in Sync with BRAIN.
      </Confirm>
    </>
  )
}

/**
 * The dataset filter as a Category → Subcategory → Dataset tree: tick a whole category, whole
 * subcategories or single datasets. The choice is always the dataset ids under what is ticked,
 * which is what the fields query and the labs take. Counts are fields under the other filters.
 */
export function DatasetTree({
  source,
  counts,
  names,
  value,
  onChange,
}: {
  /** The market's whole tree, unfiltered, so a ticked category takes every dataset in it. */
  source: CatalogFacets
  counts: CatalogFacets | undefined
  names: Map<string, string>
  value: string[]
  onChange: (ids: string[]) => void
}) {
  const [query, setQuery] = useState('')
  const [open, setOpen] = useState<ReadonlySet<string>>(() => new Set())
  const tree = useMemo(() => buildTree(source), [source])
  const chosen = new Set(value)
  const term = query.trim().toLowerCase()
  const nameOf = (id: string) => names.get(id) ?? id
  const matches = (text: string) => text.toLowerCase().includes(term)
  const hit = (id: string) => matches(id) || matches(nameOf(id))

  const fieldsIn = counts && new Map(counts.datasets.map((d) => [d.id, d.n]))
  const total = (ids: string[]) => (fieldsIn ? ids.reduce((sum, id) => sum + (fieldsIn.get(id) ?? 0), 0) : null)

  const toggle = (ids: string[], on: boolean) => {
    const next = new Set(value)
    for (const id of ids) {
      if (on) next.add(id)
      else next.delete(id)
    }
    onChange([...next])
  }
  const expand = (key: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  // A search opens everything it shows.
  const isOpen = (key: string) => term !== '' || open.has(key)

  // A matching category or subcategory shows all of itself; otherwise only the datasets that match.
  const shown = tree.flatMap((trunk) => {
    const whole = !term || matches(trunk.name)
    const subs = trunk.subcategories.flatMap((branch) => {
      const ids = whole || matches(branch.name) ? branch.ids : branch.ids.filter(hit)
      return ids.length > 0 ? [{ branch, ids }] : []
    })
    const loose = whole ? trunk.loose : trunk.loose.filter(hit)
    return whole || subs.length > 0 || loose.length > 0 ? [{ trunk, subs, loose }] : []
  })

  const summary = summarize(tree, value, nameOf)

  return (
    <div className="flex min-w-0 flex-col gap-2">
      <div className="flex min-h-7 flex-wrap items-center justify-between gap-2">
        <span className="text-xs font-medium text-ink-muted">
          Datasets
          {value.length > 0 && <span className="num text-ink-subtle"> · {fmt.int(value.length)} selected</span>}
        </span>
        {value.length > 0 && (
          <Button size="sm" variant="ghost" onClick={() => onChange([])}>
            Clear
          </Button>
        )}
      </div>
      {summary.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {summary.map((item) => (
            <span
              key={item.key}
              title={item.label}
              className="inline-flex h-7 max-w-full items-center gap-1 rounded-sm border border-primary/60 bg-primary/15 pr-1 pl-2.5 text-xs text-ink"
            >
              <span className="truncate">{item.label}</span>
              <button type="button" aria-label={`Remove ${item.label}`} className="rounded-xs p-0.5 text-ink-subtle hover:text-ink" onClick={() => toggle(item.ids, false)}>
                <XIcon className="size-3.5" />
              </button>
            </span>
          ))}
        </div>
      )}
      <Input
        placeholder="Search categories, subcategories and datasets"
        aria-label="Search categories, subcategories and datasets"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
      />
      <div role="group" aria-label="Datasets by category" className="flex max-h-80 flex-col overflow-y-auto rounded-md border border-hairline py-1">
        {tree.length === 0 ? (
          <p className="px-3 py-2 text-xs text-ink-subtle">No datasets in this market yet. Download it in Sync with BRAIN.</p>
        ) : shown.length === 0 ? (
          <p className="px-3 py-2 text-xs text-ink-subtle">No match.</p>
        ) : (
          shown.map(({ trunk, subs, loose }) => (
            <div key={trunk.key} className="flex flex-col">
              <TreeRow
                depth={0}
                label={trunk.name}
                ids={trunk.ids}
                chosen={chosen}
                count={total(trunk.ids)}
                open={isOpen(trunk.key)}
                onExpand={() => expand(trunk.key)}
                onToggle={(on) => toggle(trunk.ids, on)}
              />
              {isOpen(trunk.key) && (
                <>
                  {subs.map(({ branch, ids }) => (
                    <div key={branch.key} className="flex flex-col">
                      <TreeRow
                        depth={1}
                        label={branch.name}
                        ids={branch.ids}
                        chosen={chosen}
                        count={total(branch.ids)}
                        open={isOpen(branch.key)}
                        onExpand={() => expand(branch.key)}
                        onToggle={(on) => toggle(branch.ids, on)}
                      />
                      {isOpen(branch.key) &&
                        ids.map((id) => <TreeRow key={id} depth={2} label={nameOf(id)} title={id} ids={[id]} chosen={chosen} count={total([id])} onToggle={(on) => toggle([id], on)} />)}
                    </div>
                  ))}
                  {loose.map((id) => (
                    <TreeRow key={id} depth={1} label={nameOf(id)} title={id} ids={[id]} chosen={chosen} count={total([id])} onToggle={(on) => toggle([id], on)} />
                  ))}
                </>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

/** One node: its checkbox is ticked when every dataset under it is, and part-ticked when some are. */
function TreeRow({
  depth,
  label,
  title,
  ids,
  chosen,
  count,
  open,
  onExpand,
  onToggle,
}: {
  depth: 0 | 1 | 2
  label: string
  title?: string
  ids: string[]
  chosen: Set<string>
  count: number | null
  open?: boolean
  onExpand?: () => void
  onToggle: (on: boolean) => void
}) {
  const on = ids.filter((id) => chosen.has(id)).length
  const all = on === ids.length
  const some = on > 0 && !all

  return (
    <div className={cx('flex h-7 shrink-0 items-center gap-1 pr-3 text-xs hover:bg-surface-2', depth === 0 ? 'pl-1.5' : depth === 1 ? 'pl-6' : 'pl-[2.625rem]')}>
      {onExpand ? (
        <button type="button" aria-label={`${open ? 'Collapse' : 'Expand'} ${label}`} aria-expanded={open} onClick={onExpand} className="rounded-xs p-0.5 text-ink-subtle hover:text-ink">
          <ChevronRightIcon className={cx('size-3.5 transition-transform', open && 'rotate-90')} aria-hidden />
        </button>
      ) : (
        <span className="w-4.5 shrink-0" aria-hidden />
      )}
      <label title={title ?? label} className="flex min-w-0 flex-1 cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          className="size-3.5 shrink-0"
          checked={all}
          ref={(el) => {
            if (el) el.indeterminate = some
          }}
          onChange={(e) => onToggle(e.target.checked)}
        />
        <span className={cx('truncate', depth === 0 && 'font-medium', on > 0 ? 'text-ink' : 'text-ink-muted')}>{label}</span>
      </label>
      {count != null && <span className="num text-ink-tertiary">{fmt.int(count)}</span>}
    </div>
  )
}
