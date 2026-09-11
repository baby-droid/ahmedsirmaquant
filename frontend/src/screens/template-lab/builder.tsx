/**
 * The block builder: a palette of the account's blocks beside a canvas holding the
 * template's tree. Blocks are dragged with the pointer onto slots, or placed by clicking a
 * slot and picking from a searchable list, so every change also works without dragging.
 */

import { createContext, useContext, useEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { Popover } from '@base-ui/react/popover'
import { Command } from 'cmdk'
import { CheckIcon, EraserIcon, RefreshCwIcon, SearchIcon, Undo2Icon, XIcon } from 'lucide-react'
import type { TemplateLabOptions } from '@/api/template-lab'
import {
  accepts,
  at,
  blank,
  choices,
  drop,
  firstHole,
  keyPath,
  move,
  nextTag,
  pathKey,
  put,
  retag,
  socketAt,
  tagsOf,
  unwrap,
  variable,
  within,
  type Blocks,
  type OperatorNode,
  type Path,
  type Slot,
  type Socket,
  type TemplateDoc,
  type TemplateNode,
  type VariableName,
  type VariableNode,
} from '@/lib/template-tree'
import { Button, Checkbox, Chips, Disclosure, Input, Notice, Panel, cx } from '@/ui/kit'
import { SplitPane } from '@/ui/panels'

const VARIABLES: VariableName[] = ['FIELD', 'LOOKBACK', 'FAST_LOOKBACK', 'SLOW_LOOKBACK', 'GROUP', 'WEIGHT', 'POWER']
const CATEGORIES = ['Cross Sectional', 'Time Series', 'Group', 'Arithmetic', 'Logical', 'Transformational']
const SOCKET_LABEL: Record<Socket, string> = { signal: 'Input', lookback: 'Lookback', group: 'Group' }

/** Scratch-style muted colours, one per operator category. */
const CATEGORY_COLOR: Record<string, string> = {
  'Cross Sectional': '#c07ac8',
  'Time Series': '#4fa3c7',
  Group: '#6aae84',
  Arithmetic: '#d08b57',
  Logical: '#c9b458',
  Transformational: '#d47a9c',
}

function tintOf(category: string | undefined): CSSProperties | undefined {
  const color = category && CATEGORY_COLOR[category]
  return color ? { backgroundColor: `color-mix(in srgb, ${color} 12%, var(--color-surface-2))`, borderColor: `color-mix(in srgb, ${color} 45%, transparent)` } : undefined
}

// ── Dragging ────────────────────────────────────────────────────────────────────────────

type Payload = { from: 'palette'; node: TemplateNode; label: string } | { from: 'canvas'; path: Path; label: string }

interface Dragging {
  payload: Payload
  x: number
  y: number
}

/**
 * Pointer dragging without a library. A press becomes a drag after 4px, so clicks still
 * click; the ghost follows the pointer through a ref, and React only hears when the slot
 * under the pointer changes. Escape or a cancelled pointer drops nothing.
 */
function useDragDrop(onDrop: (payload: Payload, target: string) => void) {
  const [dragging, setDragging] = useState<Dragging | null>(null)
  const [over, setOver] = useState<string | null>(null)
  const start = useRef<{ payload: Payload; x: number; y: number } | null>(null)
  const active = useRef(false)
  const target = useRef<string | null>(null)
  const ghost = useRef<HTMLDivElement | null>(null)
  const swallow = useRef(false)
  const pressed = useRef<Event | null>(null)
  const dropped = useRef(onDrop)

  useEffect(() => {
    dropped.current = onDrop
  })

  useEffect(() => {
    const finish = (commit: boolean) => {
      if (active.current) {
        if (commit && start.current && target.current) dropped.current(start.current.payload, target.current)
        // The click that follows a drag must not also open or place anything.
        swallow.current = true
        window.setTimeout(() => {
          swallow.current = false
        }, 0)
      }
      start.current = null
      active.current = false
      target.current = null
      setDragging(null)
      setOver(null)
    }
    const onMove = (event: PointerEvent) => {
      const pending = start.current
      if (!pending) return
      if (!active.current) {
        if (Math.hypot(event.clientX - pending.x, event.clientY - pending.y) < 4) return
        active.current = true
        setDragging({ payload: pending.payload, x: event.clientX, y: event.clientY })
      }
      if (ghost.current) ghost.current.style.translate = `${event.clientX + 14}px ${event.clientY + 10}px`
      const hit = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>('[data-drop]')
      const key = hit?.dataset.drop ?? null
      if (key !== target.current) {
        target.current = key
        setOver(key)
      }
    }
    const onUp = () => finish(true)
    const onCancel = () => finish(false)
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && start.current) finish(false)
    }
    window.addEventListener('pointermove', onMove)
    window.addEventListener('pointerup', onUp)
    window.addEventListener('pointercancel', onCancel)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('pointermove', onMove)
      window.removeEventListener('pointerup', onUp)
      window.removeEventListener('pointercancel', onCancel)
      window.removeEventListener('keydown', onKey)
    }
  }, [])

  const begin = (event: ReactPointerEvent, payload: Payload) => {
    // Nested blocks all hear the press; the innermost, which hears it first, is the one dragged.
    if (event.button !== 0 || pressed.current === event.nativeEvent) return
    if ((event.target as HTMLElement).closest('input, textarea')) return
    pressed.current = event.nativeEvent
    start.current = { payload, x: event.clientX, y: event.clientY }
  }

  const layer = dragging
    ? createPortal(
        <div
          ref={ghost}
          className="pointer-events-none fixed top-0 left-0 z-50 rounded-full border border-primary/60 bg-surface-3 px-2.5 py-1 num text-xs text-ink"
          style={{ translate: `${dragging.x + 14}px ${dragging.y + 10}px` }}
        >
          {dragging.payload.label}
        </div>,
        document.body,
      )
    : null

  return { dragging, over, begin, swallowed: () => swallow.current, layer }
}

// ── Context ─────────────────────────────────────────────────────────────────────────────

interface Ctx {
  root: Slot
  blocks: Blocks
  options: TemplateLabOptions | undefined
  dragging: Dragging | null
  over: string | null
  hoverTag: string | null
  setHoverTag: (tag: string | null) => void
  begin: (event: ReactPointerEvent, payload: Payload) => void
  fits: (payload: Payload, path: Path) => boolean
  change: (root: Slot) => void
  place: (node: TemplateNode) => void
}

const BuilderContext = createContext<Ctx | null>(null)

function useBuilder(): Ctx {
  const ctx = useContext(BuilderContext)
  if (!ctx) throw new Error('Template blocks must render inside the builder.')
  return ctx
}

function useDropState(path: Path): 'over' | 'fits' | 'blocked' | null {
  const ctx = useBuilder()
  if (!ctx.dragging) return null
  if (!ctx.fits(ctx.dragging.payload, path)) return 'blocked'
  return ctx.over === pathKey(path) ? 'over' : 'fits'
}

// ── The builder ─────────────────────────────────────────────────────────────────────────

export function Builder({
  doc,
  blocks,
  options,
  problems,
  skeleton,
  canUndo,
  syncing,
  onChange,
  onUndo,
  onSync,
}: {
  doc: TemplateDoc
  blocks: Blocks
  options: TemplateLabOptions | undefined
  problems: string[]
  skeleton: string | undefined
  canUndo: boolean
  syncing: boolean
  onChange: (doc: TemplateDoc) => void
  onUndo: () => void
  onSync: () => void
}) {
  const root = doc.root
  const [hoverTag, setHoverTag] = useState<string | null>(null)
  const change = (next: Slot) => onChange({ version: 1, root: next })

  const fits = (payload: Payload, path: Path): boolean => {
    const socket = socketAt(root, path, blocks)
    if (payload.from === 'palette') return accepts(socket, payload.node, blocks)
    const node = at(root, payload.path)
    return node !== null && !within(path, payload.path) && accepts(socket, node, blocks)
  }

  const { dragging, over, begin, swallowed, layer } = useDragDrop((payload, key) => {
    if (key === 'trash') {
      if (payload.from === 'canvas') change(put(root, payload.path, null))
      return
    }
    const path = keyPath(key)
    if (!fits(payload, path)) return
    if (payload.from === 'palette') {
      change(drop(root, path, payload.node, blocks))
      return
    }
    const next = move(root, payload.path, path)
    if (next !== undefined) change(next)
  })

  const place = (node: TemplateNode) => {
    const path = firstHole(root, node, blocks)
    if (path) change(drop(root, path, node, blocks))
    else if (node.kind === 'op') change(drop(root, [], node, blocks))
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.closest('input, textarea, [contenteditable="true"]')) return
      if ((event.metaKey || event.ctrlKey) && !event.shiftKey && event.key.toLowerCase() === 'z') {
        event.preventDefault()
        onUndo()
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onUndo])

  const ctx: Ctx = { root, blocks, options, dragging, over, hoverTag, setHoverTag, begin, fits, change, place }

  return (
    <BuilderContext.Provider value={ctx}>
      <Panel
        title="Blocks"
        actions={
          <>
            <Button size="sm" variant="ghost" disabled={!canUndo} onClick={onUndo}>
              <Undo2Icon />
              Undo
            </Button>
            <Button size="sm" variant="ghost" disabled={root === null} onClick={() => change(null)}>
              <EraserIcon />
              Clear
            </Button>
            <Button size="sm" variant="ghost" loading={syncing} onClick={onSync}>
              <RefreshCwIcon />
              Sync Operators
            </Button>
          </>
        }
      >
        <div
          className={cx('flex flex-col gap-3', dragging && 'cursor-grabbing select-none')}
          onClickCapture={(event) => {
            if (swallowed()) {
              event.preventDefault()
              event.stopPropagation()
            }
          }}
        >
          <SplitPane id="template-lab" first={{ default: 300, min: 220, max: 520 }}>
            <Palette />
            <div className="flex min-h-80 min-w-0 overflow-auto rounded-lg border border-hairline bg-canvas p-4">
              <SlotView slot={root} path={[]} />
            </div>
          </SplitPane>
          {problems.map((problem) => (
            <Notice key={problem} tone="error" title={problem} />
          ))}
          {skeleton && root !== null && (
            <Disclosure summary="Expression">
              <code className="num text-xs break-all text-ink">{skeleton}</code>
            </Disclosure>
          )}
        </div>
      </Panel>
      {layer}
    </BuilderContext.Provider>
  )
}

// ── Palette ─────────────────────────────────────────────────────────────────────────────

interface PaletteItem {
  key: string
  label: string
  node: TemplateNode
  hint?: string
}

function paletteSections(root: Slot, blocks: Blocks, options: TemplateLabOptions | undefined): { heading: string; items: PaletteItem[] }[] {
  const operators = new Map<string, PaletteItem[]>()
  for (const block of Object.values(blocks)) {
    const items = operators.get(block.category) ?? []
    items.push({ key: `op:${block.name}`, label: block.symbol ? `${block.name} ${block.symbol}` : block.name, node: blank(block), hint: block.description ?? undefined })
    operators.set(block.category, items)
  }
  const categories = [...CATEGORIES.filter((c) => operators.has(c)), ...[...operators.keys()].filter((c) => !CATEGORIES.includes(c))]
  return [
    { heading: 'Variables', items: VARIABLES.map((name) => ({ key: `var:${name}`, label: name, node: variable(root, name), hint: describe(name, options) })) },
    { heading: 'Data Fields', items: (options?.dataFields ?? []).map((name) => ({ key: `data:${name}`, label: name, node: { kind: 'data', name } })) },
    { heading: 'Grouping Fields', items: (options?.groupFields ?? []).map((name) => ({ key: `group:${name}`, label: name, node: { kind: 'data', name } })) },
    { heading: 'Numbers', items: [{ key: 'num', label: 'Number', node: { kind: 'num', value: 1 } }] },
    ...categories.map((heading) => ({ heading, items: operators.get(heading) ?? [] })),
  ]
}

function describe(name: VariableName, options: TemplateLabOptions | undefined): string {
  if (name === 'FIELD') return 'A field from your datasets. Blocks with the same letter are the same field.'
  const values = options?.variables[name] ?? []
  return `Blocks with the same letter take the same value: ${values.join(', ')}.`
}

function Palette() {
  const ctx = useBuilder()
  const [search, setSearch] = useState('')
  const term = search.trim().toLowerCase()
  const sections = paletteSections(ctx.root, ctx.blocks, ctx.options)
    .map((section) => ({ ...section, items: term ? section.items.filter((item) => item.label.toLowerCase().includes(term)) : section.items }))
    .filter((section) => section.items.length > 0)
  const removing = ctx.dragging?.payload.from === 'canvas'

  return (
    <div
      data-drop="trash"
      className={cx(
        'flex h-full min-h-0 flex-col gap-3 rounded-lg border p-3 transition-colors',
        removing && ctx.over === 'trash' ? 'border-loss/50 bg-loss/10' : removing ? 'border-hairline-strong' : 'border-hairline',
      )}
    >
      <Input placeholder="Search blocks" aria-label="Search blocks" value={search} onChange={(e) => setSearch(e.target.value)} />
      <div className="flex max-h-[30rem] min-h-0 flex-col gap-3 overflow-y-auto pr-1">
        {sections.map((section) => (
          <div key={section.heading} className="flex flex-col gap-1.5">
            <span className="eyebrow">{section.heading}</span>
            <div className="flex flex-wrap gap-1.5">
              {section.items.map((item) => (
                <button
                  key={item.key}
                  type="button"
                  title={item.hint}
                  onPointerDown={(event) => ctx.begin(event, { from: 'palette', node: item.node, label: item.label })}
                  onClick={() => ctx.place(item.node)}
                  style={item.node.kind === 'op' ? tintOf(ctx.blocks[item.node.ops[0]]?.category) : undefined}
                  className={cx(
                    'inline-flex h-7 cursor-grab touch-none items-center border border-hairline-strong bg-surface-2 px-2.5 num text-xs text-ink-muted transition-colors select-none hover:border-hairline-tertiary hover:text-ink',
                    item.node.kind === 'op' ? 'rounded-md' : 'rounded-full',
                  )}
                >
                  {item.label}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Canvas ──────────────────────────────────────────────────────────────────────────────

/** `suffix` is the punctuation that follows the slot in its parent's call: a comma, or closing brackets. */
function SlotView({ slot, path, suffix }: { slot: Slot; path: Path; suffix?: string }) {
  if (slot?.kind === 'op') return <OperatorBlock node={slot} path={path} suffix={suffix} />
  const view = slot === null ? <EmptySlot path={path} /> : <Leaf node={slot} path={path} />
  if (!suffix) return view
  return (
    <span className="flex items-center">
      {view}
      <Punct text={suffix} />
    </span>
  )
}

function Floating({ open, onOpenChange, trigger, children }: { open: boolean; onOpenChange: (open: boolean) => void; trigger: ReactNode; children: ReactNode }) {
  return (
    <Popover.Root open={open} onOpenChange={onOpenChange}>
      {trigger}
      <Popover.Portal>
        <Popover.Positioner sideOffset={6} align="start" className="z-50">
          <Popover.Popup className="overflow-hidden rounded-md border border-hairline-strong bg-surface-3 outline-none">{children}</Popover.Popup>
        </Popover.Positioner>
      </Popover.Portal>
    </Popover.Root>
  )
}

function EmptySlot({ path }: { path: Path }) {
  const ctx = useBuilder()
  const [open, setOpen] = useState(false)
  const socket = socketAt(ctx.root, path, ctx.blocks)
  const state = useDropState(path)
  const isRoot = path.length === 0

  return (
    <Floating
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Popover.Trigger
          data-drop={pathKey(path)}
          className={cx(
            'inline-flex items-center justify-center border border-dashed transition-colors',
            isRoot ? 'min-h-40 w-full rounded-lg px-6 text-[13px]' : 'h-6 min-w-16 rounded-full px-2.5 text-xs',
            state === 'over'
              ? 'border-primary/60 bg-primary/15 text-ink'
              : state === 'fits'
                ? 'border-primary/60 text-ink-subtle'
                : state === 'blocked'
                  ? 'border-hairline-strong text-ink-tertiary opacity-40'
                  : 'border-hairline-strong text-ink-tertiary hover:border-hairline-tertiary hover:text-ink-subtle',
          )}
        >
          {isRoot ? 'Drag a block here, or click to choose one' : SOCKET_LABEL[socket]}
        </Popover.Trigger>
      }
    >
      <BlockSearch
        socket={socket}
        onPick={(node) => {
          ctx.change(drop(ctx.root, path, node, ctx.blocks))
          setOpen(false)
        }}
      />
    </Floating>
  )
}

function OperatorBlock({ node, path, suffix = '' }: { node: OperatorNode; path: Path; suffix?: string }) {
  const ctx = useBuilder()
  const [open, setOpen] = useState(false)
  const state = useDropState(path)
  const known = ctx.blocks[node.ops[0]] !== undefined
  // Drop highlights take over the colour while something is dragged over the block.
  const tint = state === 'over' || state === 'fits' ? undefined : tintOf(ctx.blocks[node.ops[0]]?.category)
  const label = node.ops.join(' OR ')
  const options = Object.entries(node.options ?? {})
  const count = node.args.length + options.length
  // Only leaves inside: the whole call reads on one line, like `ts_zscore(field, lookback)`.
  const inline = node.args.every((arg) => arg?.kind !== 'op')
  const close = `)${suffix}`

  // A comma follows each argument; the closing bracket ends the line when inline, or sits on its own line under the name.
  const tail = (position: number) => (position < count - 1 ? ',' : inline ? close : undefined)
  const arg = (index: number) => <SlotView key={index} slot={node.args[index]} path={[...path, index]} suffix={tail(index)} />
  const optionArgs = options.map(([key, value], index) => {
    const text = tail(node.args.length + index)
    return (
      <span key={`option:${key}`} className="flex items-center">
        <span className="num text-xs text-ink-subtle">
          {key}={String(value)}
        </span>
        {text && <Punct text={text} />}
      </span>
    )
  })
  const lines = rows(node.args)

  return (
    <div
      data-drop={pathKey(path)}
      onPointerDown={(event) => ctx.begin(event, { from: 'canvas', path, label })}
      className={cx(
        'flex w-fit max-w-full min-w-0 cursor-grab touch-none flex-col gap-1 rounded-lg border bg-surface-2 px-1.5 py-1.5 transition-colors select-none hover:border-hairline-tertiary',
        state === 'over' ? 'border-primary/60 bg-primary/15' : state === 'fits' ? 'border-primary/60' : known ? 'border-hairline-strong' : 'border-loss/50',
      )}
      style={tint}
    >
      <div className="flex min-w-0 flex-wrap items-center gap-1">
        <span className="flex min-w-0 items-center">
          <Floating
            open={open}
            onOpenChange={setOpen}
            trigger={<Popover.Trigger className="num min-w-0 truncate rounded-sm py-0.5 pr-0.5 pl-1 text-left text-[13px] text-ink hover:bg-surface-3">{label}</Popover.Trigger>}
          >
            <BlockMenu node={node} path={path} close={() => setOpen(false)} />
          </Floating>
          <Punct text={count === 0 ? `(${close}` : '('} />
        </span>
        {inline && node.args.map((_, index) => arg(index))}
        {inline && optionArgs}
      </div>
      {!inline && (
        <>
          <div className="ml-1 flex flex-col items-start gap-1 border-l border-hairline-strong pl-3" style={tint && { borderColor: tint.borderColor }}>
            {lines.map((row, index) => (
              <div key={row[0]} className="flex max-w-full flex-wrap items-center gap-1.5">
                {row.map(arg)}
                {index === lines.length - 1 && optionArgs}
              </div>
            ))}
          </div>
          <Punct text={close} className="pl-1" />
        </>
      )}
    </div>
  )
}

function Punct({ text, className }: { text: string; className?: string }) {
  return <span className={cx('num text-[13px] text-ink-subtle select-none', className)}>{text}</span>
}

/** Argument indexes grouped so neighbouring leaves and empty inputs share a row; every operator gets its own. */
function rows(args: Slot[]): number[][] {
  const out: number[][] = []
  args.forEach((arg, index) => {
    const last = out.at(-1)
    if (arg?.kind !== 'op' && last && args[last[0]]?.kind !== 'op') last.push(index)
    else out.push([index])
  })
  return out
}

function Leaf({ node, path }: { node: Exclude<TemplateNode, OperatorNode>; path: Path }) {
  const ctx = useBuilder()
  const state = useDropState(path)
  const twin = node.kind === 'var' && ctx.hoverTag === `${node.name}#${node.tag}`
  const label = node.kind === 'num' ? String(node.value) : node.name

  return (
    <div
      data-drop={pathKey(path)}
      onPointerDown={(event) => ctx.begin(event, { from: 'canvas', path, label })}
      className={cx(
        'group relative inline-flex h-6 w-fit cursor-grab touch-none items-center gap-1 rounded-full border bg-surface-3 px-2 transition-colors select-none',
        state === 'over' ? 'border-primary/60 bg-primary/15' : state === 'fits' || twin ? 'border-primary/60' : 'border-hairline-strong',
      )}
    >
      {node.kind === 'num' ? (
        <input
          key={node.value}
          type="number"
          aria-label="Number"
          defaultValue={node.value}
          onBlur={(event) => {
            const value = Number(event.target.value)
            if (event.target.value !== '' && Number.isFinite(value) && value !== node.value) ctx.change(put(ctx.root, path, { kind: 'num', value }))
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') event.currentTarget.blur()
          }}
          className="num field-sizing-content min-w-6 [appearance:textfield] bg-transparent text-xs text-ink outline-none [&::-webkit-inner-spin-button]:appearance-none [&::-webkit-outer-spin-button]:appearance-none"
        />
      ) : (
        <span className={cx('num text-xs', node.kind === 'var' ? 'text-ink' : 'text-ink-muted')}>{label}</span>
      )}
      {node.kind === 'var' && <TagChip node={node} path={path} />}
      <button type="button" aria-label={`Remove ${label}`} className="absolute -top-1.5 -right-1.5 inline-flex size-4 items-center justify-center rounded-full border border-hairline-strong bg-surface-4 text-ink-subtle opacity-0 transition-opacity group-hover:opacity-100 hover:text-ink focus-visible:opacity-100"
        onClick={() => ctx.change(put(ctx.root, path, null))}
      >
        <XIcon className="size-2.5" />
      </button>
    </div>
  )
}

/** The letter on a variable: same letter, same value. Hovering lights up its twins; clicking relinks it. */
function TagChip({ node, path }: { node: VariableNode; path: Path }) {
  const ctx = useBuilder()
  const [open, setOpen] = useState(false)
  const tags = tagsOf(ctx.root, node.name)
  const free = nextTag(ctx.root, node.name)
  const values = node.name === 'FIELD' ? null : ctx.options?.variables[node.name]
  const relink = (tag: string) => {
    ctx.change(retag(ctx.root, path, tag))
    setOpen(false)
  }

  return (
    <Floating
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Popover.Trigger
          aria-label={`${node.name} ${node.tag}`}
          onMouseEnter={() => ctx.setHoverTag(`${node.name}#${node.tag}`)}
          onMouseLeave={() => ctx.setHoverTag(null)}
          className="num inline-flex size-5 items-center justify-center rounded-full bg-surface-4 text-[11px] text-ink-muted transition-colors hover:bg-primary/15 hover:text-ink"
        >
          {node.tag}
        </Popover.Trigger>
      }
    >
      <div className="flex w-56 flex-col gap-0.5 p-1">
        {tags.map((tag) => (
          <button
            key={tag}
            type="button"
            onClick={() => relink(tag)}
            className={cx('flex h-8 items-center justify-between gap-2 rounded-sm px-2 text-[13px] hover:bg-surface-4', tag === node.tag ? 'text-ink' : 'text-ink-muted')}
          >
            <span>Same as {tag}</span>
            {tag === node.tag && <CheckIcon className="size-3.5 text-primary" aria-hidden />}
          </button>
        ))}
        {free && (
          <button type="button" onClick={() => relink(free)} className="flex h-8 items-center rounded-sm px-2 text-[13px] text-ink-muted hover:bg-surface-4 hover:text-ink">
            {node.name === 'FIELD' ? 'Another Field' : 'Own Value'} ({free})
          </button>
        )}
        {values && <p className="border-t border-hairline px-2 pt-1.5 pb-1 num text-xs text-ink-tertiary">{values.join(' · ')}</p>}
      </div>
    </Floating>
  )
}

function BlockMenu({ node, path, close }: { node: OperatorNode; path: Path; close: () => void }) {
  const ctx = useBuilder()
  const first = ctx.blocks[node.ops[0]]
  const allowed = choices(node, ctx.blocks)
  const update = (next: OperatorNode) => ctx.change(put(ctx.root, path, next))

  const setOption = (key: string, value: number | boolean | undefined) => {
    const options = Object.fromEntries(Object.entries(node.options ?? {}).filter(([name]) => name !== key))
    if (value !== undefined) options[key] = value
    // Every operator in a block must take the options it sets.
    const ops = value === undefined ? node.ops : node.ops.filter((name) => key in (ctx.blocks[name]?.options ?? {}))
    const next: OperatorNode = { kind: 'op', ops: ops.length > 0 ? ops : node.ops, args: node.args }
    if (Object.keys(options).length > 0) next.options = options
    update(next)
  }

  return (
    <div className="flex w-80 flex-col gap-3 p-3">
      {allowed.length > 1 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-ink-muted">Operators</span>
          <Chips
            label="Operators"
            items={allowed.map((block) => ({ value: block.name, label: block.name, title: block.description ?? undefined }))}
            value={node.ops}
            onChange={(ops) => {
              if (ops.length > 0) update({ ...node, ops })
            }}
          />
          <span className="text-xs text-ink-tertiary">The lab tries each and keeps what gives the best Sharpe.</span>
        </div>
      )}
      {first && Object.keys(first.options).length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-medium text-ink-muted">Options</span>
          {Object.entries(first.options).map(([key, fallback]) => (
            <OptionInput key={key} name={key} fallback={fallback} value={node.options?.[key]} onChange={(value) => setOption(key, value)} />
          ))}
        </div>
      )}
      <div className="flex flex-wrap gap-2 border-t border-hairline pt-3">
        <Button
          size="sm"
          onClick={() => {
            ctx.change(unwrap(ctx.root, path, ctx.blocks))
            close()
          }}
        >
          Remove Block (Keep Input)
        </Button>
        <Button
          size="sm"
          variant="danger"
          onClick={() => {
            ctx.change(put(ctx.root, path, null))
            close()
          }}
        >
          Remove
        </Button>
      </div>
    </div>
  )
}

function OptionInput({
  name,
  fallback,
  value,
  onChange,
}: {
  name: string
  fallback: number | boolean
  value: number | boolean | undefined
  onChange: (value: number | boolean | undefined) => void
}) {
  if (typeof fallback === 'boolean') {
    return <Checkbox label={<span className="num">{name}</span>} checked={Boolean(value ?? fallback)} onChange={(e) => onChange(e.target.checked === fallback ? undefined : e.target.checked)} />
  }
  return (
    <label className="flex items-center justify-between gap-3 text-[13px] text-ink-muted">
      <span className="num">{name}</span>
      <Input
        type="number"
        className="w-24"
        placeholder={String(fallback)}
        value={typeof value === 'number' ? value : ''}
        onChange={(e) => {
          const number = Number(e.target.value)
          onChange(e.target.value === '' || !Number.isFinite(number) ? undefined : number)
        }}
      />
    </label>
  )
}

/** Every block that fits an empty input, searchable, for placing a block without dragging. */
function BlockSearch({ socket, onPick }: { socket: Socket; onPick: (node: TemplateNode) => void }) {
  const ctx = useBuilder()
  const sections = paletteSections(ctx.root, ctx.blocks, ctx.options)
    .map((section) => ({ ...section, items: section.items.filter((item) => accepts(socket, item.node, ctx.blocks)) }))
    .filter((section) => section.items.length > 0)

  return (
    <Command loop className="flex w-72 flex-col [&_[cmdk-group-heading]]:eyebrow [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pt-2 [&_[cmdk-group-heading]]:pb-1">
      <div className="flex items-center gap-2 border-b border-hairline px-2.5">
        <SearchIcon className="size-3.5 shrink-0 text-ink-subtle" aria-hidden />
        <Command.Input autoFocus placeholder="Search blocks…" className="h-9 flex-1 bg-transparent text-[13px] text-ink outline-none placeholder:text-ink-tertiary" />
      </div>
      <Command.List className="max-h-72 overflow-y-auto p-1">
        <Command.Empty className="px-3 py-4 text-center text-[13px] text-ink-subtle">No block fits here.</Command.Empty>
        {sections.map((section) => (
          <Command.Group key={section.heading} heading={section.heading}>
            {section.items.map((item) => (
              <Command.Item
                key={item.key}
                value={item.key}
                keywords={[item.label]}
                onSelect={() => onPick(item.node)}
                className="flex h-8 cursor-default items-center rounded-sm px-2 text-[13px] text-ink-muted select-none data-[selected=true]:bg-surface-4 data-[selected=true]:text-ink"
              >
                <span className="num">{item.label}</span>
              </Command.Item>
            ))}
          </Command.Group>
        ))}
      </Command.List>
    </Command>
  )
}
