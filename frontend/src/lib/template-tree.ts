/**
 * Template Lab's block tree: the same JSON the backend stores, checks and searches. Pure
 * functions, so the builder swaps one tree for the next and undo is a stack of trees.
 *
 * A block is addressed by the path of input indices from the root: `[]` is the root and
 * `[0, 1]` the second input of the root's first input.
 */

export type Socket = 'signal' | 'lookback' | 'group'
export type VariableName = 'FIELD' | 'LOOKBACK' | 'FAST_LOOKBACK' | 'SLOW_LOOKBACK' | 'GROUP' | 'WEIGHT' | 'POWER'

export interface OperatorNode {
  kind: 'op'
  /** More than one makes a choice block: the search tries each. */
  ops: string[]
  args: Slot[]
  options?: Record<string, number | boolean>
}
export interface VariableNode {
  kind: 'var'
  name: VariableName
  /** Blocks with the same name and tag take the same value in an Alpha. */
  tag: string
}
export type TemplateNode = OperatorNode | VariableNode | { kind: 'data'; name: string } | { kind: 'num'; value: number }
export type Slot = TemplateNode | null
export type Path = readonly number[]

export interface TemplateDoc {
  version: 1
  root: Slot
}

export interface BlockInfo {
  name: string
  category: string
  inputs: Socket[]
  options: Record<string, number | boolean>
  symbol: string | null
  /** What it gives; an operator that makes a group fits only group inputs. */
  output?: Socket
  description?: string | null
}

export type Blocks = Record<string, BlockInfo>

export const TAGS = ['A', 'B', 'C', 'D'] as const
export const GROUP_FIELDS = ['market', 'sector', 'industry', 'subindustry']
export const LOOKBACK_VARIABLES: readonly VariableName[] = ['LOOKBACK', 'FAST_LOOKBACK', 'SLOW_LOOKBACK']

export function at(root: Slot, path: Path): Slot {
  let node: Slot = root
  for (const index of path) {
    if (node === null || node.kind !== 'op') return null
    node = node.args[index] ?? null
  }
  return node
}

export function put(root: Slot, path: Path, next: Slot): Slot {
  if (path.length === 0) return next
  if (root === null || root.kind !== 'op') return root
  const [index, ...rest] = path
  const args = root.args.slice()
  args[index] = put(root.args[index] ?? null, rest, next)
  return { ...root, args }
}

/** Whether `path` is `ancestor` itself or lies inside it. */
export function within(path: Path, ancestor: Path): boolean {
  return ancestor.length <= path.length && ancestor.every((index, i) => path[i] === index)
}

export function samePath(a: Path | null, b: Path | null): boolean {
  return a !== null && b !== null && a.length === b.length && a.every((index, i) => index === b[i])
}

export const pathKey = (path: Path): string => (path.length ? path.join('.') : 'root')
export const keyPath = (key: string): Path => (key === 'root' ? [] : key.split('.').map(Number))

/**
 * Moves a placed block onto another slot: an empty slot takes it, a filled one swaps with
 * it, and dropping a block on its own ancestor replaces that ancestor. `undefined` when the
 * target is the block itself or lies inside it.
 */
export function move(root: Slot, from: Path, to: Path): Slot | undefined {
  const node = at(root, from)
  if (node === null || within(to, from)) return undefined
  if (within(from, to)) return put(root, to, node)
  return put(put(root, from, at(root, to)), to, node)
}

export function socketAt(root: Slot, path: Path, blocks: Blocks): Socket {
  if (path.length === 0) return 'signal'
  const parent = at(root, path.slice(0, -1))
  if (parent === null || parent.kind !== 'op') return 'signal'
  return blocks[parent.ops[0]]?.inputs[path[path.length - 1]] ?? 'signal'
}

/** Whether a block may go in an input: fields and operators are signals, lookbacks and groups have their own. */
export function accepts(socket: Socket, node: TemplateNode, blocks: Blocks): boolean {
  if (node.kind === 'var') {
    if (node.name === 'GROUP') return socket === 'group'
    if (node.name === 'FIELD' || node.name === 'WEIGHT' || node.name === 'POWER') return socket === 'signal'
    return socket === 'lookback'
  }
  if (node.kind === 'data') return GROUP_FIELDS.includes(node.name) ? socket === 'group' : socket === 'signal'
  if (node.kind === 'num') return socket === 'signal' || (socket === 'lookback' && Number.isInteger(node.value) && node.value > 0)
  return socket === (blocks[node.ops[0]]?.output ?? 'signal')
}

export function blank(block: BlockInfo): OperatorNode {
  return { kind: 'op', ops: [block.name], args: block.inputs.map(() => null) }
}

/** Drops a new block on a slot. An operator dropped on a filled slot wraps what was there. */
export function drop(root: Slot, path: Path, node: TemplateNode, blocks: Blocks): Slot {
  const current = at(root, path)
  if (current !== null && node.kind === 'op') {
    const inputs = blocks[node.ops[0]]?.inputs ?? []
    const index = inputs.findIndex((socket, i) => node.args[i] === null && accepts(socket, current, blocks))
    if (index >= 0) {
      const args = node.args.slice()
      args[index] = current
      return put(root, path, { ...node, args })
    }
  }
  return put(root, path, node)
}

/** Removes an operator block, keeping the first of its inputs that fits where it was. */
export function unwrap(root: Slot, path: Path, blocks: Blocks): Slot {
  const node = at(root, path)
  if (node === null || node.kind !== 'op') return put(root, path, null)
  const socket = socketAt(root, path, blocks)
  const keep = node.args.find((child): child is TemplateNode => child !== null && accepts(socket, child, blocks)) ?? null
  return put(root, path, keep)
}

export function sameInputs(a: readonly Socket[], b: readonly Socket[]): boolean {
  return a.length === b.length && a.every((socket, i) => socket === b[i])
}

/** Operators that may share a choice block with this one: the same inputs, and every option it sets. */
export function choices(node: OperatorNode, blocks: Blocks): BlockInfo[] {
  const first = blocks[node.ops[0]]
  if (!first) return []
  const set = Object.keys(node.options ?? {})
  return Object.values(blocks).filter((block) => sameInputs(block.inputs, first.inputs) && set.every((key) => key in block.options))
}

export function walk(root: Slot, visit: (node: TemplateNode, path: Path) => void, path: Path = []): void {
  if (root === null) return
  visit(root, path)
  if (root.kind === 'op') root.args.forEach((child, index) => walk(child, visit, [...path, index]))
}

export function count(root: Slot): number {
  let total = 0
  walk(root, () => {
    total += 1
  })
  return total
}

export function holes(root: Slot, path: Path = []): Path[] {
  if (root === null) return [path]
  if (root.kind !== 'op') return []
  return root.args.flatMap((child, index) => holes(child, [...path, index]))
}

/** The first empty slot a block fits, so clicking a palette block places it. */
export function firstHole(root: Slot, node: TemplateNode, blocks: Blocks): Path | null {
  return holes(root).find((path) => accepts(socketAt(root, path, blocks), node, blocks)) ?? null
}

export function tagsOf(root: Slot, name: VariableName): string[] {
  const found = new Set<string>()
  walk(root, (node) => {
    if (node.kind === 'var' && node.name === name) found.add(node.tag)
  })
  return TAGS.filter((tag) => found.has(tag))
}

export function nextTag(root: Slot, name: VariableName): string | null {
  const used = new Set(tagsOf(root, name))
  return TAGS.find((tag) => !used.has(tag)) ?? null
}

/** A new variable block, linked to the first tag of its name already in the template. */
export function variable(root: Slot, name: VariableName): VariableNode {
  return { kind: 'var', name, tag: tagsOf(root, name)[0] ?? 'A' }
}

export function retag(root: Slot, path: Path, tag: string): Slot {
  const node = at(root, path)
  return node?.kind === 'var' ? put(root, path, { ...node, tag }) : root
}
