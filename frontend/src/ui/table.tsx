/**
 * A virtualised, server-sorted table. Only visible rows mount, so a 500-row page scrolls
 * without layout cost. Sorting and paging are the backend's job (DuckDB); this only asks.
 */

import { useRef, type ReactNode } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowDownIcon, ArrowUpIcon, ChevronLeftIcon, ChevronRightIcon } from 'lucide-react'
import { fmt } from '@/lib/format'
import { Button, Empty, Skeleton, cx } from './kit'

export interface Column<T> {
  key: string
  header: ReactNode
  cell: (row: T) => ReactNode
  /** CSS grid track, e.g. `120px` or `minmax(240px,2fr)`. */
  width?: string
  align?: 'left' | 'right'
  sortable?: boolean
}

export interface Sort {
  key: string
  desc: boolean
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  onRowClick,
  sort,
  onSort,
  selected,
  onSelect,
  loading,
  empty = 'Nothing to show.',
  maxHeight = '70vh',
  rowHeight = 32,
  label,
}: {
  rows: T[]
  columns: Column<T>[]
  rowKey: (row: T) => string
  onRowClick?: (row: T) => void
  sort?: Sort
  onSort?: (sort: Sort) => void
  /** With `onSelect`, adds a checkbox column. */
  selected?: ReadonlySet<string>
  onSelect?: (key: string, on: boolean) => void
  loading?: boolean
  empty?: ReactNode
  maxHeight?: string
  rowHeight?: number
  label: string
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const virtual = useVirtualizer({ count: rows.length, getScrollElement: () => scroller.current, estimateSize: () => rowHeight, overscan: 12 })
  const selectable = selected !== undefined && onSelect !== undefined
  const template = [selectable ? '36px' : null, ...columns.map((c) => c.width ?? 'minmax(96px,1fr)')].filter(Boolean).join(' ')

  const header = (
    <div role="row" className="sticky top-0 z-10 grid border-b border-hairline bg-surface-1" style={{ gridTemplateColumns: template }}>
      {selectable && <div role="columnheader" aria-label="Select" />}
      {columns.map((column) => {
        const active = sort?.key === column.key
        const content = (
          <>
            {column.header}
            {active && (sort.desc ? <ArrowDownIcon className="size-3" /> : <ArrowUpIcon className="size-3" />)}
          </>
        )
        return (
          <div
            key={column.key}
            role="columnheader"
            aria-sort={active ? (sort.desc ? 'descending' : 'ascending') : undefined}
            className={cx('flex h-8 items-center px-3 text-xs font-medium text-ink-subtle', column.align === 'right' && 'justify-end')}
          >
            {column.sortable && onSort ? (
              <button
                type="button"
                className={cx('inline-flex items-center gap-1 hover:text-ink', active && 'text-ink')}
                onClick={() => onSort({ key: column.key, desc: active ? !sort.desc : true })}
              >
                {content}
              </button>
            ) : (
              content
            )}
          </div>
        )
      })}
    </div>
  )

  return (
    <div ref={scroller} role="table" aria-label={label} aria-rowcount={rows.length} className="min-w-0 overflow-auto" style={{ maxHeight }}>
      {/* fit-content: fills the panel, and only scrolls when the columns' minimum widths do not fit. */}
      <div className="w-fit min-w-full">
        {header}
        {loading && rows.length === 0 ? (
          <div className="flex flex-col gap-1 p-2">
            {Array.from({ length: 8 }, (_, i) => (
              <Skeleton key={i} className="h-6" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <Empty title={empty} />
        ) : (
          <div role="rowgroup" className="relative" style={{ height: virtual.getTotalSize() }}>
            {virtual.getVirtualItems().map((item) => {
              const row = rows[item.index]
              const key = rowKey(row)
              const isSelected = selectable && selected.has(key)
              return (
                <div
                  key={key}
                  role="row"
                  aria-rowindex={item.index + 1}
                  aria-selected={selectable ? isSelected : undefined}
                  tabIndex={onRowClick ? 0 : undefined}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  onKeyDown={onRowClick ? (e) => e.key === 'Enter' && onRowClick(row) : undefined}
                  className={cx(
                    'absolute inset-x-0 grid border-b border-hairline/60 text-[13px]',
                    onRowClick && 'cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none',
                    isSelected && 'bg-primary/8',
                  )}
                  style={{ gridTemplateColumns: template, height: rowHeight, transform: `translateY(${item.start}px)` }}
                >
                  {selectable && (
                    <div role="cell" className="flex items-center justify-center" onClick={(e) => e.stopPropagation()}>
                      <input type="checkbox" aria-label="Select row" className="size-3.5" checked={isSelected} onChange={(e) => onSelect(key, e.target.checked)} />
                    </div>
                  )}
                  {columns.map((column) => (
                    <div
                      key={column.key}
                      role="cell"
                      className={cx('flex min-w-0 items-center truncate px-3', column.align === 'right' && 'num justify-end')}
                    >
                      {column.cell(row)}
                    </div>
                  ))}
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

export function Pager({ total, offset, limit, onChange }: { total: number; offset: number; limit: number; onChange: (offset: number) => void }) {
  const end = Math.min(total, offset + limit)
  return (
    <div className="flex items-center justify-end gap-2 text-xs text-ink-subtle">
      <span className="num">
        {total === 0 ? 0 : fmt.int(offset + 1)}–{fmt.int(end)} of {fmt.int(total)}
      </span>
      <Button size="icon-sm" variant="ghost" aria-label="Previous page" disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>
        <ChevronLeftIcon />
      </Button>
      <Button size="icon-sm" variant="ghost" aria-label="Next page" disabled={end >= total} onClick={() => onChange(offset + limit)}>
        <ChevronRightIcon />
      </Button>
    </div>
  )
}
