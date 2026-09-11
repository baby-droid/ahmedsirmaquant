/**
 * "Plan the day": the one primary action. A free draw proposes tracks, the assistant picks
 * tracks and says why (one LLM request), or the PM allocates the cores across labs (one LLM
 * request per ask, previewed with dryRun first). Queueing is always confirmed.
 */

import { useState, type ReactNode } from 'react'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { CalendarClockIcon, ShuffleIcon } from 'lucide-react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { plan, type DispatchResult, type PlanLuckyResponse, type PlanTrack, type StartPlanResponse } from '@/api/plan'
import { scopeLabel, type Scope } from '@/api/types'
import { fmt } from '@/lib/format'
import { Badge, Button, Disclosure, ErrorNotice, Field, Input, Notice, Segmented, Skeleton, Textarea, TEXT_TONE } from '@/ui/kit'
import { Confirm, Sheet } from '@/ui/overlay'
import { DataTable } from '@/ui/table'

type Mode = 'draw' | 'advise' | 'pm'

export function PlanTheDay({ scope, remaining, enabledKeys }: { scope: Scope; remaining: number; enabledKeys: number }) {
  const [open, setOpen] = useState(false)
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)}>
        <CalendarClockIcon />
        Plan the day
      </Button>
      <Sheet
        open={open}
        onOpenChange={setOpen}
        title="Plan the day"
        description={
          <>
            Queue simulations in <span className="num">{scopeLabel(scope)}</span>. Nothing is spent until you confirm.
          </>
        }
      >
        {/* The popup unmounts when closed, so the target starts at what is left today. */}
        <PlanBody scope={scope} remaining={remaining} enabledKeys={enabledKeys} />
      </Sheet>
    </>
  )
}

function PlanBody({ scope, remaining, enabledKeys }: { scope: Scope; remaining: number; enabledKeys: number }) {
  const [text, setText] = useState(String(Math.max(1, remaining)))
  const [mode, setMode] = useState<Mode>(enabledKeys > 0 ? 'pm' : 'draw')
  const [trackCount, setTrackCount] = useState(3)
  const target = Number(text)
  const valid = Number.isInteger(target) && target >= 1 && target <= 50_000
  const needsKey = mode !== 'draw' && enabledKeys === 0

  return (
    <div className="flex flex-col gap-4">
      <Field
        label="Simulations to queue"
        hint={
          valid ? (
            <>
              <span className="num">{fmt.int(remaining)}</span> left today. Unused simulations disappear at midnight US Eastern.
            </>
          ) : (
            <span className="text-loss">Enter a whole number from 1 to 50,000.</span>
          )
        }
      >
        <Input type="number" inputMode="numeric" min={1} max={50_000} value={text} onChange={(e) => setText(e.target.value)} aria-invalid={!valid} className="max-w-40" />
      </Field>

      <Segmented
        label="How to plan"
        value={mode}
        onChange={setMode}
        items={[
          { value: 'pm', label: 'Ask the PM' },
          { value: 'advise', label: 'Ask the assistant' },
          { value: 'draw', label: 'Draw without the assistant' },
        ]}
      />

      {mode !== 'pm' && (
        <Disclosure summary="Advanced">
          <Field label="Research tracks" hint="How many lines of research the day is split across.">
            <Segmented label="Research tracks" value={trackCount} onChange={setTrackCount} items={[1, 2, 3, 4].map((n) => ({ value: n, label: String(n) }))} />
          </Field>
        </Disclosure>
      )}

      {needsKey && (
        <Notice title="This needs an enabled assistant Key">
          <Link to="/ai/$tab" params={{ tab: 'keys' }} className="text-primary-hover underline underline-offset-2">
            Add a free Key
          </Link>{' '}
          or draw a plan without the assistant.
        </Notice>
      )}

      {mode === 'pm' && <PmPlan scope={scope} target={target} valid={valid && !needsKey} />}
      {mode === 'advise' && <AdvisePlan scope={scope} target={target} valid={valid && !needsKey} trackCount={trackCount} />}
      {mode === 'draw' && <DrawPlan scope={scope} target={target} valid={valid} trackCount={trackCount} />}
    </div>
  )
}

function useInvalidateQueued() {
  const queryClient = useQueryClient()
  return () => {
    for (const queryKey of [['bar'], ['today'], ['simulations'], ['plan', 'status'], ['plan', 'yield']]) void queryClient.invalidateQueries({ queryKey })
  }
}

const Explainer = ({ children }: { children: ReactNode }) => <p className="text-xs text-ink-subtle">{children}</p>

// ── PM: allocate cores across labs ─────────────────────────────────────────────────────

function PmPlan({ scope, target, valid }: { scope: Scope; target: number; valid: boolean }) {
  const invalidate = useInvalidateQueued()
  const [confirming, setConfirming] = useState(false)
  const body = { ...scope, target, days: 14 }

  const preview = useMutation({ mutationFn: () => plan.lucky({ ...body, dryRun: true }) })
  const run = useMutation({
    mutationFn: () => plan.lucky({ ...body, dryRun: false }),
    onSuccess: (result) => {
      invalidate()
      const started = result.started
      if (!started) return void toast.error('The PM funded no lab, so nothing was queued.')
      toast.success(
        `Queued ${fmt.int(started.queued)} simulations across ${started.desksStarted} of ${started.desksAllocated} labs` +
          (started.skipped ? ` · ${fmt.int(started.skipped)} skipped` : '') +
          '.',
      )
    },
    onError: (error) => toast.error(errorMessage(error)),
    onSettled: () => setConfirming(false),
  })

  const shown = run.data ?? preview.data
  const busy = preview.isPending || run.isPending

  return (
    <>
      <Explainer>
        The PM reads each lab's yield over the last 14 days and splits the cores across labs. Asking uses one LLM request from your assistant budget and queues
        nothing.
      </Explainer>
      <div className="flex flex-wrap gap-2">
        <Button disabled={busy || !valid} loading={preview.isPending} onClick={() => preview.mutate()}>
          {preview.data ? 'Ask again' : 'Ask the PM'}
        </Button>
        {preview.data && preview.data.allocations.length > 0 && !run.data && (
          <Button variant="primary" disabled={busy || !valid} onClick={() => setConfirming(true)}>
            Queue {fmt.int(target)} simulations
          </Button>
        )}
      </div>
      {preview.isError && <ErrorNotice error={preview.error} title="The PM could not be asked" />}
      {run.isError && <ErrorNotice error={run.error} title="Nothing was queued" />}
      {run.data?.started && <Dispatched result={run.data.started} />}
      {preview.isPending && <Skeleton className="h-32" />}
      {shown && <Allocation result={shown} />}

      <Confirm
        open={confirming}
        onOpenChange={(open) => !run.isPending && setConfirming(open)}
        title={`Queue ${fmt.int(target)} simulations?`}
        confirmLabel="Queue and spend"
        cancelLabel="Not yet"
        pending={run.isPending}
        onConfirm={() => run.mutate()}
      >
        Queues <span className="num text-ink">{fmt.int(target)}</span> simulations in <span className="num">{scopeLabel(scope)}</span>, spending BRAIN quota. The
        PM is asked again (one more LLM request), so the allocation it starts may differ from this preview.
      </Confirm>
    </>
  )
}

function Allocation({ result }: { result: PlanLuckyResponse }) {
  const { fund } = result
  return (
    <div className="flex flex-col gap-3">
      {!result.fromPM && (
        <Notice tone="warn" title="Fallback allocation">
          The PM did not give a usable answer, so the cores were split by the fund's own rules instead.
        </Notice>
      )}
      {result.briefing && <p className="text-[13px] whitespace-pre-line text-ink-muted">{result.briefing}</p>}
      {result.allocations.length === 0 ? (
        <Notice tone="error" title="No lab was funded">
          Nothing can be queued from this answer.
        </Notice>
      ) : (
        <ul className="flex flex-col divide-y divide-hairline rounded-md border border-hairline">
          {result.allocations.map((a) => (
            <li key={a.lab} className="flex gap-4 px-3 py-2 text-[13px]">
              <div className="flex w-40 shrink-0 flex-col">
                <span className="text-ink">{a.name}</span>
                <span className="num text-xs text-ink-subtle">{a.lab}</span>
              </div>
              <span className="num w-16 shrink-0 text-right text-ink">{fmt.int(a.cores)} cores</span>
              <span className="min-w-0 text-ink-subtle">{a.why}</span>
            </li>
          ))}
        </ul>
      )}
      {result.rejected && result.rejected.length > 0 && (
        <Notice tone="warn" title="Rejected labs">
          The PM named labs that do not exist or cannot run: <span className="num">{result.rejected.join(', ')}</span>
        </Notice>
      )}
      <p className="text-xs text-ink-subtle">
        {fund.plainly} Yield rate{' '}
        <span className={`num ${fund.finished > 0 ? TEXT_TONE[fund.meetsTarget ? 'profit' : 'loss'] : ''}`}>{fmt.pct(fund.yieldRate, 3)}</span> against a{' '}
        <span className="num">{fmt.pct(fund.targetYield, 2)}</span> target over <span className="num">{fmt.int(fund.finished)}</span> finished simulations.
      </p>
      {result.model && <ModelLine model={result.model} tokens={result.usage?.totalTokens} />}
    </div>
  )
}

const ModelLine = ({ model, tokens }: { model: string; tokens?: number }) => (
  <p className="num text-xs text-ink-tertiary">
    {model}
    {tokens != null ? ` · ${fmt.int(tokens)} tokens` : ''}
  </p>
)

function Dispatched({ result }: { result: DispatchResult }) {
  const notes = result.started.filter((d) => d.note || !d.started || d.short)
  return (
    <div className="flex flex-col gap-2">
      <h3 className="title">Queued</h3>
      <p className="num text-xs text-ink-subtle">
        {fmt.int(result.queued)} queued · {fmt.int(result.skipped)} skipped · {result.desksStarted}/{result.desksAllocated} labs started · {result.toppedUp} topped up
      </p>
      {result.short && (
        <Notice tone="warn" title="Fewer simulations than asked">
          <ul className="flex flex-col gap-0.5">
            {notes.map((d) => (
              <li key={`${d.task}-${d.topUp ? 'top-up' : 'desk'}`}>
                <span className="num">{d.name ?? d.lab}</span>: {d.note ?? (d.started ? 'short of its share' : 'not started')}
              </li>
            ))}
          </ul>
        </Notice>
      )}
      <DataTable
        label="Queued labs"
        rows={result.started}
        rowKey={(d) => `${d.task}-${d.topUp ? 'top-up' : 'desk'}`}
        maxHeight="20rem"
        columns={[
          { key: 'lab', header: 'Lab', width: 'minmax(120px,1fr)', cell: (d) => d.name ?? <span className="num">{d.lab}</span> },
          { key: 'task', header: 'Task', width: 'minmax(140px,1fr)', cell: (d) => <span className="num truncate">{d.task}</span> },
          { key: 'cores', header: 'Cores', align: 'right', width: '64px', cell: (d) => fmt.int(d.cores) },
          { key: 'queued', header: 'Queued', align: 'right', width: '96px', cell: (d) => `${fmt.int(d.queued)}${d.asked !== undefined ? `/${fmt.int(d.asked)}` : ''}` },
          { key: 'skipped', header: 'Skipped', align: 'right', width: '72px', cell: (d) => fmt.int(d.skipped ?? 0) },
          {
            key: 'note',
            header: 'Note',
            width: '140px',
            cell: (d) => (
              <div className="flex gap-1">
                {!d.started && <Badge tone="loss">Not started</Badge>}
                {d.short && <Badge tone="warn">Short</Badge>}
                {d.topUp && <Badge tone="outline">Top-up</Badge>}
              </div>
            ),
          },
        ]}
      />
    </div>
  )
}

// ── Tracks: drawn for free, or chosen by the assistant ─────────────────────────────────

function DrawPlan({ scope, target, valid, trackCount }: { scope: Scope; target: number; valid: boolean; trackCount: number }) {
  const [draw, setDraw] = useState(0)
  const suggestion = useQuery({
    queryKey: ['plan', 'suggest', target, trackCount, draw],
    queryFn: () => plan.suggest({ tracks: trackCount, target, neutralization: 'SUBINDUSTRY' }),
    enabled: valid,
    staleTime: Infinity,
    placeholderData: keepPreviousData,
  })
  const tracks = suggestion.data?.tracks ?? []
  const stale = suggestion.isFetching || suggestion.isPlaceholderData

  return (
    <>
      <Explainer>
        Draws {trackCount} research {trackCount === 1 ? 'track' : 'tracks'} at random from the lever space. Drawing is free: nothing is sent to BRAIN and no LLM
        request is used.
      </Explainer>
      <StartTracks
        scope={scope}
        target={target}
        tracks={tracks}
        disabled={!valid || stale}
        extra={
          <Button disabled={!valid || suggestion.isFetching} loading={suggestion.isFetching} onClick={() => setDraw((d) => d + 1)}>
            {!suggestion.isFetching && <ShuffleIcon />}
            Draw again
          </Button>
        }
      >
        {suggestion.isError && <ErrorNotice error={suggestion.error} title="No plan could be drawn" />}
        {valid && suggestion.isPending ? <Skeleton className="h-40" /> : <TrackList tracks={tracks} />}
      </StartTracks>
    </>
  )
}

function AdvisePlan({ scope, target, valid, trackCount }: { scope: Scope; target: number; valid: boolean; trackCount: number }) {
  const [note, setNote] = useState('')
  const advice = useMutation({ mutationFn: () => plan.advise({ ...scope, target, tracks: trackCount, note, neutralization: 'SUBINDUSTRY' }) })
  const data = advice.data

  return (
    <>
      <Explainer>The assistant picks the day's research tracks from the same levers and says why each is worth running. Asking uses one LLM request and queues nothing.</Explainer>
      <Field label="What you want out of today (optional)">
        <Textarea value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder="Anything, in your own words." />
      </Field>
      <StartTracks
        scope={scope}
        target={target}
        tracks={data?.tracks ?? []}
        disabled={!valid || advice.isPending}
        extra={
          <Button disabled={!valid || advice.isPending} loading={advice.isPending} onClick={() => advice.mutate()}>
            {data ? 'Ask again' : 'Ask the assistant'}
          </Button>
        }
      >
        {advice.isError && <ErrorNotice error={advice.error} title="The assistant could not be asked" />}
        {advice.isPending && <Skeleton className="h-40" />}
        {data && (
          <>
            {!data.fromAssistant && (
              <Notice tone="warn" title="Drawn plan instead">
                The assistant's answer was unusable, so an ordinary drawn plan stands in.
              </Notice>
            )}
            {data.rejected > 0 && (
              <Notice tone="warn">
                <span className="num">{fmt.int(data.rejected)}</span> proposed {data.rejected === 1 ? 'track was' : 'tracks were'} thrown away: invented levers or
                duplicates.
              </Notice>
            )}
            {data.opening && <p className="text-[13px] text-ink-muted">{data.opening}</p>}
            <TrackList tracks={data.tracks} />
            {data.model && <ModelLine model={data.model} tokens={data.usage?.totalTokens} />}
          </>
        )}
      </StartTracks>
    </>
  )
}

function TrackList({ tracks }: { tracks: PlanTrack[] }) {
  if (tracks.length === 0) return null
  return (
    <ul className="flex flex-col divide-y divide-hairline rounded-md border border-hairline">
      {tracks.map((track) => (
        <li key={track.task} className="flex gap-4 px-3 py-2.5 text-[13px]">
          <div className="flex min-w-0 flex-1 flex-col gap-0.5">
            <span className="font-medium text-ink">{track.name}</span>
            <span className="text-ink-subtle">{track.explains}</span>
            {track.why && <span className="text-ink-muted">{track.why}</span>}
            <span className="num text-xs text-ink-tertiary">
              {track.levers.speed} · {track.levers.shape} · {track.levers.crowding} · {track.levers.compared} · depth {track.levers.depth}
            </span>
          </div>
          <div className="num flex shrink-0 flex-col items-end text-xs text-ink-subtle">
            <span className="text-[13px] text-ink">{fmt.int(track.target)}</span>
            <span>{fmt.int(track.cores)} cores</span>
          </div>
        </li>
      ))}
    </ul>
  )
}

/** The confirmed start shared by drawn and advised tracks. `replace` stops today's plan first. */
function StartTracks({
  scope,
  target,
  tracks,
  disabled,
  extra,
  children,
}: {
  scope: Scope
  target: number
  tracks: PlanTrack[]
  disabled: boolean
  extra: ReactNode
  children: ReactNode
}) {
  const invalidate = useInvalidateQueued()
  const [confirming, setConfirming] = useState(false)
  const start = useMutation({
    mutationFn: () => plan.start({ ...scope, tracks, target, replace: true }),
    onSuccess: (result) => {
      invalidate()
      toast.success(`Queued ${fmt.int(result.queued)} simulations across ${result.tracks.length} tracks${result.skipped ? ` · ${fmt.int(result.skipped)} skipped` : ''}.`)
    },
    onError: (error) => toast.error(errorMessage(error)),
    onSettled: () => setConfirming(false),
  })
  const cost = tracks.reduce((sum, t) => sum + t.target, 0)

  return (
    <>
      <div className="flex flex-wrap gap-2">
        {extra}
        <Button variant="primary" disabled={disabled || tracks.length === 0 || start.isPending} onClick={() => setConfirming(true)}>
          Queue {fmt.int(cost || target)} simulations
        </Button>
      </div>
      {start.isError && <ErrorNotice error={start.error} title="Nothing was queued" />}
      {start.data && <Started result={start.data} />}
      {children}
      <Confirm
        open={confirming}
        onOpenChange={(open) => !start.isPending && setConfirming(open)}
        title={`Queue ${fmt.int(cost)} simulations?`}
        confirmLabel="Queue and spend"
        cancelLabel="Not yet"
        pending={start.isPending}
        onConfirm={() => start.mutate()}
      >
        Queues <span className="num text-ink">{fmt.int(cost)}</span> simulations in <span className="num">{scopeLabel(scope)}</span> across {tracks.length}{' '}
        {tracks.length === 1 ? 'track' : 'tracks'}, spending BRAIN quota. It first stops today's current plan: its queued simulations are dropped, while anything
        already sent to BRAIN keeps running.
      </Confirm>
    </>
  )
}

function Started({ result }: { result: StartPlanResponse }) {
  const problems = result.tracks.filter((t) => t.empty || t.short)
  return (
    <div className="flex flex-col gap-2">
      <h3 className="title">Queued</h3>
      <p className="num text-xs text-ink-subtle">
        {fmt.int(result.queued)} queued · {fmt.int(result.skipped)} skipped · {result.day}
      </p>
      {problems.length > 0 && (
        <Notice tone="warn" title="Some tracks fell short">
          <ul className="flex flex-col gap-0.5">
            {problems.map((t) => (
              <li key={t.task}>
                {t.name}: {t.empty ? 'no usable data fields in this scope.' : `queued ${fmt.int(t.queued)} of ${fmt.int(t.target)}.`}
              </li>
            ))}
          </ul>
        </Notice>
      )}
      <DataTable
        label="Queued tracks"
        rows={result.tracks}
        rowKey={(t) => t.task}
        maxHeight="16rem"
        columns={[
          { key: 'name', header: 'Track', width: 'minmax(160px,2fr)', cell: (t) => t.name },
          { key: 'queued', header: 'Queued', align: 'right', width: '110px', cell: (t) => `${fmt.int(t.queued)}/${fmt.int(t.target)}` },
          { key: 'skipped', header: 'Skipped', align: 'right', width: '72px', cell: (t) => fmt.int(t.skipped) },
          { key: 'fields', header: 'Fields', align: 'right', width: '72px', cell: (t) => fmt.int(t.empty ? 0 : t.fields) },
          {
            key: 'note',
            header: 'Note',
            width: '150px',
            cell: (t) => (t.empty ? <Badge tone="loss">No usable fields</Badge> : t.short ? <Badge tone="warn">Short of target</Badge> : null),
          },
        ]}
      />
    </div>
  )
}
