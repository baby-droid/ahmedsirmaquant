/**
 * The small primitives every screen composes. Linear rules: surfaces carry hierarchy
 * (canvas → surface-1 panel → surface-2 lift), hairlines divide, lavender marks interaction
 * only, profit/loss mark data only, and every figure uses `num`.
 */

import { Fragment, type ComponentProps, type ReactNode } from 'react'
import { mergeProps } from '@base-ui/react/merge-props'
import { Radio } from '@base-ui/react/radio'
import { RadioGroup } from '@base-ui/react/radio-group'
import { Toggle } from '@base-ui/react/toggle'
import { ToggleGroup } from '@base-ui/react/toggle-group'
import { useRender } from '@base-ui/react/use-render'
import { createLink } from '@tanstack/react-router'
import { cva, type VariantProps } from 'class-variance-authority'
import { ChevronRightIcon, CircleAlertIcon, InfoIcon, LoaderCircleIcon, TriangleAlertIcon } from 'lucide-react'
import { ApiError, errorMessage } from '@/api/http'
import type { CheckResult } from '@/api/types'
import { cn } from '@/lib/cn'

/** tailwind-merge aware: a later conflicting utility (from props) wins over the variant's. */
export const cx = cn

// ── Tone helpers: data colour comes from the value, never from decoration ──────────────

export type Tone = 'neutral' | 'muted' | 'profit' | 'loss' | 'warn'

export const TEXT_TONE: Record<Tone, string> = {
  neutral: 'text-ink',
  muted: 'text-ink-subtle',
  profit: 'text-profit',
  loss: 'text-loss',
  warn: 'text-warn',
}

/** Sign of a return, Sharpe or PnL. */
export const signTone = (v: number | null | undefined): Tone => (v == null || v === 0 ? 'neutral' : v > 0 ? 'profit' : 'loss')

/** A BRAIN submission check result. */
export const checkTone = (result: CheckResult | null | undefined): Tone =>
  result === 'PASS' ? 'profit' : result === 'FAIL' || result === 'ERROR' ? 'loss' : result ? 'warn' : 'muted'

// ── Layout ──────────────────────────────────────────────────────────────────────────────

/** A screen's root: the canvas shows through the gaps as the gutter. */
export function Page({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx('flex min-w-0 flex-col gap-3 p-3 lg:p-4', className)}>{children}</div>
}

export function PageHeader({ title, description, actions }: { title: ReactNode; description?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 px-1 pt-1">
      <div className="flex min-w-0 flex-col gap-1">
        <h1 className="title-lg">{title}</h1>
        {description && <p className="max-w-3xl text-[13px] text-ink-subtle">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

export function Panel({
  title,
  description,
  actions,
  children,
  className,
  bodyClassName,
}: {
  title?: ReactNode
  description?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  className?: string
  bodyClassName?: string
}) {
  return (
    <section className={cx('flex min-w-0 flex-col rounded-lg border border-hairline bg-surface-1', className)}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-hairline px-4 py-3">
          <div className="flex min-w-0 flex-col gap-0.5">
            {title && <h2 className="title">{title}</h2>}
            {description && <p className="text-xs text-ink-subtle">{description}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cx('min-w-0 p-4', bodyClassName)}>{children}</div>
    </section>
  )
}

// ── Actions ─────────────────────────────────────────────────────────────────────────────

/** DESIGN.md button-primary/secondary/tertiary; md is the spec's 8px 14px padding, 14px/500, 8px radius. */
export const buttonVariants = cva(
  'inline-flex shrink-0 items-center justify-center gap-1.5 font-medium whitespace-nowrap transition-colors select-none disabled:pointer-events-none disabled:opacity-40 aria-disabled:pointer-events-none aria-disabled:opacity-40 [&_svg]:shrink-0',
  {
    variants: {
      variant: {
        primary: 'bg-primary text-white hover:bg-primary-hover active:bg-primary-focus',
        secondary: 'border border-hairline bg-surface-1 text-ink hover:border-hairline-strong hover:bg-surface-2',
        tertiary: 'border border-transparent bg-canvas text-ink hover:border-hairline',
        ghost: 'text-ink-muted hover:bg-surface-2 hover:text-ink',
        danger: 'border border-hairline bg-surface-1 text-loss hover:border-loss/50 hover:bg-loss/10',
      },
      size: {
        md: 'h-[34px] rounded-md px-3.5 text-[14px] [&_svg]:size-4',
        sm: 'h-7 rounded-sm px-2.5 text-xs [&_svg]:size-3.5',
        icon: 'size-[34px] rounded-md [&_svg]:size-4',
        'icon-sm': 'size-7 rounded-sm [&_svg]:size-3.5',
      },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export type ButtonProps = ComponentProps<'button'> &
  VariantProps<typeof buttonVariants> & {
    loading?: boolean
    /** Render as another element with the button's look, e.g. `render={<a href={url} />}` or `render={<Link to="…" />}`. */
    render?: useRender.RenderProp
  }

export function Button({ variant, size, loading, render, className, children, disabled, type = 'button', ref, ...props }: ButtonProps) {
  const inert = Boolean(disabled || loading)
  return useRender({
    defaultTagName: 'button',
    render,
    ref,
    props: mergeProps<'button'>(
      {
        // A rendered link cannot be `disabled`; it is marked aria-disabled and made inert by CSS.
        type: render ? undefined : type,
        disabled: render ? undefined : inert,
        'aria-disabled': render && inert ? true : undefined,
        className: cn(buttonVariants({ variant, size }), className),
        children: (
          <>
            {loading && <LoaderCircleIcon className="animate-spin" />}
            {children}
          </>
        ),
      },
      props,
    ),
  })
}

// ── Inputs ──────────────────────────────────────────────────────────────────────────────

const FIELD =
  'w-full min-w-0 rounded-md border border-hairline bg-surface-1 px-3 text-[13px] text-ink placeholder:text-ink-tertiary transition-colors hover:border-hairline-strong focus-visible:border-primary focus-visible:outline-none disabled:opacity-50'

export function Input({ className, ...props }: ComponentProps<'input'>) {
  return <input className={cx(FIELD, 'h-8', props.type === 'number' && 'num', className)} {...props} />
}

export function Textarea({ className, ...props }: ComponentProps<'textarea'>) {
  return <textarea className={cx(FIELD, 'min-h-20 py-2', className)} {...props} />
}

export function Field({ label, hint, children, className }: { label: ReactNode; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cx('flex min-w-0 flex-col gap-1.5', className)}>
      <span className="text-xs font-medium text-ink-muted">{label}</span>
      {children}
      {hint && <span className="text-xs text-ink-tertiary">{hint}</span>}
    </label>
  )
}

export function Checkbox({ label, className, ...props }: Omit<ComponentProps<'input'>, 'type'> & { label: ReactNode }) {
  return (
    <label className={cx('inline-flex items-center gap-2 text-[13px] text-ink-muted', className)}>
      <input type="checkbox" className="size-3.5" {...props} />
      {label}
    </label>
  )
}

/** Multi-choice as toggle chips (Base UI ToggleGroup: pressed state, arrow-key focus). Selection is interaction state, so it is lavender. */
export function Chips<V extends string>({
  items,
  value,
  onChange,
  label,
  disabled,
}: {
  items: { value: V; label: ReactNode; title?: string }[]
  value: V[]
  onChange: (value: V[]) => void
  label: string
  disabled?: boolean
}) {
  return (
    <ToggleGroup multiple aria-label={label} value={value} disabled={disabled} onValueChange={(next) => onChange(next as V[])} className="flex flex-wrap gap-1.5">
      {items.map((item) => (
        <Toggle
          key={item.value}
          value={item.value}
          title={item.title}
          className="h-7 rounded-sm border border-hairline bg-surface-1 px-2.5 text-xs text-ink-subtle transition-colors hover:border-hairline-strong hover:text-ink data-[disabled]:opacity-50 data-[pressed]:border-primary/60 data-[pressed]:bg-primary/15 data-[pressed]:text-ink"
        >
          {item.label}
        </Toggle>
      ))}
    </ToggleGroup>
  )
}

/** One choice among a few, as a segmented control (Base UI RadioGroup: radio semantics, arrow-key focus). */
export function Segmented<V extends string | number>({
  items,
  value,
  onChange,
  label,
  disabled,
}: {
  items: { value: V; label: ReactNode }[]
  value: V
  onChange: (value: V) => void
  label: string
  disabled?: boolean
}) {
  return (
    <RadioGroup
      aria-label={label}
      value={String(value)}
      disabled={disabled}
      // Base UI values are strings; map back so numeric choices stay numbers.
      onValueChange={(next) => {
        const item = items.find((i) => String(i.value) === String(next))
        if (item) onChange(item.value)
      }}
      className="inline-flex flex-wrap items-center gap-0.5 rounded-md border border-hairline bg-surface-1 p-0.5"
    >
      {items.map((item) => (
        <Radio.Root
          key={String(item.value)}
          value={String(item.value)}
          className="inline-flex h-7 items-center justify-center rounded-sm border border-transparent px-3 text-xs leading-none font-medium text-ink-subtle transition-colors hover:text-ink data-[checked]:border-primary/60 data-[checked]:bg-primary/15 data-[checked]:text-ink data-[disabled]:opacity-50"
        >
          {item.label}
        </Radio.Root>
      ))}
    </RadioGroup>
  )
}

// ── Data display ────────────────────────────────────────────────────────────────────────

export function Metric({
  label,
  value,
  hint,
  tone = 'neutral',
  size = 'md',
  boxed,
}: {
  label: ReactNode
  value: ReactNode
  hint?: ReactNode
  tone?: Tone
  size?: 'sm' | 'md' | 'lg'
  /** A status box in DESIGN.md's brand-secure tint, as in the top bar. */
  boxed?: boolean
}) {
  return (
    <div className={cx('flex min-w-0 flex-col gap-1.5', boxed && 'rounded-md border border-brand-secure/35 bg-brand-secure/10 px-3 py-2.5')}>
      <span className={cx('text-xs', boxed ? 'text-brand-secure' : 'text-ink-subtle')}>{label}</span>
      <span className={cx('num leading-none', size === 'lg' ? 'text-3xl' : size === 'md' ? 'text-xl' : 'text-base', TEXT_TONE[tone])}>{value}</span>
      {hint && <span className="text-xs text-ink-tertiary">{hint}</span>}
    </div>
  )
}

const BADGE: Record<Tone | 'outline', string> = {
  neutral: 'bg-surface-3 text-ink-muted',
  muted: 'bg-surface-2 text-ink-subtle',
  profit: 'bg-profit/12 text-profit',
  loss: 'bg-loss/12 text-loss',
  warn: 'bg-warn/12 text-warn',
  outline: 'border border-hairline-strong text-ink-subtle',
}

export function Badge({ tone = 'neutral', children, className, title }: { tone?: Tone | 'outline'; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={cx('inline-flex h-5 items-center gap-1 rounded-full px-2 text-xs whitespace-nowrap', BADGE[tone], className)}>
      {children}
    </span>
  )
}

export function KV({ items, className }: { items: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cx('grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-[13px]', className)}>
      {items.map(([k, v], i) => (
        <Fragment key={i}>
          <dt className="text-ink-subtle">{k}</dt>
          <dd className="num min-w-0 truncate text-ink">{v}</dd>
        </Fragment>
      ))}
    </dl>
  )
}

export function Progress({ value, className, label }: { value: number | null; className?: string; label?: string }) {
  const clamped = value == null ? null : Math.min(1, Math.max(0, value))
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={1}
      aria-valuenow={clamped ?? undefined}
      className={cx('h-1 overflow-hidden rounded-full bg-surface-3', className)}
    >
      <div
        className={cx('h-full rounded-full bg-primary transition-[width]', clamped == null && 'w-1/3 animate-pulse')}
        style={clamped == null ? undefined : { width: `${Math.round(clamped * 100)}%` }}
      />
    </div>
  )
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="rounded-xs border border-hairline-strong bg-surface-2 px-1 text-[11px] leading-4 text-ink-subtle">{children}</kbd>
}

// ── Feedback ────────────────────────────────────────────────────────────────────────────

export function Spinner({ className }: { className?: string }) {
  return <LoaderCircleIcon aria-label="Loading" className={cx('size-4 animate-spin text-ink-subtle', className)} />
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx('animate-pulse rounded-md bg-surface-2', className)} />
}

const NOTICE = {
  info: { border: 'border-hairline-strong', icon: InfoIcon, color: 'text-ink-subtle' },
  warn: { border: 'border-warn/40', icon: TriangleAlertIcon, color: 'text-warn' },
  error: { border: 'border-loss/40', icon: CircleAlertIcon, color: 'text-loss' },
}

/** Never under-deliver silently: every refusal and shortfall is said in one of these. */
export function Notice({
  tone = 'info',
  title,
  children,
  action,
  className,
}: {
  tone?: keyof typeof NOTICE
  title?: ReactNode
  children?: ReactNode
  action?: ReactNode
  className?: string
}) {
  const { border, icon: Icon, color } = NOTICE[tone]
  return (
    <div role={tone === 'error' ? 'alert' : 'status'} className={cx('flex gap-3 rounded-md border bg-surface-2 px-3 py-2.5 text-[13px]', border, className)}>
      <Icon className={cx('mt-0.5 size-4 shrink-0', color)} aria-hidden />
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {title && <p className="font-medium text-ink">{title}</p>}
        {children && <div className="text-ink-muted">{children}</div>}
      </div>
      {action}
    </div>
  )
}

/** A thrown query/mutation error, with BRAIN's verification link when it asks for one. */
export function ErrorNotice({ error, title = 'That did not work', className }: { error: unknown; title?: ReactNode; className?: string }) {
  const url = error instanceof ApiError ? error.body.verificationUrl : undefined
  return (
    <Notice tone="error" title={title} className={className}>
      {errorMessage(error)}{' '}
      {url && (
        <a href={url} target="_blank" rel="noopener noreferrer" className="text-primary-hover underline underline-offset-2">
          Open the verification page
        </a>
      )}
    </Notice>
  )
}

export function Empty({ title, children, icon, className }: { title: ReactNode; children?: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <div className={cx('flex flex-col items-center justify-center gap-2 px-6 py-10 text-center', className)}>
      {icon && <div className="text-ink-tertiary [&_svg]:size-5">{icon}</div>}
      <p className="text-[13px] font-medium text-ink">{title}</p>
      {children && <div className="max-w-md text-xs text-ink-subtle">{children}</div>}
    </div>
  )
}

/** Advanced controls stay behind a one-click triangle (CLAUDE.md anti-goal 2). */
export function Disclosure({ summary, children, defaultOpen, className }: { summary: ReactNode; children: ReactNode; defaultOpen?: boolean; className?: string }) {
  return (
    <details open={defaultOpen} className={cx('group rounded-md border border-hairline', className)}>
      <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-[13px] text-ink-muted select-none hover:text-ink [&::-webkit-details-marker]:hidden">
        <ChevronRightIcon className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />
        {summary}
      </summary>
      <div className="border-t border-hairline p-3">{children}</div>
    </details>
  )
}

// ── Navigation ──────────────────────────────────────────────────────────────────────────

function TabAnchor({ className, ...props }: ComponentProps<'a'>) {
  return (
    <a
      {...props}
      className={cx(
        'relative inline-flex h-9 shrink-0 items-center text-[13px] text-ink-subtle transition-colors hover:text-ink',
        'after:absolute after:inset-x-0 after:bottom-0 after:h-px data-[status=active]:text-ink data-[status=active]:after:bg-primary',
        className,
      )}
    />
  )
}

/** A sub-tab that is a real link: `<TabLink to="/data/$tab" params={{ tab: 'fields' }}>`. */
export const TabLink = createLink(TabAnchor)

export function TabBar({ children }: { children: ReactNode }) {
  return <nav className="flex gap-5 overflow-x-auto overflow-y-hidden border-b border-hairline px-1">{children}</nav>
}

/** Stand-in while a screen is being built. */
export function Placeholder({ title }: { title: string }) {
  return (
    <Page>
      <PageHeader title={title} />
      <Panel>
        <Empty title="Being rebuilt">This screen moves to the new design system next.</Empty>
      </Panel>
    </Page>
  )
}
