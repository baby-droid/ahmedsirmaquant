import { useEffect, useMemo, useState } from 'react'
import { Link } from '@tanstack/react-router'
import {
  ArrowRightIcon,
  BrainCircuitIcon,
  CheckCircle2Icon,
  CircleDotIcon,
  DatabaseIcon,
  GaugeIcon,
  GitBranchIcon,
  ListChecksIcon,
  NetworkIcon,
  Settings2Icon,
  SparklesIcon,
  WorkflowIcon,
} from 'lucide-react'
import { toast } from 'sonner'
import { Badge, Button, Checkbox, Field, Input, Metric, Page, PageHeader, Panel, Textarea } from '@/ui/kit'

const SYNC_KEY = 'quant-ai-sync-status'
const SETTINGS_KEY = 'quant-ai-settings'

type Settings = {
  region: string
  category: string
  datasets: string
  fields: string
  objective: string
}

const defaultSettings: Settings = {
  region: 'USA',
  category: 'Fundamental',
  datasets: 'fundamental6',
  fields: 'quality, value, momentum',
  objective: 'Robust Sharpe with controlled turnover',
}

const agents = [
  ['KIMI', 'Request decomposition and operator discovery', 'READY'],
  ['CLAUDE', 'Research review and expression reasoning', 'READY'],
  ['DEEPSEEK', 'Power Pool and alpha ranking', 'PRIORITY'],
  ['CHATGPT', 'Synthesis, explanation, and report card', 'READY'],
] as const

function useFlowState() {
  const [synced, setSynced] = useState(false)
  const [settings, setSettings] = useState<Settings>(defaultSettings)

  useEffect(() => {
    setSynced(window.localStorage.getItem(SYNC_KEY) === 'ready')
    try {
      const saved = window.localStorage.getItem(SETTINGS_KEY)
      if (saved) setSettings({ ...defaultSettings, ...(JSON.parse(saved) as Partial<Settings>) })
    } catch {
      window.localStorage.removeItem(SETTINGS_KEY)
    }
  }, [])

  const saveSettings = (next: Settings) => {
    setSettings(next)
    window.localStorage.setItem(SETTINGS_KEY, JSON.stringify(next))
  }

  return { synced, setSynced, settings, saveSettings }
}

export function ConnectionScreen() {
  const { synced, setSynced } = useFlowState()
  const [connected, setConnected] = useState<string[]>(['operators', 'datasets', 'fields'])
  const sources = JSON.parse(window.localStorage.getItem('quant-ai-store-booth-1') ?? '[]') as unknown[]

  const sync = () => {
    window.localStorage.setItem(SYNC_KEY, 'ready')
    setSynced(true)
    toast.success('STORE BOOTH 1 synced with QUANT AI')
  }

  return (
    <Page>
      <PageHeader title={<span className="flex items-center gap-2"><NetworkIcon className="size-5 text-[#c084fc]" /> Data connection</span>} description="Connect source memory to the BRAIN catalog before the agents begin research." actions={<Badge tone={synced ? 'profit' : 'warn'}>{synced ? 'SYNCED' : 'WAITING FOR SYNC'}</Badge>} />
      <Panel title="Store Booth handoff" description="The first step keeps local source memory separate from the research catalog.">
        <div className="flex flex-wrap items-center gap-3">
          <div className="flex items-center gap-2 rounded-md border border-hairline bg-surface-2 px-3 py-2 text-sm"><DatabaseIcon className="size-4 text-[#c084fc]" /> STORE BOOTH 1 <Badge tone={sources.length ? 'profit' : 'muted'}>{sources.length} sources</Badge></div>
          <ArrowRightIcon className="size-4 text-ink-tertiary" />
          <div className="flex items-center gap-2 rounded-md border border-hairline bg-surface-2 px-3 py-2 text-sm"><NetworkIcon className="size-4 text-primary-hover" /> Connection layer</div>
          <Button variant="primary" onClick={sync}>Sync booth 1</Button>
        </div>
      </Panel>
      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="Available BRAIN sources" description="Select which catalog areas the agents may use.">
          <div className="grid gap-2">
            {[
              ['operators', 'Operators', 'Expressions and operator rules'],
              ['datasets', 'Datasets', 'Dataset metadata and coverage'],
              ['fields', 'Data fields', 'Field definitions and availability'],
              ['settings', 'Simulation settings', 'Regions, universes, neutralization, and decay'],
            ].map(([id, label, detail]) => (
              <Checkbox key={id} label={<span><span className="font-medium text-ink">{label}</span><span className="ml-2 text-xs text-ink-subtle">{detail}</span></span>} checked={connected.includes(id)} onChange={(event) => setConnected((current) => event.target.checked ? [...current, id] : current.filter((item) => item !== id))} />
            ))}
          </div>
        </Panel>
        <Panel title="Next handoff" description="Once connected, send the request to the agent command center.">
          <div className="rounded-md border border-hairline bg-surface-2 p-4">
            <p className="text-sm font-medium text-ink">{synced ? 'Connection is ready for review' : 'Sync the booth to unlock review'}</p>
            <p className="mt-1 text-xs leading-5 text-ink-subtle">The command center labels every agent response, checks understanding, and routes accepted work to monitoring.</p>
            <Button className="mt-4" render={<Link to="/quant-ai/command-center" />} disabled={!synced}>Open command center <ArrowRightIcon /></Button>
          </div>
        </Panel>
      </div>
    </Page>
  )
}

export function CommandCenterScreen() {
  const { synced, settings } = useFlowState()
  const [request, setRequest] = useState('Find strong, explainable alphas for the selected region and category.')
  const [submitted, setSubmitted] = useState(false)
  return (
    <Page>
      <PageHeader title={<span className="flex items-center gap-2"><BrainCircuitIcon className="size-5 text-[#c084fc]" /> Agent command center</span>} description="A labeled review queue for decomposing requests, checking agent understanding, and routing accepted research." actions={<Badge tone={synced ? 'profit' : 'warn'}>{synced ? 'CONTEXT CONNECTED' : 'SYNC STORE BOOTH 1 FIRST'}</Badge>} />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Metric boxed label="Agents" value="04" hint="Labeled response lanes" />
        <Metric boxed label="Context" value={synced ? 'READY' : '—'} hint="Store Booth and catalog" tone={synced ? 'profit' : 'neutral'} />
        <Metric boxed label="Review gate" value={submitted ? 'ACTIVE' : 'IDLE'} hint="Human-readable checks" />
        <Metric boxed label="Routing" value="AUTO" hint="Best agent per task" />
      </div>
      <Panel title="Request intake" description="The same request is sent to every enabled lane with the selected context.">
        <div className="grid gap-3 lg:grid-cols-[1fr_auto]">
          <Textarea value={request} onChange={(event) => setRequest(event.target.value)} className="min-h-24" />
          <Button variant="primary" className="self-end" disabled={!synced || !request.trim()} onClick={() => { setSubmitted(true); toast.success('Request distributed to labeled agent lanes') }}>Send to agents <ArrowRightIcon /></Button>
        </div>
        <p className="mt-2 text-xs text-ink-tertiary">{settings.region} · {settings.category} · {settings.datasets}</p>
      </Panel>
      <Panel title="Agent lanes" description="Each lane has a stable label so the command center can compare and refine responses.">
        <div className="grid gap-2 md:grid-cols-2">
          {agents.map(([name, role, status]) => <div key={name} className="flex items-center gap-3 rounded-md border border-hairline bg-surface-2 p-3"><span className="flex size-8 items-center justify-center rounded-md bg-[#8b5cf6]/15 text-xs font-medium text-[#c084fc]">{name.slice(0, 2)}</span><div className="min-w-0 flex-1"><p className="text-[13px] font-medium text-ink">{name} <span className="ml-1 text-xs text-ink-tertiary">· {role}</span></p><Badge tone={status === 'PRIORITY' ? 'warn' : 'profit'}>{submitted ? 'ANALYZING' : status}</Badge></div><CircleDotIcon className="size-4 text-profit" /></div>)}
        </div>
      </Panel>
    </Page>
  )
}

export function MonitoringScreen() {
  return (
    <Page>
      <PageHeader title={<span className="flex items-center gap-2"><GaugeIcon className="size-5 text-[#c084fc]" /> Agent monitoring</span>} description="Compare accepted responses and keep a routing preference for each quant task." />
      <Panel title="Routing recommendations" description="These are review outcomes, not hidden provider credentials. Add keys through LLM Integration.">
        <div className="grid gap-2">
          {[
            ['Power Pool alphas', 'DEEPSEEK', 'Best fit for candidate generation and ranking', 'PRIORITY'],
            ['Research explanation', 'CLAUDE', 'Best fit for readable field and operator reasoning', 'READY'],
            ['Request planning', 'KIMI', 'Best fit for breaking a broad request into steps', 'READY'],
            ['Final report card', 'CHATGPT', 'Best fit for synthesis and user-facing summaries', 'READY'],
          ].map(([task, agent, reason, badge]) => <div key={task} className="grid gap-2 rounded-md border border-hairline bg-surface-2 p-3 md:grid-cols-[1.1fr_140px_1.5fr_auto] md:items-center"><span className="font-medium text-ink">{task}</span><span className="num text-[#c084fc]">{agent}</span><span className="text-xs text-ink-subtle">{reason}</span><Badge tone={badge === 'PRIORITY' ? 'warn' : 'profit'}>{badge}</Badge></div>)}
        </div>
      </Panel>
      <Panel title="Research pipeline" description="Accepted work moves through the same stages used by the existing Labs and Tasks pages.">
        <div className="grid gap-2 md:grid-cols-5">
          {['Understand', 'Research', 'Review', 'Template', 'Simulate'].map((step, index) => <div key={step} className="flex items-center gap-2 rounded-md border border-hairline bg-surface-2 p-3"><span className="num text-xs text-primary-hover">0{index + 1}</span><span className="text-xs text-ink">{step}</span>{index < 4 && <ArrowRightIcon className="ml-auto size-3 text-ink-tertiary" />}</div>)}
        </div>
      </Panel>
      <Button render={<Link to="/quant-ai/data-drive" />}>Open data drive <ArrowRightIcon /></Button>
    </Page>
  )
}

export function SettingsScreen() {
  const { settings, saveSettings } = useFlowState()
  const [draft, setDraft] = useState(settings)
  return (
    <Page>
      <PageHeader title={<span className="flex items-center gap-2"><Settings2Icon className="size-5 text-[#c084fc]" /> QUANT AI settings</span>} description="Set the research context that follows the request into the data drive and templates." />
      <Panel title="Research context" description="These preferences are saved locally and are included in the agent handoff.">
        <div className="grid gap-4 md:grid-cols-2">
          <Field label="Region"><Input value={draft.region} onChange={(event) => setDraft({ ...draft, region: event.target.value })} placeholder="USA" /></Field>
          <Field label="Data category"><Input value={draft.category} onChange={(event) => setDraft({ ...draft, category: event.target.value })} placeholder="Fundamental" /></Field>
          <Field label="Datasets"><Input value={draft.datasets} onChange={(event) => setDraft({ ...draft, datasets: event.target.value })} placeholder="fundamental6" /></Field>
          <Field label="Data fields"><Input value={draft.fields} onChange={(event) => setDraft({ ...draft, fields: event.target.value })} placeholder="quality, value, momentum" /></Field>
          <Field label="Primary objective" className="md:col-span-2"><Input value={draft.objective} onChange={(event) => setDraft({ ...draft, objective: event.target.value })} /></Field>
        </div>
        <Button className="mt-4" variant="primary" onClick={() => { saveSettings(draft); toast.success('QUANT AI settings saved') }}>Save research context</Button>
      </Panel>
      <Panel title="Recommended handoff" description="The next pass researches fields, boosters, operators, and simulation settings.">
        <div className="flex flex-wrap items-center gap-2 text-xs text-ink-subtle"><Badge>{draft.region}</Badge><Badge>{draft.category}</Badge><Badge>{draft.datasets}</Badge><ArrowRightIcon className="size-3.5" /><Link className="text-primary-hover hover:underline" to="/quant-ai/data-drive">Start data drive</Link></div>
      </Panel>
    </Page>
  )
}

export function DataDriveScreen() {
  const { settings } = useFlowState()
  const [started, setStarted] = useState(false)
  const rows = useMemo(() => [['Field coverage', started ? 'COMPLETE' : 'READY'], ['Booster candidates', started ? 'REVIEWING' : 'QUEUED'], ['Operator fit', started ? 'REVIEWING' : 'QUEUED'], ['Simulation settings', started ? 'READY' : 'QUEUED']], [started])
  return (
    <Page>
      <PageHeader title={<span className="flex items-center gap-2"><WorkflowIcon className="size-5 text-[#c084fc]" /> Data drive</span>} description="Deep-research the selected category before opening template creation." actions={<Badge tone={started ? 'profit' : 'outline'}>{started ? 'RESEARCH ACTIVE' : 'STAGED'}</Badge>} />
      <Panel title="Current research brief" description="The data drive keeps the agent conversation focused on the user's selected scope.">
        <div className="flex flex-wrap gap-2"><Badge>{settings.region}</Badge><Badge>{settings.category}</Badge><Badge>{settings.datasets}</Badge><Badge>{settings.fields}</Badge></div>
        <Button className="mt-4" variant="primary" onClick={() => { setStarted(true); toast.success('Data drive started') }}><SparklesIcon /> Start deep research</Button>
      </Panel>
      <Panel title="Research checklist" description={`Each stage must be reviewed before templates are created for ${settings.category}.`}>
        <div className="grid gap-2 md:grid-cols-2">{rows.map(([label, status]) => <div key={label} className="flex items-center gap-3 rounded-md border border-hairline bg-surface-2 p-3"><CheckCircle2Icon className={status === 'QUEUED' ? 'size-4 text-ink-tertiary' : 'size-4 text-profit'} /><span className="flex-1 text-[13px] text-ink">{label}</span><Badge tone={status === 'QUEUED' ? 'muted' : 'profit'}>{status}</Badge></div>)}</div>
      </Panel>
      <Button disabled={!started} render={<Link to="/quant-ai/templates" />}>Open template creation <ArrowRightIcon /></Button>
    </Page>
  )
}

export function TemplatesScreen() {
  return (
    <Page>
      <PageHeader title={<span className="flex items-center gap-2"><GitBranchIcon className="size-5 text-[#c084fc]" /> QUANT AI templates</span>} description="Turn reviewed research into BRAIN-ready expressions, then send them to Tasks for simulation." />
      <Panel title="Session 2 · Template creation" description="The existing Template Lab validates the expression rules and creates a simulation task.">
        <div className="grid gap-2 md:grid-cols-3">{[['01', 'Choose template blocks', '/labs/template'], ['02', 'Validate expression', '/labs/template'], ['03', 'Send to Tasks', '/tasks']].map(([number, label, to]) => <Link key={number} to={to} className="rounded-md border border-hairline bg-surface-2 p-4 transition-colors hover:border-primary/60"><span className="num text-xs text-primary-hover">{number}</span><p className="mt-2 text-[13px] font-medium text-ink">{label}</p><ArrowRightIcon className="mt-4 size-4 text-ink-tertiary" /></Link>)}</div>
      </Panel>
      <Panel title="Quality gate" description="The best ten alphas return to QUANT AI as a report card after simulation.">
        <div className="flex items-center gap-3 rounded-md border border-profit/30 bg-profit/5 p-4"><ListChecksIcon className="size-5 text-profit" /><div><p className="text-[13px] font-medium text-ink">Ready for simulation</p><p className="text-xs text-ink-subtle">Use Template Lab to create the task, then monitor results in Tasks.</p></div></div>
      </Panel>
    </Page>
  )
}