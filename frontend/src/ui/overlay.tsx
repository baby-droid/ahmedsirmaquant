/**
 * Floating surfaces on Base UI's headless primitives (focus trap, dismissal, ARIA), styled
 * as lifted Linear surfaces: surface-2/3 with a strong hairline, no shadows.
 */

import type { ReactElement, ReactNode } from 'react'
import { Dialog as BDialog } from '@base-ui/react/dialog'
import { Menu as BMenu } from '@base-ui/react/menu'
import { Select as BSelect } from '@base-ui/react/select'
import { Tooltip as BTooltip } from '@base-ui/react/tooltip'
import { CheckIcon, ChevronDownIcon, XIcon } from 'lucide-react'
import { Button, cx } from './kit'

interface DialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  description?: ReactNode
  children?: ReactNode
  footer?: ReactNode
  className?: string
}

function Frame({ title, description, children, footer }: Omit<DialogProps, 'open' | 'onOpenChange' | 'className'>) {
  return (
    <>
      <div className="flex items-start justify-between gap-4 border-b border-hairline px-4 py-3">
        <div className="flex min-w-0 flex-col gap-0.5">
          <BDialog.Title className="title">{title}</BDialog.Title>
          {description && <BDialog.Description className="text-xs text-ink-subtle">{description}</BDialog.Description>}
        </div>
        <BDialog.Close render={<Button variant="ghost" size="icon-sm" aria-label="Close" />}>
          <XIcon />
        </BDialog.Close>
      </div>
      <div className="min-h-0 flex-1 overflow-auto p-4">{children}</div>
      {footer && <div className="flex flex-wrap justify-end gap-2 border-t border-hairline px-4 py-3">{footer}</div>}
    </>
  )
}

export function Dialog({ open, onOpenChange, className, ...frame }: DialogProps) {
  return (
    <BDialog.Root open={open} onOpenChange={(next) => onOpenChange(next)}>
      <BDialog.Portal>
        <BDialog.Backdrop className="fixed inset-0 z-50 bg-black/60" />
        <BDialog.Popup
          className={cx(
            'fixed top-1/2 left-1/2 z-50 flex max-h-[85vh] w-[calc(100%-2rem)] max-w-lg -translate-x-1/2 -translate-y-1/2 flex-col rounded-lg border border-hairline-strong bg-surface-2 outline-none',
            className,
          )}
        >
          <Frame {...frame} />
        </BDialog.Popup>
      </BDialog.Portal>
    </BDialog.Root>
  )
}

/** A right-hand detail panel. */
export function Sheet({ open, onOpenChange, className, ...frame }: DialogProps) {
  return (
    <BDialog.Root open={open} onOpenChange={(next) => onOpenChange(next)}>
      <BDialog.Portal>
        <BDialog.Backdrop className="fixed inset-0 z-50 bg-black/50" />
        <BDialog.Popup
          className={cx(
            'fixed inset-y-0 right-0 z-50 flex w-full max-w-2xl flex-col border-l border-hairline-strong bg-surface-1 outline-none',
            className,
          )}
        >
          <Frame {...frame} />
        </BDialog.Popup>
      </BDialog.Portal>
    </BDialog.Root>
  )
}

/** Anything that spends quota or asks BRAIN to cancel goes through one of these. */
export function Confirm({
  open,
  onOpenChange,
  title,
  children,
  confirmLabel,
  cancelLabel = 'Cancel',
  onConfirm,
  danger,
  pending,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: ReactNode
  children?: ReactNode
  confirmLabel: ReactNode
  cancelLabel?: ReactNode
  onConfirm: () => void
  danger?: boolean
  pending?: boolean
}) {
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      footer={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            {cancelLabel}
          </Button>
          <Button variant={danger ? 'danger' : 'primary'} loading={pending} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="text-[13px] text-ink-muted">{children}</div>
    </Dialog>
  )
}

const POPUP = 'rounded-md border border-hairline-strong bg-surface-3 p-1 outline-none'
const ITEM =
  'flex h-8 cursor-default items-center gap-2 rounded-sm px-2 text-[13px] text-ink-muted outline-none select-none data-[disabled]:opacity-50 data-[highlighted]:bg-surface-4 data-[highlighted]:text-ink'

export interface Choice<V extends string = string> {
  value: V
  label: string
}

export function Select<V extends string>({
  value,
  onChange,
  items,
  label,
  className,
  mono,
  disabled,
}: {
  value: V | null
  onChange: (value: V) => void
  items: Choice<V>[]
  label: string
  className?: string
  mono?: boolean
  disabled?: boolean
}) {
  return (
    <BSelect.Root items={items} value={value} disabled={disabled} onValueChange={(next) => next != null && onChange(next as V)}>
      <BSelect.Trigger
        aria-label={label}
        className={cx(
          'inline-flex h-8 min-w-0 items-center justify-between gap-2 rounded-md border border-hairline bg-surface-1 px-2.5 text-[13px] text-ink transition-colors hover:border-hairline-strong data-[disabled]:opacity-50 data-[popup-open]:border-primary',
          mono && 'num',
          className,
        )}
      >
        <BSelect.Value className="truncate" />
        <BSelect.Icon className="text-ink-subtle">
          <ChevronDownIcon className="size-3.5" />
        </BSelect.Icon>
      </BSelect.Trigger>
      <BSelect.Portal>
        <BSelect.Positioner sideOffset={4} alignItemWithTrigger={false} className="z-50">
          <BSelect.Popup className={cx(POPUP, 'max-h-(--available-height) min-w-(--anchor-width) overflow-y-auto')}>
            <BSelect.List>
              {items.map((item) => (
                <BSelect.Item key={item.value} value={item.value} className={cx(ITEM, 'justify-between', mono && 'num')}>
                  <BSelect.ItemText>{item.label}</BSelect.ItemText>
                  <BSelect.ItemIndicator className="text-primary">
                    <CheckIcon className="size-3.5" />
                  </BSelect.ItemIndicator>
                </BSelect.Item>
              ))}
            </BSelect.List>
          </BSelect.Popup>
        </BSelect.Positioner>
      </BSelect.Portal>
    </BSelect.Root>
  )
}

export interface MenuItem {
  label: ReactNode
  onClick: () => void
  icon?: ReactNode
  danger?: boolean
  disabled?: boolean
}

export function Menu({ trigger, items, align = 'end' }: { trigger: ReactElement; items: MenuItem[]; align?: 'start' | 'center' | 'end' }) {
  return (
    <BMenu.Root>
      <BMenu.Trigger render={trigger} />
      <BMenu.Portal>
        <BMenu.Positioner sideOffset={4} align={align} className="z-50">
          <BMenu.Popup className={cx(POPUP, 'min-w-44')}>
            {items.map((item, i) => (
              <BMenu.Item key={i} disabled={item.disabled} onClick={item.onClick} className={cx(ITEM, item.danger && 'text-loss data-[highlighted]:text-loss', '[&_svg]:size-3.5')}>
                {item.icon}
                {item.label}
              </BMenu.Item>
            ))}
          </BMenu.Popup>
        </BMenu.Positioner>
      </BMenu.Portal>
    </BMenu.Root>
  )
}

export function Tooltip({ content, children }: { content: ReactNode; children: ReactElement }) {
  return (
    <BTooltip.Root>
      <BTooltip.Trigger render={children} />
      <BTooltip.Portal>
        <BTooltip.Positioner sideOffset={6} className="z-50">
          <BTooltip.Popup className="max-w-sm rounded-md border border-hairline-strong bg-surface-3 px-2 py-1.5 text-xs text-ink-muted">
            {content}
          </BTooltip.Popup>
        </BTooltip.Positioner>
      </BTooltip.Portal>
    </BTooltip.Root>
  )
}
