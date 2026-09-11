/**
 * Resizable split panes on react-resizable-panels. Wide screens get a draggable hairline
 * gutter that turns lavender on hover, drag or keyboard focus; narrow screens stack as a
 * column. Each layout persists per id in localStorage.
 */

import { Children, useSyncExternalStore, type ReactNode } from 'react'
import { Group, Panel, Separator, useDefaultLayout } from 'react-resizable-panels'
import { cn } from '@/lib/cn'

export const BREAKPOINTS = { lg: '(min-width: 1024px)', xl: '(min-width: 1280px)', '2xl': '(min-width: 1536px)' } as const
export type Breakpoint = keyof typeof BREAKPOINTS

export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (notify) => {
      const list = window.matchMedia(query)
      list.addEventListener('change', notify)
      return () => list.removeEventListener('change', notify)
    },
    () => window.matchMedia(query).matches,
  )
}

/** A number is pixels; a unitless string is a percentage of the group. */
export interface PaneSize {
  default?: number | string
  min?: number | string
  max?: number | string
}

/**
 * The drag handle. `gutter` keeps the 12px canvas gap between panels with a hairline in it;
 * `edge` sits on an existing border (the sidebar) with a wider invisible hit area.
 */
export function ResizeHandle({ variant = 'gutter' }: { variant?: 'gutter' | 'edge' }) {
  const edge = variant === 'edge'
  return (
    <Separator className={cn('group relative flex shrink-0 cursor-col-resize justify-center self-stretch outline-none', edge ? 'z-10 -ml-px w-px' : 'w-3')}>
      <span
        className={cn(
          'h-full w-px transition-colors group-data-[separator=active]:bg-primary group-data-[separator=focus]:bg-primary group-data-[separator=hover]:bg-primary',
          edge ? 'bg-transparent' : 'bg-hairline',
        )}
      />
      {edge && <span aria-hidden className="absolute inset-y-0 -right-1.5 -left-1.5" />}
    </Separator>
  )
}

/** Two panes side by side from `breakpoint` up, stacked below it. Takes exactly two children. */
export function SplitPane({
  id,
  breakpoint = 'lg',
  first,
  second,
  children,
  className,
}: {
  id: string
  breakpoint?: Breakpoint
  first?: PaneSize
  second?: PaneSize
  children: ReactNode
  className?: string
}) {
  const wide = useMediaQuery(BREAKPOINTS[breakpoint])
  const [a, b] = Children.toArray(children)
  if (!wide) return <div className={cn('flex min-w-0 flex-col gap-3', className)}>{a}{b}</div>
  return (
    <Split id={id} first={first} second={second} className={className}>
      {a}
      {b}
    </Split>
  )
}

function Split({ id, first, second, children, className }: { id: string; first?: PaneSize; second?: PaneSize; children: ReactNode; className?: string }) {
  const [a, b] = Children.toArray(children)
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({ id: `ah-panels:${id}`, storage: localStorage, onlySaveAfterUserInteractions: true })
  return (
    <Group id={id} orientation="horizontal" defaultLayout={defaultLayout} onLayoutChanged={onLayoutChanged} className={cn('min-w-0', className)}>
      <Panel id="first" defaultSize={first?.default} minSize={first?.min} maxSize={first?.max} className="min-w-0">
        {a}
      </Panel>
      <ResizeHandle />
      <Panel id="second" defaultSize={second?.default} minSize={second?.min} maxSize={second?.max} className="min-w-0">
        {b}
      </Panel>
    </Group>
  )
}
