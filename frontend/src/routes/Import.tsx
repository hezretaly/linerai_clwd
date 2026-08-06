import { useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { dateTime, money } from '../lib/format'
import type { IngestRun } from '../lib/types'
import { Badge, Button, Card, Empty, Spinner } from '../components/ui'
import { PageHeader } from '../components/dashboard/AppShell'

interface RunsPayload {
  runs: IngestRun[]
  source_url: string
  configured: boolean
  csv_columns: string
  detail: string
}

export function ImportPage() {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [openRun, setOpenRun] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['ingest'],
    queryFn: () => api.get<RunsPayload>('/api/ingest/runs'),
  })

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['ingest'] })
    void queryClient.invalidateQueries({ queryKey: ['inventory'] })
  }

  const scrape = useMutation({
    mutationFn: () => api.post<IngestRun>('/api/ingest/runs'),
    onSuccess: (run) => {
      setOpenRun(run.id)
      invalidate()
    },
  })
  const upload = useMutation({
    mutationFn: (file: File) => api.upload<IngestRun>('/api/ingest/csv', file),
    onSuccess: (run) => {
      setOpenRun(run.id)
      invalidate()
    },
  })
  const publish = useMutation({
    mutationFn: (runId: string) => api.post(`/api/ingest/runs/${runId}/publish`),
    onSuccess: invalidate,
  })

  const { data: run } = useQuery({
    queryKey: ['ingest', openRun],
    queryFn: () => api.get<IngestRun>(`/api/ingest/runs/${openRun}`),
    enabled: Boolean(openRun),
  })

  if (isLoading || !data) return <Spinner />

  const scrapeError = scrape.error as ApiError | null
  const notConfigured = scrapeError?.notConfigured

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

      <div className="space-y-6 p-6">
        <div className="grid gap-4 lg:grid-cols-2">
          <Card className="p-4">
            <h2 className="text-sm font-semibold">Crawl the dealer website</h2>
            {data.configured ? (
              <>
                <p className="mt-1 text-sm text-muted-foreground">{data.source_url}</p>
                <Button
                  variant="primary"
                  className="mt-3"
                  onClick={() => scrape.mutate()}
                  disabled={scrape.isPending}
                >
                  {scrape.isPending ? 'Crawling...' : 'Start a run'}
                </Button>
              </>
            ) : (
              <div className="mt-2 rounded-lg bg-warning-muted p-3">
                <p className="text-sm text-warning-foreground">
                  No dealer website is configured. Set{' '}
                  <code className="font-mono text-xs">SCRAPER_BASE_URL</code> to crawl a site.
                </p>
                <p className="mt-1 text-xs text-warning-foreground/80">
                  The CSV import on the right needs no configuration and works right now.
                </p>
              </div>
            )}
            {notConfigured && (
              <p className="mt-2 text-sm text-destructive">
                Missing: {notConfigured.missing.join(', ')}
              </p>
            )}
          </Card>

          <Card className="p-4">
            <h2 className="text-sm font-semibold">Upload a CSV</h2>
            <p className="mt-1 text-sm text-muted-foreground">
              Columns: <code className="font-mono text-xs">{data.csv_columns}</code>
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
          <Card>
            <header className="flex items-center justify-between border-b border-border px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold">
                  Review this run before it touches inventory
                </h2>
                <p className="text-xs text-muted-foreground">
                  {run.listings_found} listings found -- {run.created_count} new,{' '}
                  {run.updated_count} changed, {run.removed_count} gone
                </p>
              </div>
              <div className="flex items-center gap-2">
                <Badge tone={run.status === 'published' ? 'success' : 'warning'}>
                  {run.status}
                </Badge>
                {run.status === 'ready' && (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => publish.mutate(run.id)}
                    disabled={publish.isPending}
                  >
                    Publish to inventory
                  </Button>
                )}
              </div>
            </header>

            <div className="grid gap-4 p-4 lg:grid-cols-3">
              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  New ({run.diff.created?.length ?? 0})
                </h3>
                <ul className="mt-2 space-y-1 text-sm">
                  {(run.diff.created ?? []).map((item) => (
                    <li key={String(item.vin)}>
                      {String(item.year ?? '')} {String(item.make ?? '')}{' '}
                      {String(item.model ?? '')}
                      <span className="ml-1 text-muted-foreground">
                        {money(item.price as number | null)}
                      </span>
                    </li>
                  ))}
                  {!run.diff.created?.length && (
                    <li className="text-muted-foreground">None</li>
                  )}
                </ul>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Changed ({run.diff.updated?.length ?? 0})
                </h3>
                <ul className="mt-2 space-y-1.5 text-sm">
                  {(run.diff.updated ?? []).map((item) => (
                    <li key={item.vin}>
                      <p className="font-mono text-xs">{item.vin}</p>
                      {Object.entries(item.changes).map(([field, change]) => (
                        <p key={field} className="text-xs text-muted-foreground">
                          {field}: {String(change.from)} -&gt; {String(change.to)}
                        </p>
                      ))}
                      {item.protected?.length > 0 && (
                        <p className="text-xs text-primary">
                          Keeping your edit to {item.protected.join(', ')}
                        </p>
                      )}
                    </li>
                  ))}
                  {!run.diff.updated?.length && (
                    <li className="text-muted-foreground">None</li>
                  )}
                </ul>
              </section>

              <section>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Skipped ({run.errors.length})
                </h3>
                <ul className="mt-2 space-y-1 text-sm">
                  {run.errors.map((error, index) => (
                    <li key={index} className="text-muted-foreground">
                      <span className="text-destructive">{error.error}</span>
                      {error.url && (
                        <span className="block truncate text-xs">
                          {error.url.split('/').pop()}
                        </span>
                      )}
                    </li>
                  ))}
                  {!run.errors.length && <li className="text-muted-foreground">None</li>}
                </ul>
              </section>
            </div>
          </Card>
        )}

        <Card>
          <header className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">Run history</h2>
          </header>
          {data.runs.length === 0 ? (
            <Empty title="No imports yet" hint="Nothing has ever been imported." />
          ) : (
            <ul className="divide-y divide-border">
              {data.runs.map((item) => (
                <li key={item.id}>
                  <button
                    onClick={() => setOpenRun(item.id)}
                    className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors duration-150 hover:bg-muted"
                  >
                    <div>
                      <p className="text-sm font-medium">
                        {item.method || 'crawl'} -- {item.source_url}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {dateTime(item.started_at)} -- {item.listings_found} listings
                      </p>
                    </div>
                    <Badge
                      tone={
                        item.status === 'published'
                          ? 'success'
                          : item.status === 'failed'
                            ? 'destructive'
                            : 'warning'
                      }
                    >
                      {item.status}
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
