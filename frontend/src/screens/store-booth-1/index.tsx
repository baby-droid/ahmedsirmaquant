import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent, type FormEvent } from 'react'
import { toast } from 'sonner'
import { DatabaseIcon, ExternalLinkIcon, FilePlusIcon, SearchIcon, SparklesIcon, TrashIcon, UploadCloudIcon } from 'lucide-react'
import { Badge, Button, Empty, Field, Input, Metric, Page, PageHeader, Panel, Notice, cx } from '@/ui/kit'

const MEMORY_KEY = 'quant-ai-store-booth-1'
const TEXT_EXTENSIONS = new Set(['.csv', '.json', '.md', '.mdx', '.txt', '.tsv', '.yaml', '.yml'])

type StoredSource = {
  id: string
  name: string
  kind: 'file' | 'url'
  mime: string
  size: number | null
  addedAt: string
  words: number
  summary: string
  status: 'indexed' | 'metadata'
}

const bytes = (size: number | null) => {
  if (size == null) return 'Source link'
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

const extension = (name: string) => {
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot).toLowerCase() : ''
}

const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(value))

async function analyzeFile(file: File): Promise<StoredSource> {
  const ext = extension(file.name)
  const textCapable = file.type.startsWith('text/') || TEXT_EXTENSIONS.has(ext)
  let text = ''

  if (textCapable) {
    text = (await file.text()).trim()
  }

  const words = text ? text.split(/\s+/).filter(Boolean).length : 0
  const summary = text
    ? text.replace(/\s+/g, ' ').slice(0, 180) + (text.length > 180 ? '…' : '')
    : `${file.name} is indexed by filename and metadata. Content extraction for this format will be added next.`

  return {
    id: `${file.name}-${file.lastModified}-${file.size}`,
    name: file.name,
    kind: 'file',
    mime: file.type || ext.slice(1).toUpperCase() || 'FILE',
    size: file.size,
    addedAt: new Date().toISOString(),
    words,
    summary,
    status: textCapable ? 'indexed' : 'metadata',
  }
}

export function StoreBoothScreen() {
  const [sources, setSources] = useState<StoredSource[]>([])
  const [query, setQuery] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    try {
      const saved = window.localStorage.getItem(MEMORY_KEY)
      if (saved) setSources(JSON.parse(saved) as StoredSource[])
    } catch {
      // A malformed local entry should not prevent the booth from opening.
      window.localStorage.removeItem(MEMORY_KEY)
    }
  }, [])

  useEffect(() => {
    window.localStorage.setItem(MEMORY_KEY, JSON.stringify(sources))
  }, [sources])

  const filtered = useMemo(
    () => sources.filter((source) => `${source.name} ${source.summary}`.toLowerCase().includes(query.toLowerCase().trim())),
    [query, sources],
  )
  const indexedWords = sources.reduce((total, source) => total + source.words, 0)

  const addFiles = async (files: File[]) => {
    if (!files.length) return
    setBusy(true)
    try {
      const analyzed = await Promise.all(files.map(analyzeFile))
      setSources((current) => [...analyzed.reverse(), ...current.filter((source) => !analyzed.some((item) => item.id === source.id))])
      toast.success(`${analyzed.length} source${analyzed.length === 1 ? '' : 's'} added to memory`)
    } catch {
      toast.error('The selected files could not be analyzed')
    } finally {
      setBusy(false)
    }
  }

  const onPick = (event: ChangeEvent<HTMLInputElement>) => {
    void addFiles(Array.from(event.target.files ?? []))
    event.target.value = ''
  }

  const onDrop = (event: DragEvent<HTMLLabelElement>) => {
    event.preventDefault()
    setDragging(false)
    void addFiles(Array.from(event.dataTransfer.files))
  }

  const addUrl = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const value = sourceUrl.trim()
    if (!value) return
    try {
      const parsed = new URL(value)
      const item: StoredSource = {
        id: `url-${value}`,
        name: parsed.hostname + parsed.pathname,
        kind: 'url',
        mime: 'URL',
        size: null,
        addedAt: new Date().toISOString(),
        words: 0,
        summary: 'Source link saved for future analysis. The booth does not fetch remote content yet.',
        status: 'metadata',
      }
      setSources((current) => [item, ...current.filter((source) => source.id !== item.id)])
      setSourceUrl('')
      toast.success('Source link saved')
    } catch {
      toast.error('Enter a complete source URL, such as https://example.com')
    }
  }

  const removeSource = (id: string) => {
    setSources((current) => current.filter((source) => source.id !== id))
    toast.success('Source removed from memory')
  }

  return (
    <Page>
      <PageHeader
        title={
          <span className="flex items-center gap-2">
            <DatabaseIcon className="size-5 text-[#c084fc]" aria-hidden />
            STORE BOOTH 1
          </span>
        }
        description="Add the files, documents, and source links QUANT AI should remember for future work."
        actions={<Badge tone="outline">{sources.length} {sources.length === 1 ? 'ITEM' : 'ITEMS'} IN MEMORY</Badge>}
      />

      <Notice title="Local source memory">
        Files are analyzed in your browser and stored in this workspace on this device. Nothing is uploaded or shared yet.
      </Notice>

      <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
        <Metric label="Sources" value={sources.length} hint="Files and links saved" />
        <Metric label="Text indexed" value={indexedWords ? indexedWords.toLocaleString() : '—'} hint="Words ready for context" tone={indexedWords ? 'profit' : 'neutral'} />
        <Metric label="Files" value={sources.filter((source) => source.kind === 'file').length} hint="From local storage" />
        <Metric label="Links" value={sources.filter((source) => source.kind === 'url').length} hint="Saved for later" />
      </div>

      <div className="grid gap-3 xl:grid-cols-[minmax(0,1.15fr)_minmax(380px,0.85fr)]">
        <Panel title="Add files and documents" description="Drop files here or choose them from your local device.">
          <label
            htmlFor="source-files"
            onDragOver={(event) => {
              event.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={onDrop}
            className={cx(
              'flex min-h-56 cursor-pointer flex-col items-center justify-center rounded-lg border border-dashed px-6 text-center transition-colors',
              dragging ? 'border-primary bg-primary/10' : 'border-hairline-strong bg-surface-2 hover:border-primary/60 hover:bg-surface-3',
            )}
          >
            <span className="flex size-11 items-center justify-center rounded-lg bg-[#8b5cf6]/15 text-[#c084fc]">
              {busy ? <SparklesIcon className="size-5 animate-pulse" aria-hidden /> : <UploadCloudIcon className="size-5" aria-hidden />}
            </span>
            <span className="mt-3 text-[14px] font-medium text-ink">{busy ? 'Analyzing your sources…' : 'Drop files to add them to memory'}</span>
            <span className="mt-1 max-w-sm text-xs leading-5 text-ink-subtle">PDF, DOCX, TXT, CSV, JSON, Markdown, spreadsheets, images, and more.</span>
            <span className="mt-4 inline-flex h-8 items-center gap-1.5 rounded-md border border-hairline-strong bg-surface-1 px-3 text-xs font-medium text-ink hover:bg-surface-3">
              <FilePlusIcon className="size-3.5" /> Choose files
            </span>
            <input ref={inputRef} id="source-files" type="file" multiple className="sr-only" onChange={onPick} />
          </label>
        </Panel>

        <Panel title="Save a source link" description="Keep a URL nearby for the next analysis pass.">
          <form onSubmit={addUrl} className="flex flex-col gap-3">
            <Field label="Source URL" hint="Remote content is saved as a reference and not fetched yet.">
              <Input value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://example.com/research-note" type="url" />
            </Field>
            <Button type="submit" className="self-start">
              <ExternalLinkIcon /> Save source
            </Button>
          </form>
          <div className="mt-6 rounded-md border border-hairline bg-surface-2 p-3">
            <div className="flex items-center gap-2 text-xs font-medium text-ink">
              <SparklesIcon className="size-3.5 text-[#c084fc]" aria-hidden />
              How memory works
            </div>
            <p className="mt-1.5 text-xs leading-5 text-ink-subtle">Text files are read and indexed immediately. Other formats keep their metadata until deeper extraction is added.</p>
          </div>
        </Panel>
      </div>

      <Panel
        title="Stored memory"
        description="Everything added in this booth stays available for future QUANT AI features."
        actions={
          <div className="relative w-full sm:w-56">
            <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-ink-tertiary" aria-hidden />
            <Input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Filter memory…" className="pl-8" aria-label="Filter stored memory" />
          </div>
        }
      >
        {filtered.length === 0 ? (
          <Empty icon={<DatabaseIcon />} title={sources.length ? 'No matching sources' : 'Your source memory is empty'}>
            {sources.length ? 'Try a different filter.' : 'Add a file, document, or source link above to give QUANT AI its first context.'}
          </Empty>
        ) : (
          <div className="divide-y divide-hairline">
            {filtered.map((source) => (
              <article key={source.id} className="flex flex-wrap items-start gap-3 py-3 first:pt-0 last:pb-0">
                <div className="flex size-8 shrink-0 items-center justify-center rounded-md bg-surface-3 text-ink-subtle">
                  {source.kind === 'url' ? <ExternalLinkIcon className="size-4" aria-hidden /> : <FilePlusIcon className="size-4" aria-hidden />}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="truncate text-[13px] font-medium text-ink">{source.name}</h3>
                    <Badge tone={source.status === 'indexed' ? 'profit' : 'muted'}>{source.status === 'indexed' ? 'INDEXED' : 'METADATA'}</Badge>
                  </div>
                  <p className="mt-1 max-w-3xl text-xs leading-5 text-ink-subtle">{source.summary}</p>
                  <p className="mt-1 text-[11px] text-ink-tertiary">
                    {source.mime} · {bytes(source.size)} · added {formatDate(source.addedAt)}
                    {source.words ? ` · ${source.words.toLocaleString()} words` : ''}
                  </p>
                </div>
                <Button variant="ghost" size="icon-sm" aria-label={`Remove ${source.name}`} title={`Remove ${source.name}`} onClick={() => removeSource(source.id)}>
                  <TrashIcon />
                </Button>
              </article>
            ))}
          </div>
        )}
      </Panel>
    </Page>
  )
}