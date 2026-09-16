import { Link } from '@tanstack/react-router'
import { ArrowUpRightIcon, DatabaseIcon, SparklesIcon } from 'lucide-react'
import { Page, PageHeader, Panel, Button, Badge, Metric } from '@/ui/kit'

export function QuantAiScreen() {
  return (
    <Page>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <SparklesIcon className="size-5 text-[#c084fc]" aria-hidden />
            QUANT AI
          </span>
        }
        description="A focused workspace for turning your research into useful, reusable intelligence."
      />

      <Panel className="overflow-hidden" bodyClassName="p-0">
        <div className="relative overflow-hidden p-6 sm:p-8">
          <div className="pointer-events-none absolute -top-28 right-[-5%] size-72 rounded-full bg-[#8b5cf6]/10 blur-3xl" aria-hidden />
          <div className="relative max-w-2xl">
            <Badge tone="outline">WORKSPACE READY</Badge>
            <h2 className="mt-4 text-2xl font-medium tracking-[-0.5px] text-ink sm:text-3xl">A new home for your quant intelligence.</h2>
            <p className="mt-3 max-w-xl text-[14px] leading-6 text-ink-subtle">
              QUANT AI will become the place where your sources, ideas, and research workflows connect. Start by giving it a
              knowledge base in STORE BOOTH 1.
            </p>
            <div className="mt-6 flex flex-wrap gap-2">
              <Button render={<Link to="/quant-ai/store-booth-1" />}>
                <DatabaseIcon /> Open STORE BOOTH 1
              </Button>
              <Button variant="ghost" disabled>
                More tools coming soon
              </Button>
            </div>
          </div>
        </div>
        <div className="grid border-t border-hairline sm:grid-cols-3">
          <Metric label="Workspace" value="QUANT AI" hint="Your future AI command center" boxed />
          <Metric label="Knowledge sources" value="—" hint="Add your first source in the booth" />
          <Metric label="Next step" value="01" hint="Build your source memory" />
        </div>
      </Panel>

      <div className="grid gap-3 lg:grid-cols-2">
        <Panel title="What belongs here" description="The direction is intentionally open for your next instructions.">
          <div className="grid gap-2.5">
            {[
              ['Source-aware assistance', 'Ask questions against the material you choose to keep close.'],
              ['Research workflows', 'Turn repeatable quant work into focused tools instead of scattered notes.'],
              ['Private by default', 'Your first source memory stays in this workspace until you decide otherwise.'],
            ].map(([title, text]) => (
              <div key={title} className="rounded-md border border-hairline bg-surface-2 px-3 py-2.5">
                <p className="text-[13px] font-medium text-ink">{title}</p>
                <p className="mt-0.5 text-xs leading-5 text-ink-subtle">{text}</p>
              </div>
            ))}
          </div>
        </Panel>
        <Panel title="Start with your sources" description="Bring in the documents that should inform future work.">
          <div className="flex min-h-36 flex-col items-center justify-center rounded-md border border-dashed border-hairline-strong bg-surface-2 px-5 text-center">
            <div className="flex size-9 items-center justify-center rounded-md bg-[#8b5cf6]/15 text-[#c084fc]">
              <DatabaseIcon className="size-5" aria-hidden />
            </div>
            <p className="mt-3 text-[13px] font-medium text-ink">Your source memory starts in STORE BOOTH 1</p>
            <Link to="/quant-ai/store-booth-1" className="mt-2 inline-flex items-center gap-1 text-xs text-primary-hover hover:underline">
              Add files and sources <ArrowUpRightIcon className="size-3.5" />
            </Link>
          </div>
        </Panel>
      </div>
    </Page>
  )
}