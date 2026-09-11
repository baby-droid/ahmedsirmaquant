/**
 * Dashboard (spec §4.1): the day in one sentence, the Submittable Alphas counter, today's
 * allowance and queue, and the work in flight. The live matrix has its own screen.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { today } from '@/api/core'
import { http } from '@/api/http'
import { fmt } from '@/lib/format'
import { useRefetchOn } from '@/lib/ws'
import { ErrorNotice, Page, Panel, Skeleton, TEXT_TONE, cx, type Tone } from '@/ui/kit'
import { GettingStarted } from './getting-started'
import { WorkInFlight } from './work'

const LINK = 'text-primary-hover underline underline-offset-2'

export function DashboardScreen() {
  const day = useQuery({ queryKey: ['today'], queryFn: () => today.get() })
  useRefetchOn('simulations', ['today'], 10_000)
  const sims = day.data?.simulations

  const submittable = useQuery({
    queryKey: ['pool', 'submittable-count'],
    queryFn: () => http.get<{ total: number }>('/api/vault/submittable?limit=1'),
  })
  useRefetchOn('simulations', ['pool', 'submittable-count'], 5000)
  const total = submittable.data?.total

  return (
    <Page>
      {sims ? <Hero name={day.data?.you.fullName} text={sims.headline} /> : !day.isError && <Skeleton className="mx-1 mt-1 h-9 w-2/3" />}
      <GettingStarted today={day.data} />
      {day.isError && <ErrorNotice error={day.error} title="Today's figures could not load" />}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatTile
          label="Submittable Alphas"
          action={
            <Link to="/pool/$tab" params={{ tab: 'submittable' }} className={cx(LINK, 'text-xs')}>
              Open
            </Link>
          }
          loading={submittable.isPending}
          value={submittable.isError ? '—' : fmt.int(total)}
          tone={total ? 'profit' : 'neutral'}
          hint={submittable.isError ? 'The count could not load.' : 'Passing every submission check'}
        />
        <StatTile
          label="Simulations Left Today"
          loading={!sims}
          value={
            sims && (
              <>
                {sims.exact ? '' : '~'}
                {fmt.int(sims.remaining)}
                <span className="text-base text-ink-tertiary"> / {fmt.int(sims.limit)}</span>
              </>
            )
          }
          hint={
            sims && (
              <>
                Resets in <ResetCountdown seconds={sims.resetsInSeconds} since={day.dataUpdatedAt} />
                {sims.exact ? '' : ' · estimate until the first result today'}
              </>
            )
          }
        />
        <StatTile
          label="Sent Today"
          loading={!sims}
          value={sims && fmt.int(sims.used)}
          hint={
            sims && (
              <>
                <span className="num">{fmt.pct(sims.limit ? sims.used / sims.limit : null, 0)}</span> of the allowance
              </>
            )
          }
        />
        <StatTile
          label="Queued"
          loading={!sims}
          value={sims && fmt.int(sims.queued)}
          hint={
            sims && (
              <>
                <span className="num">
                  {fmt.int(sims.engine.slotsUsed)}/{fmt.int(sims.engine.slots)}
                </span>{' '}
                cores busy
              </>
            )
          }
        />
      </div>

      <WorkInFlight />
    </Page>
  )
}

/**
 * The day in one sentence, as the page's heading, greeted by the account's name from
 * `/api/today` (BRAIN profile, falling back to the email). Figures keep tabular digits.
 */
function Hero({ name, text }: { name?: string | null; text: string }) {
  const parts = text.split(/(~?\d[\d,.]*%?)/)
  return (
    <h1 className="headline px-1 pt-1 text-balance">
      {name && <span className="block text-ink-muted">Welcome, {name}</span>}
      {parts.map((part, i) =>
        i % 2 === 1 ? (
          <span key={i} className="tracking-normal tabular-nums">
            {part}
          </span>
        ) : (
          part
        ),
      )}
    </h1>
  )
}

/** One layout for every tile: label (and action) on top, the figure, then a hint. */
function StatTile({
  label,
  action,
  value,
  hint,
  tone = 'neutral',
  loading,
}: {
  label: string
  action?: ReactNode
  value: ReactNode
  hint?: ReactNode
  tone?: Tone
  loading: boolean
}) {
  return (
    <Panel bodyClassName="flex h-full flex-col gap-3">
      <div className="flex min-h-5 items-baseline justify-between gap-2">
        <span className="text-[13px] text-ink-muted">{label}</span>
        {action}
      </div>
      {loading ? (
        <Skeleton className="h-9 w-28" />
      ) : (
        <span className={cx('num text-3xl leading-none', TEXT_TONE[tone])}>{value}</span>
      )}
      {!loading && hint && <span className="text-xs text-ink-subtle">{hint}</span>}
    </Panel>
  )
}

/** Ticks locally between refetches, in its own component so the page does not re-render. */
function ResetCountdown({ seconds, since }: { seconds: number; since: number }) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return <span className="num">{fmt.countdown(seconds - Math.max(0, (now - since) / 1000))}</span>
}
