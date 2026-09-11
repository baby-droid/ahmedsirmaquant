/**
 * The bar above every screen: the clocks a consultant runs against (session, simulations
 * left, queued with the matrix on hover, quota reset), connection trouble, and ⌘K search.
 */

import { useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { SearchIcon } from 'lucide-react'
import { simulations, today } from '@/api/core'
import type { CellState } from '@/lib/matrix'
import { fmt } from '@/lib/format'
import { useCores, useLive } from '@/lib/live'
import { useRefetchOn } from '@/lib/ws'
import { Button, cx } from '@/ui/kit'
import { Tooltip } from '@/ui/overlay'
import { useCommandMenu } from './command-menu'

export function Header() {
  const openMenu = useCommandMenu((s) => s.setOpen)

  // No breadcrumb: every screen already names itself in its PageHeader, and the sidebar marks where you are.
  return (
    <header className="flex h-12 shrink-0 items-center gap-3 border-b border-hairline bg-canvas px-4">
      <div className="ml-auto flex items-center gap-4">
        <Clocks />
        <ConnectionNotice />
        <Button size="icon-sm" aria-label="Go to a screen or lab (⌘K)" title="Go to a screen or lab (⌘K)" onClick={() => openMenu(true)}>
          <SearchIcon />
        </Button>
      </div>
    </header>
  )
}

/**
 * Silent while live updates flow. Only after the socket has been down for 2s does it say so,
 * so the normal connect on page load never flashes it.
 */
function ConnectionNotice() {
  const connected = useLive((s) => s.connected)
  const [lost, setLost] = useState(false)
  useEffect(() => {
    if (connected) {
      setLost(false)
      return
    }
    const timer = setTimeout(() => setLost(true), 2000)
    return () => clearTimeout(timer)
  }, [connected])

  if (!lost) return null
  return (
    <Tooltip content="Live updates are paused: the connection to the backend dropped. The matrix and counts resume when it reconnects.">
      <span role="status" className="flex h-7 items-center gap-1.5 rounded-md border border-warn/40 bg-warn/10 px-2.5 text-xs whitespace-nowrap text-warn">
        <span className="size-1.5 animate-pulse rounded-full bg-warn" aria-hidden />
        Reconnecting…
      </span>
    </Tooltip>
  )
}

/** Seconds since the last fetch, so countdowns tick between polls. */
function useElapsed(since: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return Math.max(0, Math.floor((now - since) / 1000))
}

function Clocks() {
  const bar = useQuery({ queryKey: ['bar'], queryFn: () => today.bar(), refetchInterval: 30_000 })
  useRefetchOn('simulations', ['bar'], 3000)
  useRefetchOn('session', ['bar'])
  const elapsed = useElapsed(bar.dataUpdatedAt)
  if (!bar.data) return null

  const session = bar.data.expiresInSeconds == null ? null : Math.max(0, bar.data.expiresInSeconds - elapsed)
  // `exact` flips true once today's first simulation POST returns BRAIN's own quota headers.
  const { remaining, exact, queued } = bar.data.simulations

  return (
    <div className="hidden items-center gap-2 text-xs text-ink-subtle lg:flex">
      <Clock label="BRAIN Session" hint="BRAIN Session Time To Live">
        <span className={cx('num', session !== null && session < 1800 ? 'text-warn' : 'text-ink')}>{fmt.countdown(session)}</span>
      </Clock>
      <Clock label="Simulations Left Today" valueFirst>
        <span className="num text-ink">
          {exact ? '' : '~'}
          {fmt.int(remaining)}
        </span>
      </Clock>
      <Tooltip content={<MiniMatrix />}>
        <Link to="/matrix" aria-label={`${fmt.int(queued)} queued. Open the Simulation Matrix`} className={cx(BOX, 'transition-colors hover:border-brand-secure/70')}>
          <span className="num text-ink">{fmt.int(queued)}</span>
          Queued
        </Link>
      </Tooltip>
      <Clock label="Simulation Quota Reset in">
        <span className="num text-ink">{fmt.countdown(Math.max(0, bar.data.resetsInSeconds - elapsed))}</span>
      </Clock>
    </div>
  )
}

/** The header's status boxes: DESIGN.md brand-secure, a muted brand tint for non-interactive status chrome. */
const BOX = 'flex h-7 items-center gap-1.5 whitespace-nowrap rounded-md border border-brand-secure/35 bg-brand-secure/10 px-2.5 text-brand-secure'

function Clock({ label, hint, valueFirst, children }: { label: string; hint?: string; valueFirst?: boolean; children: ReactNode }) {
  const body = (
    <span className={BOX}>
      {valueFirst ? children : label}
      {valueFirst ? label : children}
    </span>
  )
  return hint ? <Tooltip content={hint}>{body}</Tooltip> : body
}

const CELL: Record<CellState, string> = {
  RUNNING: 'bg-primary',
  PENDING: 'bg-ink-tertiary',
  EMPTY: 'border border-hairline-strong',
}

/**
 * The 8 cores × 10-Alpha batches at a glance, on hover of "queued". Mounts only while the
 * tooltip is open, and reads the same live snapshot as the Dashboard matrix.
 */
function MiniMatrix() {
  const live = useLive((s) => s.simulations)
  const active = useQuery({ queryKey: ['simulations', 'active'], queryFn: () => simulations.active(), enabled: live === null })
  const engine = useQuery({ queryKey: ['simulations', 'engine'], queryFn: () => simulations.engine() })
  const slots = engine.data?.slots ?? 8
  const maxBatch = engine.data?.maxBatch ?? 10
  const { cores } = useCores(live ?? active.data, slots, maxBatch)

  return (
    // 16px cells with 6px gaps: about 214×170px for 8×10, legible at a glance without covering the page.
    <div className="flex items-stretch gap-2 p-1">
      {/* Y-axis label, read bottom-to-top like a chart axis. */}
      <span className="flex rotate-180 items-center justify-center text-[11px] font-medium whitespace-nowrap text-ink-subtle [writing-mode:vertical-rl]">
        {slots} Cores
      </span>
      <div role="img" aria-label={`Simulation matrix, ${slots} cores`} className="flex flex-col gap-1.5">
        {cores.map((core, i) => (
          <div key={i} className="flex gap-1.5">
            {core.cells.map((cell, j) => (
              <span key={j} className={cx('size-4 rounded-[3px]', CELL[cell.state])} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
