/** Research Labs: every lab, two by two. */

import { Link } from '@tanstack/react-router'
import { ArrowUpRightIcon, BlocksIcon, DnaIcon, FlaskConicalIcon, SearchIcon, type LucideIcon } from 'lucide-react'
import { LAB_TABS } from '@/shell/nav'
import { Page, PageHeader } from '@/ui/kit'

const LABS = 4

const ICONS: Record<(typeof LAB_TABS)[number]['tab'], LucideIcon> = { search: SearchIcon, template: BlocksIcon, evolution: DnaIcon }

const number = (index: number) => String(index + 1).padStart(2, '0')

export function ResearchLabsScreen() {
  return (
    <Page>
      <PageHeader title="Research Labs" />
      <div className="grid gap-3 sm:grid-cols-2">
        {LAB_TABS.map((lab, index) => {
          const Icon = ICONS[lab.tab]
          return (
            <Link
              key={lab.tab}
              to={lab.to}
              className="group flex min-h-56 flex-col justify-between rounded-lg border border-hairline bg-surface-1 p-6 transition-colors hover:border-primary/60 hover:bg-surface-2"
            >
              <div className="flex items-start justify-between">
                <span className="flex size-12 items-center justify-center rounded-md border border-hairline-strong bg-surface-2 text-ink-muted transition-colors group-hover:border-primary/60 group-hover:text-primary">
                  <Icon className="size-6" aria-hidden />
                </span>
                <ArrowUpRightIcon className="size-5 text-ink-tertiary transition-colors group-hover:text-primary" aria-hidden />
              </div>
              <div className="flex flex-col gap-1.5">
                <span className="num text-xs text-ink-tertiary">{number(index)}</span>
                <span className="headline">{lab.label}</span>
              </div>
            </Link>
          )
        })}
        {Array.from({ length: LABS - LAB_TABS.length }, (_, i) => (
          <div key={i} className="flex min-h-56 flex-col justify-between rounded-lg border border-dashed border-hairline p-6 text-ink-tertiary">
            <span className="flex size-12 items-center justify-center rounded-md border border-dashed border-hairline">
              <FlaskConicalIcon className="size-6" aria-hidden />
            </span>
            <div className="flex flex-col gap-1.5">
              <span className="num text-xs">{number(LAB_TABS.length + i)}</span>
              <span className="headline text-ink-tertiary">Coming Soon</span>
            </div>
          </div>
        ))}
      </div>
    </Page>
  )
}
