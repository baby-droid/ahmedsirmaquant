/** Pieces every Alpha Pool tab uses: check badges, settings, the BRAIN link, re-check. */

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ExternalLinkIcon, RefreshCwIcon } from 'lucide-react'
import { toast } from 'sonner'
import { errorMessage } from '@/api/http'
import { pool, type AlphaSettings } from '@/api/pool'
import type { AlphaCheck } from '@/api/types'
import { fmt, isNum } from '@/lib/format'
import { Badge, Button, checkTone } from '@/ui/kit'
import { Tooltip } from '@/ui/overlay'
import { checkFigure } from './helpers'

export function CheckBadge({ check }: { check: AlphaCheck }) {
  const result = check.result ?? 'PENDING'
  const figures = isNum(check.value) ? `${checkFigure(check.name, check.value)}${isNum(check.limit) ? ` / ${checkFigure(check.name, check.limit)}` : ''}` : null
  return (
    <Tooltip content={`${result}${check.message ? ` · ${check.message}` : figures && isNum(check.limit) ? ' · value / limit' : ''}`}>
      <span>
        <Badge tone={checkTone(check.result ?? 'PENDING')} className="num">
          {check.name}
          {figures && <span className="opacity-70">{figures}</span>}
        </Badge>
      </span>
    </Tooltip>
  )
}

export function SettingsLine({ settings: s }: { settings: AlphaSettings }) {
  const parts = [s.region, s.universe, isNum(s.delay) ? `D${s.delay}` : null, s.neutralization, isNum(s.decay) ? `Decay ${s.decay}` : null, isNum(s.truncation) ? `Trunc ${fmt.ratio(s.truncation)}` : null]
  return <p className="num text-xs text-ink-subtle">{parts.filter(Boolean).join(' · ')}</p>
}

/** The strict submission boundary: submission happens on BRAIN, never here. */
export function OpenInBrain({ url }: { url: string }) {
  return (
    <Button size="sm" render={<a href={url} target="_blank" rel="noopener noreferrer" />}>
      <ExternalLinkIcon aria-hidden />
      Open in BRAIN
    </Button>
  )
}

/** Re-runs BRAIN's submission checks (never submits) and refreshes what depends on them. */
export function RecheckButton({ alphaId }: { alphaId: string }) {
  const queryClient = useQueryClient()
  const mutation = useMutation({
    mutationFn: () => pool.check(alphaId),
    onSuccess: (body) => {
      const checks = body.is?.checks ?? []
      const count = (r: string) => checks.filter((c) => (c.result ?? 'PENDING') === r).length
      toast.success(`Checked ${alphaId}: ${count('PASS')} pass, ${count('FAIL')} fail, ${count('PENDING')} pending`)
      void queryClient.invalidateQueries({ queryKey: ['pool', 'detail', alphaId] })
      void queryClient.invalidateQueries({ queryKey: ['pool', 'submittable'] })
    },
    onError: (e) => toast.error(errorMessage(e)),
  })
  return (
    <Button size="sm" loading={mutation.isPending} onClick={() => mutation.mutate()}>
      {!mutation.isPending && <RefreshCwIcon />}
      Re-check on BRAIN
    </Button>
  )
}
