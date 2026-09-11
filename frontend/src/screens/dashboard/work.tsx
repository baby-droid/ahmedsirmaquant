/**
 * Work in flight: today's plan tracks with their progress, any other task holding queued
 * simulations or cores (work queued by labs), and the background tasks summary.
 */

import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'
import { tasks as tasksApi } from '@/api/core'
import { errorMessage } from '@/api/http'
import { plan } from '@/api/plan'
import { DASH, fmt } from '@/lib/format'
import { useLive } from '@/lib/live'
import { useRefetchOn } from '@/lib/ws'
import { Badge, Button, Empty, ErrorNotice, Panel, Progress, Skeleton } from '@/ui/kit'
import { Confirm } from '@/ui/overlay'
import { DataTable, type Column } from '@/ui/table'

interface WorkRow {
  task: string
  name: string | null
  explains: string
  running: boolean
  fromEarlier: boolean
  done: number | null
  inFlight: number
  waiting: number
  target: number | null
}

export function WorkInFlight() {
  const queryClient = useQueryClient()
  const status = useQuery({ queryKey: ['plan', 'status'], queryFn: () => plan.status() })
  useRefetchOn('simulations', ['plan', 'status'], 2000)

  // `task` undefined = stop everything.
  const [confirm, setConfirm] = useState<{ task?: string; name: string; waiting: number } | null>(null)
  const stop = useMutation({
    mutationFn: (task?: string) => (task ? plan.stop(task) : plan.stopAll()),
    onSuccess: (result) => {
      toast.success(`Dropped ${fmt.int(result.dropped)} queued simulations${result.task ? ` from ${result.task}` : ''}. Anything already sent to BRAIN keeps running.`)
      for (const queryKey of [['plan', 'status'], ['simulations'], ['bar'], ['today']]) void queryClient.invalidateQueries({ queryKey })
    },
    onError: (error) => toast.error(errorMessage(error)),
    onSettled: () => setConfirm(null),
  })

  const data = status.data
  const planTasks = new Set(data?.tracks.map((t) => t.task))
  const rows: WorkRow[] = data
    ? [
        ...data.tracks.map((t) => ({
          task: t.task,
          name: t.name,
          explains: t.explains,
          running: t.status === 'running',
          fromEarlier: t.fromEarlier,
          done: t.done,
          inFlight: t.inFlight,
          waiting: t.waiting,
          target: t.target,
        })),
        ...[...new Set([...Object.keys(data.engine.queued), ...Object.keys(data.engine.inFlight)])]
          .filter((task) => !planTasks.has(task))
          .map((task) => ({
            task,
            name: null,
            explains: 'Queued by a lab',
            running: (data.engine.inFlight[task] ?? 0) > 0,
            fromEarlier: false,
            done: null,
            inFlight: data.engine.inFlight[task] ?? 0,
            waiting: data.engine.queued[task] ?? 0,
            target: null,
          })),
      ]
    : []
  const stoppable = rows.filter((r) => (r.name === null || r.running) && r.waiting > 0)
  const waitingTotal = stoppable.reduce((sum, r) => sum + r.waiting, 0)

  const columns: Column<WorkRow>[] = [
    {
      key: 'task',
      header: 'Task',
      width: 'minmax(220px,3fr)',
      cell: (r) => (
        <div className="flex min-w-0 flex-col py-1 leading-tight">
          <span className="truncate text-ink">
            {r.name ?? <span className="num">{r.task}</span>} {r.name && <span className="num text-xs text-ink-tertiary">{r.task}</span>}
          </span>
          <span className="truncate text-xs text-ink-subtle">{r.explains}</span>
        </div>
      ),
    },
    {
      key: 'status',
      header: 'Status',
      width: '150px',
      cell: (r) => (
        <div className="flex gap-1">
          <Badge tone={r.running ? 'neutral' : 'outline'}>{r.name === null ? (r.running ? 'Running' : 'Queued') : r.running ? 'Running' : 'Stopped'}</Badge>
          {r.fromEarlier && <Badge tone="outline">From earlier</Badge>}
        </div>
      ),
    },
    {
      key: 'progress',
      header: 'Progress',
      width: '100px',
      cell: (r) => (r.target ? <Progress value={(r.done ?? 0) / r.target} className="w-20" label="Done of target" /> : null),
    },
    { key: 'done', header: 'Done', align: 'right', width: '80px', cell: (r) => fmt.int(r.done) },
    { key: 'inFlight', header: 'On BRAIN', align: 'right', width: '90px', cell: (r) => (r.name === null ? `${fmt.int(r.inFlight)} cores` : fmt.int(r.inFlight)) },
    { key: 'waiting', header: 'Waiting', align: 'right', width: '80px', cell: (r) => fmt.int(r.waiting) },
    { key: 'target', header: 'Target', align: 'right', width: '80px', cell: (r) => fmt.int(r.target) },
    {
      key: 'actions',
      header: <span className="sr-only">Actions</span>,
      width: '72px',
      align: 'right',
      cell: (r) =>
        stoppable.includes(r) && (
          <Button variant="ghost" size="sm" disabled={stop.isPending} onClick={() => setConfirm({ task: r.task, name: r.name ?? r.task, waiting: r.waiting })}>
            Stop
          </Button>
        ),
    },
  ]

  return (
    <Panel
      title="Work in Flight"
      description={
        data && data.tracks.length > 0 ? (
          <span className="num">
            {fmt.int(data.done)} done · {fmt.int(data.inFlight)} on BRAIN · {fmt.int(data.waiting)} waiting of {fmt.int(data.target)} · {fmt.int(data.remaining)} not
            yet run
          </span>
        ) : undefined
      }
      actions={
        stoppable.length > 0 && (
          <Button variant="danger" size="sm" disabled={stop.isPending} onClick={() => setConfirm({ name: 'all queued work', waiting: waitingTotal })}>
            Stop all
          </Button>
        )
      }
      bodyClassName="flex flex-col gap-4"
    >
      {status.isPending ? (
        <Skeleton className="h-24" />
      ) : status.isError ? (
        <ErrorNotice error={status.error} title="Today's plan could not load" />
      ) : rows.length === 0 ? (
        <Empty title="Nothing is queued right now" />
      ) : (
        <DataTable label="Work in Flight"rows={rows} columns={columns} rowKey={(r) => r.task} rowHeight={48} maxHeight="24rem" />
      )}

      <TasksSummary />

      <Confirm
        open={confirm !== null}
        onOpenChange={(open) => !open && !stop.isPending && setConfirm(null)}
        title={confirm?.task ? `Stop ${confirm.name}?` : 'Stop all queued work?'}
        confirmLabel="Stop"
        cancelLabel="Keep going"
        danger
        pending={stop.isPending}
        onConfirm={() => stop.mutate(confirm?.task)}
      >
        Drops <span className="num text-ink">{fmt.int(confirm?.waiting)}</span> queued simulations of {confirm?.name}; they will not be sent. Anything already sent
        to BRAIN keeps running.
      </Confirm>
    </Panel>
  )
}

/** Background tasks (syncs, backfills, lab runs) from the socket, with a REST read until it lands. */
function TasksSummary() {
  const live = useLive((s) => s.tasks)
  const fallback = useQuery({ queryKey: ['tasks'], queryFn: () => tasksApi.list(), enabled: live === null })
  const summary = live ?? fallback.data

  if (live === null && fallback.isError) return <ErrorNotice error={fallback.error} title="Background tasks could not load" />
  if (!summary) return null
  const shown = summary.tasks.filter((t) => t.state !== 'done')

  return (
    <div className="flex flex-col gap-2 border-t border-hairline pt-3">
      <p className="text-xs text-ink-subtle">
        Background Tasks: <span className="num text-ink">{fmt.int(summary.running)}</span> running
        {summary.failed > 0 && (
          <>
            {' '}
            · <span className="num text-loss">{fmt.int(summary.failed)}</span> failed
          </>
        )}
      </p>
      {shown.map((t) => (
        <div key={t.id} className="flex flex-col gap-1 text-[13px]">
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 truncate text-ink">{t.label}</span>
            <Badge tone={t.state === 'failed' ? 'loss' : t.state === 'cancelled' ? 'outline' : 'neutral'}>{t.state}</Badge>
            <span className="num text-xs text-ink-subtle">{t.progress == null ? DASH : fmt.pct(t.progress, 0)}</span>
          </div>
          {t.state === 'running' && <Progress value={t.progress} label={t.label} />}
          {(t.error || t.detail) && <span className={t.error ? 'text-xs text-loss' : 'text-xs text-ink-subtle'}>{t.error ?? t.detail}</span>}
        </div>
      ))}
    </div>
  )
}
