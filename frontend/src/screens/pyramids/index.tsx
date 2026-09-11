/**
 * Sync with BRAIN: the sync matrix that downloads every market's Data Fields, above BRAIN's
 * Pyramid Multiplier for every Region · Delay · Dataset Category, with ✓ where 3 or more of your
 * Alphas formulate the pyramid this quarter. The pyramid map is read-only and refreshes itself.
 */

import { useQuery } from '@tanstack/react-query'
import { useNavigate } from '@tanstack/react-router'
import { catalog } from '@/api/catalog'
import { DASH, fmt } from '@/lib/format'
import { useScope } from '@/lib/scope'
import { multiplierHeat } from '@/lib/theme'
import { SyncHero } from '@/screens/data/sync-matrix'
import { ErrorNotice, Page, PageHeader, Panel, Skeleton, cx } from '@/ui/kit'

const REFRESH_MS = 10 * 60 * 1000

/** DESIGN.md brand-secure, the top bar's status tint, deepening with the multiplier (×1.0 → ×2.0). */
const TINT = ['bg-brand-secure/5', 'bg-brand-secure/15', 'bg-brand-secure/25', 'bg-brand-secure/40', 'bg-brand-secure/55', 'bg-brand-secure/70'] as const

const key = (categoryId: string, region: string, delay: number) => `${categoryId}|${region}|${delay}`
const times = (m: number | null) => (m == null ? DASH : `×${fmt.ratio(m, 1)}`)

export function PyramidsScreen() {
  const query = useQuery({ queryKey: ['catalog', 'pyramids'], queryFn: catalog.pyramids, refetchInterval: REFRESH_MS, staleTime: REFRESH_MS / 2 })
  const data = query.data
  const cells = new Map((data?.cells ?? []).map((c) => [key(c.categoryId, c.region, c.delay), c]))
  const navigate = useNavigate()
  const [scope, update] = useScope('data')

  return (
    <Page>
      <PageHeader title="Sync with BRAIN" description="Download every market's Data Fields from BRAIN, and see each Pyramid Multiplier." />
      <SyncHero
        scope={scope}
        onPick={(change) => {
          // Clicking a market opens it in the Data Explorer.
          update(change)
          void navigate({ to: '/data/$tab', params: { tab: 'fields' } })
        }}
      />
      <Panel title="Pyramid Multiplier Map">
        {query.isError ? (
          <ErrorNotice error={query.error} title="Could not load pyramids from BRAIN" />
        ) : !data ? (
          <Skeleton className="h-96" />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[64rem] table-fixed border-separate border-spacing-1 text-xs">
              <thead>
                <tr>
                  <th className="w-32 pr-3 text-left font-medium text-ink-subtle">Category</th>
                  {data.columns.map((column) => (
                    <th key={`${column.region}-${column.delay}`} className="num px-1 font-medium whitespace-nowrap text-ink-subtle">
                      {column.region} D{column.delay}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.categories.map((category) => (
                  <tr key={category.id}>
                    <td className="pr-3 whitespace-nowrap text-ink-muted">{category.name}</td>
                    {data.columns.map((column) => {
                      const id = `${column.region}-${column.delay}`
                      const cell = cells.get(key(category.id, column.region, column.delay))
                      if (!cell) {
                        return (
                          <td key={id} className="num text-center text-ink-tertiary">
                            {DASH}
                          </td>
                        )
                      }
                      return (
                        <td key={id}>
                          <span
                            title={`${category.name} · ${column.region} D${column.delay} · Pyramid Multiplier ${times(cell.multiplier)} · ${fmt.int(cell.alphaCount)} of your Alphas this quarter${cell.lit ? ' · formulated' : ''}`}
                            className={cx(
                              'num flex h-8 items-center justify-center rounded-sm border border-brand-secure/35 whitespace-nowrap text-ink',
                              TINT[multiplierHeat(cell.multiplier)],
                            )}
                          >
                            {times(cell.multiplier)}
                            {cell.lit && ' ✓'}
                          </span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </Page>
  )
}
