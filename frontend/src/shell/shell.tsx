/**
 * The gate and the workspace. `GET /api/today` decides: backend down, sign-in, or work.
 */

import { Suspense, useEffect, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Outlet } from '@tanstack/react-router'
import { Group, Panel, useDefaultLayout } from 'react-resizable-panels'
import { BREAKPOINTS, ResizeHandle, useMediaQuery } from '@/ui/panels'
import { RefreshCwIcon, ServerCrashIcon, XIcon } from 'lucide-react'
import { today } from '@/api/core'
import { errorMessage } from '@/api/http'
import type { Today } from '@/api/types'
import { useLive } from '@/lib/live'
import { useRefetchOn } from '@/lib/ws'
import { Button, Empty, Notice, Skeleton, Spinner } from '@/ui/kit'
import { CommandMenu } from './command-menu'
import { Header } from './header'
import { Sidebar } from './sidebar'
import { SignIn } from './sign-in'

export function Shell() {
  const query = useQuery({ queryKey: ['today'], queryFn: () => today.get() })
  useRefetchOn('session', ['today'])
  useEffect(() => {
    if (!query.isError) return
    const timer = window.setInterval(() => void query.refetch(), 2500)
    return () => window.clearInterval(timer)
  }, [query.isError, query.refetch])

  if (query.isPending) {
    return (
      <div className="flex h-svh items-center justify-center">
        <Spinner className="size-5" />
      </div>
    )
  }

  if (query.isError) {
    return (
      <div className="flex h-svh items-center justify-center p-6">
        <div className="w-full max-w-md rounded-lg border border-hairline bg-surface-1">
          <Empty icon={<ServerCrashIcon />} title="Reconnecting to A-SIRMA QUANT">
            <p>{errorMessage(query.error)}</p>
            <p className="mt-2 text-xs text-ink-tertiary">The page will retry automatically while the service comes back.</p>
          </Empty>
          <div className="flex justify-center border-t border-hairline p-3">
            <Button onClick={() => query.refetch()} loading={query.isFetching}>
              <RefreshCwIcon /> Try again
            </Button>
          </div>
        </div>
      </div>
    )
  }

  if (query.data.step === 'sign-in') return <SignIn storedEmail={query.data.you.email} />
  return <Workspace you={query.data.you} />
}

function Workspace({ you }: { you: Today['you'] }) {
  const wide = useMediaQuery(BREAKPOINTS.lg)
  const main = (
    <div className="flex h-full min-h-0 min-w-0 flex-col">
      <Header />
      <VerificationBanner />
      <main className="min-h-0 flex-1 overflow-auto">
        <Suspense fallback={<ScreenSkeleton />}>
          <Outlet />
        </Suspense>
      </main>
    </div>
  )
  return (
    <>
      {wide ? (
        <ResizableShell you={you}>{main}</ResizableShell>
      ) : (
        <div className="grid h-svh grid-cols-[52px_minmax(0,1fr)]">
          <Sidebar you={you} collapsed />
          {main}
        </div>
      )}
      <CommandMenu />
    </>
  )
}

/** Sidebar | workspace, draggable from `lg` up. Dragged below its minimum it collapses to the icon rail. */
function ResizableShell({ you, children }: { you: Today['you']; children: ReactNode }) {
  const [collapsed, setCollapsed] = useState(false)
  const { defaultLayout, onLayoutChanged } = useDefaultLayout({ id: 'ah-panels:shell', storage: localStorage, onlySaveAfterUserInteractions: true })
  return (
    <Group id="shell" orientation="horizontal" defaultLayout={defaultLayout} onLayoutChanged={onLayoutChanged} className="h-svh">
      <Panel id="sidebar" defaultSize={216} minSize={180} maxSize={320} collapsible collapsedSize={52} onResize={(size) => setCollapsed(size.inPixels <= 64)}>
        <Sidebar you={you} collapsed={collapsed} />
      </Panel>
      <ResizeHandle variant="edge" />
      <Panel id="workspace" minSize={480} className="min-w-0">
        {children}
      </Panel>
    </Group>
  )
}

function VerificationBanner() {
  const url = useLive((s) => s.verificationUrl)
  if (!url) return null
  return (
    <div className="border-b border-hairline p-2">
      <Notice
        tone="warn"
        title="BRAIN needs to verify your identity"
        action={
          <Button variant="ghost" size="icon-sm" aria-label="Dismiss" onClick={() => useLive.setState({ verificationUrl: null })}>
            <XIcon />
          </Button>
        }
      >
        Finish the check in your browser, then sign in again.{' '}
        <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary-hover underline underline-offset-2">
          Open the verification page
        </a>
      </Notice>
    </div>
  )
}

function ScreenSkeleton() {
  return (
    <div className="flex flex-col gap-3 p-4">
      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-80" />
    </div>
  )
}
