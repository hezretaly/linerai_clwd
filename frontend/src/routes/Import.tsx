import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { dateTime, money, relative } from '../lib/format'
import type { IngestRun } from '../lib/types'
import { Badge, Button, Card, Empty, Spinner, Switch } from '../components/ui'
import { PageHeader } from '../components/dashboard/AppShell'

interface RunsPayload {
  runs: IngestRun[]
  source_url: string
  configured: boolean
  csv_columns: string
  detail: string
  /** A refresh still reading the site, if one is. */
  running: IngestRun | null
  /** Whether this site needs the headless browser, and why it cannot run if it cannot. */
  browser: { needed: boolean; problem: string }
  /** Whether "also read each car's own page" means anything for this site. */
  details: { supported: boolean }
}

/** What publishing would take off sale, when it is enough to ask first. */
interface RemovalRisk {
  error: 'removals'
  removed: number
  on_sale: number
  share: number
  why: string[]
}

/** The words a manager reads for each field a refresh can change. */
const FIELD: Record<string, string> = {
  price: 'price',
  mileage: 'mileage',
  year: 'year',
  make: 'make',
  model: 'model',
  trim: 'trim',
  body_style: 'body style',
  seats: 'seats',
  photo_url: 'photo',
  listing_url: 'link to their page',
  features: 'options',
  advertised_price: 'advertised price',
  vehicle_history_url: 'Carfax link',
  stock_number: 'stock number',
  fuel_type: 'fuel',
  drivetrain: 'drive',
  transmission: 'transmission',
  exterior_color: 'colour',
  interior_color: 'interior',
  engine: 'engine',
  location: 'lot',
  dealer_phone: 'phone',
  doc_fee: 'doc fee',
}

function hostOf(url: string): string {
  try {
    return new URL(url).host.replace(/^www\./, '')
  } catch {
    return url
  }
}

/** One value, the way a person would say it -- a link is "updated", not a URL. */
function say(field: string, value: unknown): string {
  if (value === null || value === undefined || value === '') return 'none'
  if (field === 'price' || field === 'advertised_price') {
    const n = Number(value)
    return Number.isFinite(n) ? money(n) : String(value)
  }
  if (field === 'mileage') {
    const n = Number(value)
    return Number.isFinite(n) ? `${n.toLocaleString()} mi` : String(value)
  }
  return String(value)
}

/** A change, in words. Links and photos say "updated"; the options list says
 *  how many; the extra details list only the parts that moved. */
function describe(field: string, from: unknown, to: unknown): string[] {
  if (field === 'features') {
    const a = Array.isArray(from) ? from.length : 0
    const b = Array.isArray(to) ? to.length : 0
    return [`options: ${a} -> ${b}`]
  }
  if (field === 'raw') {
    const before = (from && typeof from === 'object' ? from : {}) as Record<string, unknown>
    const after = (to && typeof to === 'object' ? to : {}) as Record<string, unknown>
    return Object.keys(after)
      .filter((key) => before[key] !== after[key])
      .map((key) => describe(key, before[key], after[key])[0])
  }
  const label = FIELD[field] ?? field.replace(/_/g, ' ')
  if (field.endsWith('_url')) return [`${label}: ${from ? 'updated' : 'added'}`]
  return [`${label}: ${say(field, from)} -> ${say(field, to)}`]
}

function sourceOf(run: IngestRun): string {
  if (run.method === 'csv') return 'CSV upload'
  const site = hostOf(run.source_url)
  return run.method.endsWith('+details') ? `${site}, with each car's page` : site
}

const STATUS: Record<IngestRun['status'], string> = {
  pending: 'reading...',
  ready: 'ready to review',
  published: 'applied',
  failed: 'failed',
}

export function ImportPage() {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)
  const [details, setDetails] = useState(false)
  const [risk, setRisk] = useState<RemovalRisk | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['ingest'],
    queryFn: () => api.get<RunsPayload>('/api/ingest/runs'),
    // A refresh runs on the server whether or not this page is open, so the
    // page asks again while one is running rather than waiting on a request.
    refetchInterval: (query) => (query.state.data?.running ? 4000 : false),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['ingest'] })
    void queryClient.invalidateQueries({ queryKey: ['inventory'] })
  }

  const scrape = useMutation({
    mutationFn: () => api.post<IngestRun>('/api/ingest/runs', details ? { details: true } : undefined),
    onSuccess: (run) => {
      setOpenRun(run.id)
      setRisk(null)
      invalidate()
    },
  })
  const upload = useMutation({
    mutationFn: (file: File) => api.upload<IngestRun>('/api/ingest/csv', file),
    onSuccess: (run) => {
      setOpenRun(run.id)
      setRisk(null)
      invalidate()
    },
  })
  const publish = useMutation({
    mutationFn: ({ runId, allowRemovals }: { runId: string; allowRemovals: boolean }) =>
      api.post(`/api/ingest/runs/${runId}/publish`, allowRemovals ? { allow_removals: true } : undefined),
    onSuccess: () => {
      setRisk(null)
      invalidate()
    },
    onError: (err: unknown) => {
      const payload = (err as ApiError)?.payload as { detail?: RemovalRisk } | undefined
      if ((err as ApiError)?.status === 409 && payload?.detail?.error === 'removals') setRisk(payload.detail)
    },
  })

  const shown = openRun ?? data?.running?.id ?? null
  const { data: run } = useQuery({
    queryKey: ['ingest', shown],
    queryFn: () => api.get<IngestRun>(`/api/ingest/runs/${shown}`),
    enabled: Boolean(shown),
    refetchInterval: (query) => (query.state.data?.status === 'pending' ? 3000 : false),
  })

  // When a refresh finishes, the list and the inventory both changed.
  const lastStatus = useRef<string | undefined>(undefined)
  useEffect(() => {
    if (lastStatus.current === 'pending' && run?.status && run.status !== 'pending') invalidate()
    lastStatus.current = run?.status
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.status])

  if (isLoading || !data) return <Spinner />

  const scrapeError = scrape.error as ApiError | null
  const notConfigured = scrapeError?.notConfigured
  const alreadyRunning = scrapeError?.status === 409
  const host = hostOf(data.source_url)
  const busy = scrape.isPending || Boolean(data.running)
  const publishError = publish.error as ApiError | null

  return (
    <>
      <PageHeader
        title="Import inventory"
        subtitle={data.detail}
        actions={
          <Link to="/app/inventory">
            <Button size="sm">Back to inventory</Button>
          </Link>
        }
      />

      <div className="space-y-6 p-4 sm:p-6">
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Card className="min-w-0 p-4">
            <h2 className="text-sm font-semibold">Refresh from your website</h2>
            {data.configured ? (
              <>
                <p className="mt-1 text-sm text-muted-foreground">
                  Reads the cars listed on {host} and shows you every change here before
                  anything in your inventory is updated.
                </p>
                {data.browser.problem && (
                  <p className="mt-2 rounded-lg bg-warning-muted p-3 text-sm text-warning-foreground">
                    This server cannot read {host} yet: {data.browser.problem}
                  </p>
                )}
                <Button
                  variant="primary"
                  className="mt-3"
                  onClick={() => scrape.mutate()}
                  disabled={busy || Boolean(data.browser.problem)}
                >
                  {busy ? `Reading ${host}...` : `Refresh from ${host}`}
                </Button>
                {data.running && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Started {relative(data.running.started_at)}. This page updates by itself when
                    it finishes, and it keeps going if you leave.
                  </p>
                )}
                {alreadyRunning && !data.running && (
                  <p className="mt-2 text-xs text-muted-foreground">A refresh is already running.</p>
                )}
                {data.details.supported && (
                  <details className="mt-4 min-w-0 rounded-md border border-border">
                    <summary className="cursor-pointer px-3 py-2 text-sm font-medium">Advanced</summary>
                    <div className="flex items-start gap-3 border-t border-border p-3">
                      <Switch
                        checked={details}
                        onChange={setDetails}
                        label="Also read each car's own page"
                      />
                      <div className="min-w-0 text-sm">
                        Also read each car&apos;s own page
                        <span className="block text-xs text-muted-foreground">
                          Brings in each car&apos;s full options list. One page per car, so it
                          takes a few minutes. Prices are kept current either way.
                        </span>
                      </div>
                    </div>
                  </details>
                )}
              </>
            ) : (
              <div className="mt-2 rounded-lg bg-warning-muted p-3">
                <p className="text-sm text-warning-foreground">
                  No website is set up for this dealership yet.
                </p>
                <p className="mt-1 text-xs text-warning-foreground/80">
                  Uploading a CSV on the right needs no setup and works right now.
                </p>
              </div>
            )}
            {notConfigured && (
              <p className="mt-2 text-sm text-primary">Missing: {notConfigured.missing.join(', ')}</p>
            )}
          </Card>

          <Card className="min-w-0 p-4">
            <h2 className="text-sm font-semibold">Upload a CSV</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Columns:{' '}
              <code className="break-all font-mono text-xs">{data.csv_columns}</code>
            </p>
            <input
              ref={fileInput}
              type="file"
              accept=".csv,text/csv"
              className="hidden"
              onChange={(event) => {
                const file = event.target.files?.[0]
                if (file) upload.mutate(file)
                event.target.value = ''
              }}
            />
            <Button
              className="mt-3"
              onClick={() => fileInput.current?.click()}
              disabled={upload.isPending}
            >
              {upload.isPending ? 'Reading...' : 'Choose a file'}
            </Button>
          </Card>
        </div>

        {run && (
          <Card className="min-w-0">
            <header className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
              <div className="min-w-0">
                <h2 className="text-sm font-semibold">
                  {run.status === 'pending'
                    ? `Reading ${hostOf(run.source_url)}...`
                    : 'Review these changes before they reach your inventory'}
                </h2>
                {run.status !== 'pending' && (
                  <p className="text-xs text-muted-foreground">
                    {run.listings_found} cars found: {run.created_count} new, {run.updated_count}{' '}
                    changed, {run.removed_count} going off sale
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={run.status === 'published' ? 'success' : 'warning'}>
                  {STATUS[run.status]}
                </Badge>
                {run.status === 'ready' && (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => publish.mutate({ runId: run.id, allowRemovals: false })}
                    disabled={publish.isPending}
                  >
                    Apply these changes
                  </Button>
                )}
              </div>
            </header>

            {run.status === 'pending' ? (
              <div className="flex items-center gap-3 p-4 text-sm text-muted-foreground">
                <Spinner />
                Started {relative(run.started_at)}. Nothing changes until you review it here.
              </div>
            ) : (
              <>
                {risk && run.status === 'ready' && (
                  <div className="m-4 rounded-lg bg-warning-muted p-3 text-sm text-warning-foreground">
                    <p>
                      This would take {risk.removed} of {risk.on_sale} cars off sale (
                      {Math.round(risk.share * 100)}%). A car the refresh did not see looks the
                      same as one that sold.
                    </p>
                    {risk.why.map((reason) => (
                      <p key={reason} className="mt-1 text-xs">
                        {reason[0].toUpperCase() + reason.slice(1)}.
                      </p>
                    ))}
                    <Button
                      size="sm"
                      className="mt-2"
                      onClick={() => publish.mutate({ runId: run.id, allowRemovals: true })}
                      disabled={publish.isPending}
                    >
                      They really sold -- apply anyway
                    </Button>
                  </div>
                )}
                {publishError && !risk && (
                  <p className="m-4 text-sm text-destructive">{publishError.message}</p>
                )}
                <div className="grid grid-cols-1 gap-4 p-4 lg:grid-cols-2">
                  <section className="min-w-0">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      New ({run.diff.created?.length ?? 0})
                    </h3>
                    <ul className="mt-2 space-y-1 text-sm">
                      {(run.diff.created ?? []).map((item) => (
                        <li key={String(item.vin)} className="truncate">
                          {String(item.year ?? '')} {String(item.make ?? '')} {String(item.model ?? '')}
                          <span className="ml-1 text-muted-foreground">
                            {item.price ? money(item.price as number) : 'no price listed'}
                          </span>
                        </li>
                      ))}
                      {!run.diff.created?.length && <li className="text-muted-foreground">None</li>}
                    </ul>
                  </section>

                  <section className="min-w-0">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Going off sale ({run.diff.removed?.length ?? 0})
                    </h3>
                    <ul className="mt-2 space-y-1 text-sm">
                      {(run.diff.removed ?? []).map((item) => (
                        <li key={item.vin} className="truncate">
                          {item.title}
                          <span className="ml-1 text-xs text-muted-foreground">no longer listed</span>
                        </li>
                      ))}
                      {!run.diff.removed?.length && <li className="text-muted-foreground">None</li>}
                    </ul>
                  </section>

                  <section className="min-w-0">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Changed ({run.diff.updated?.length ?? 0})
                    </h3>
                    <ul className="mt-2 space-y-1.5 text-sm">
                      {(run.diff.updated ?? []).map((item) => (
                        <li key={item.vin} className="min-w-0">
                          <p className="truncate font-mono text-xs">{item.vin}</p>
                          {Object.entries(item.changes).flatMap(([field, change]) =>
                            describe(field, change.from, change.to).map((line) => (
                              <p key={`${field}-${line}`} className="truncate text-xs text-muted-foreground">
                                {line}
                              </p>
                            )),
                          )}
                          {item.protected?.length > 0 && (
                            <p className="text-xs text-primary">
                              Keeping your edit to {item.protected.join(', ')}
                            </p>
                          )}
                        </li>
                      ))}
                      {!run.diff.updated?.length && <li className="text-muted-foreground">None</li>}
                    </ul>
                  </section>

                  <section className="min-w-0">
                    <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                      Problems ({run.errors.length})
                    </h3>
                    <ul className="mt-2 space-y-1 text-sm">
                      {run.errors.map((error, index) => (
                        <li key={index} className="min-w-0 text-muted-foreground">
                          <span className="text-destructive">{error.error}</span>
                          {error.url && (
                            <span className="block truncate text-xs">{error.url.split('/').pop()}</span>
                          )}
                        </li>
                      ))}
                      {!run.errors.length && <li className="text-muted-foreground">None</li>}
                    </ul>
                  </section>
                </div>
              </>
            )}
          </Card>
        )}

        <Card className="min-w-0">
          <header className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">Past refreshes and uploads</h2>
          </header>
          {data.runs.length === 0 ? (
            <Empty title="No imports yet" hint="Nothing has ever been imported." />
          ) : (
            <ul className="divide-y divide-border">
              {data.runs.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => {
                      setOpenRun(item.id)
                      setRisk(null)
                    }}
                    className="flex w-full min-w-0 items-center justify-between gap-2 px-4 py-3 text-left transition-colors duration-150 hover:bg-muted"
                  >
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{sourceOf(item)}</p>
                      <p className="text-xs text-muted-foreground">
                        {dateTime(item.started_at)} -- {item.listings_found} cars
                      </p>
                    </div>
                    <Badge
                      tone={
                        item.status === 'published'
                          ? 'success'
                          : item.status === 'failed'
                            ? 'primary'
                            : 'warning'
                      }
                    >
                      {STATUS[item.status]}
                    </Badge>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  )
}
