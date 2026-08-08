import { useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'
import { dateTime, money } from '../lib/format'
import type { AdfPreview, Lead, Prospect } from '../lib/types'
import { Badge, Button, Card, Empty, Field, Input } from '../components/ui'
import { Icon } from '../components/Icon'
import { PageIntro } from '../components/dashboard/AppShell'

/**
 * ADF/XML lead import.
 *
 * Preview then commit, the same shape as the inventory importer: parsing writes
 * nothing, and the dealer sees exactly what will be created -- including which
 * prospects already exist and which cars are actually on the lot -- before
 * anything touches the leads table.
 */

type Commit = { created: Lead[]; merged: Lead[] }

const BLANK = {
  name: '',
  email: '',
  phone: '',
  source: 'phone',
  vehicle_year: '',
  vehicle_make: '',
  vehicle_model: '',
  comments: '',
}

export function LeadImportPage() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)

  const [preview, setPreview] = useState<AdfPreview | null>(null)
  const [skipped, setSkipped] = useState<Set<number>>(new Set())
  const [result, setResult] = useState<Commit | null>(null)
  const [manual, setManual] = useState(BLANK)

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['leads'] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
  }

  const upload = useMutation({
    mutationFn: (file: File) => api.upload<AdfPreview>('/api/leads/import/adf/preview', file),
    onSuccess: (data) => {
      setPreview(data)
      setSkipped(new Set())
      setResult(null)
    },
  })

  const commit = useMutation({
    mutationFn: (prospects: Prospect[]) =>
      api.post<Commit>('/api/leads/import/adf', { prospects }),
    onSuccess: (data) => {
      setResult(data)
      setPreview(null)
      invalidate()
    },
  })

  const create = useMutation({
    mutationFn: () =>
      api.post<{ lead: Lead; merged: boolean }>('/api/leads', {
        ...manual,
        vehicle_year: manual.vehicle_year ? Number(manual.vehicle_year) : null,
      }),
    onSuccess: () => {
      setManual(BLANK)
      invalidate()
    },
  })

  const uploadError = upload.error as ApiError | null
  const createError = create.error as ApiError | null
  const commitError = commit.error as ApiError | null

  const selected = (preview?.prospects ?? []).filter((_, i) => !skipped.has(i))

  return (
    <main className="p-4 md:p-6">
      <PageIntro
        title="Import leads"
        subtitle="Take in the leads your marketplaces already send you, then reach out from here."
        actions={
          <Link to="/app/leads">
            <Button size="sm">Back to leads</Button>
          </Link>
        }
      />

      {/* The one thing a dealer will get wrong if we don't say it: nothing here
          runs on a timer. Better stated once, at the top, than discovered. */}
      <div className="mb-4 rounded-lg border border-warning/25 bg-warning-muted px-4 py-3">
        <p className="text-sm font-medium text-warning-foreground">
          Nothing on this page is scheduled.
        </p>
        <p className="mt-0.5 text-sm text-warning-foreground/85">
          There is no job runner in this system, so a follow-up or a reminder is a draft a rep
          reviews and sends from the lead drawer. It is not a drip campaign. Imports happen when
          you upload a file -- no inbox is polled and no feed is subscribed to.
        </p>
      </div>

      {/* Upload and its review stack in one column so the reviewed rows fill
          the space beside the manual form rather than pushing it up the page. */}
      <div className="grid items-start gap-4 lg:grid-cols-[1.6fr_1fr]">
        <div className="space-y-4">
        {/* ---- upload ---- */}
        <Card className="p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold">Upload an ADF/XML drop</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                ADF (Auto-lead Data Format) is what AutoTrader, CarGurus, Cars.com and most
                website forms post to a dealer's lead inbox. Drop the file here and it is parsed,
                matched against inventory and shown to you before anything is created.
              </p>
            </div>
            <Icon name="file" className="mt-0.5 size-5 shrink-0 text-muted-foreground" />
          </div>

          <input
            ref={fileInput}
            type="file"
            accept=".xml,.adf,text/xml,application/xml"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0]
              if (file) upload.mutate(file)
              event.target.value = ''
            }}
          />
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              variant="primary"
              onClick={() => fileInput.current?.click()}
              disabled={upload.isPending}
            >
              {upload.isPending ? 'Reading...' : 'Choose a file'}
            </Button>
            <a
              href="/api/leads/import/adf/sample"
              className="text-sm text-primary hover:underline"
              download
            >
              Download a sample ADF file
            </a>
          </div>

          {uploadError && (
            <p className="mt-3 rounded-md border border-primary/25 bg-accent px-3 py-2 text-sm text-primary">
              {uploadError.message}
            </p>
          )}
        </Card>

        {/* ---- review ---- */}
        {preview && (
          <Card>
            <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3">
              <div>
                <h2 className="text-sm font-semibold">Review before anything is created</h2>
                <p className="text-xs text-muted-foreground">
                  {preview.filename} -- {preview.found} prospects found, {selected.length} selected
                  {preview.errors.length ? `, ${preview.errors.length} unusable` : ''}
                </p>
            </div>
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => setPreview(null)}>
                Discard
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => commit.mutate(selected)}
                disabled={commit.isPending || selected.length === 0}
              >
                {commit.isPending ? 'Importing...' : `Import ${selected.length}`}
              </Button>
            </div>
          </header>

          {commitError && (
            <p className="border-b border-border px-4 py-2 text-sm text-primary">
              {commitError.message}
            </p>
          )}

          <ul className="divide-y divide-border">
            {preview.prospects.map((p, index) => (
              <ProspectRow
                key={index}
                prospect={p}
                skipped={skipped.has(index)}
                onToggle={() => {
                  const next = new Set(skipped)
                  if (next.has(index)) next.delete(index)
                  else next.add(index)
                  setSkipped(next)
                }}
              />
            ))}
          </ul>

          {preview.errors.length > 0 && (
            <div className="border-t border-border px-4 py-3">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Skipped ({preview.errors.length})
              </h3>
              <ul className="mt-1.5 space-y-1">
                {preview.errors.map((e, i) => (
                  <li key={i} className="text-sm text-muted-foreground">
                    <span className="text-destructive">Row {e.row}:</span> {e.error}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Card>
      )}

      {/* ---- result ---- */}
      {result && (
        <Card className="p-4">
          <h2 className="text-sm font-semibold">
            {result.created.length} created, {result.merged.length} merged into existing leads
          </h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Merging fills blanks only -- a name or number a rep corrected by hand survives a
            re-drop of the same feed.
          </p>
          <ul className="mt-3 flex flex-wrap gap-1.5">
            {[...result.created, ...result.merged].map((lead) => (
              <li key={lead.id}>
                <Badge tone="neutral">{lead.name}</Badge>
              </li>
            ))}
          </ul>
          <Link to="/app/leads">
            <Button variant="primary" className="mt-4">
              Open the leads table
            </Button>
          </Link>
        </Card>
      )}

      {!preview && !result && !upload.isPending && (
        <Card>
          <Empty
            title="Nothing uploaded yet"
            hint="Parsed prospects appear here for review before they become leads."
          />
        </Card>
      )}
        </div>

        {/* ---- manual ---- */}
        <Card className="p-4">
          <h2 className="text-sm font-semibold">Or enter one by hand</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            A walk-in, or a name off a voicemail. Same row, same follow-up.
          </p>
          <div className="mt-3 space-y-3">
            <Field label="Name">
              <Input
                value={manual.name}
                onChange={(e) => setManual({ ...manual, name: e.target.value })}
                placeholder="Jordan Ellis"
              />
            </Field>
            <div className="grid gap-3 sm:grid-cols-2">
              <Field label="Email">
                <Input
                  value={manual.email}
                  onChange={(e) => setManual({ ...manual, email: e.target.value })}
                  placeholder="jordan@example.com"
                />
              </Field>
              <Field label="Phone">
                <Input
                  value={manual.phone}
                  onChange={(e) => setManual({ ...manual, phone: e.target.value })}
                  placeholder="(555) 013-0100"
                />
              </Field>
            </div>
            <Field label="Where it came from">
              <select
                value={manual.source}
                onChange={(e) => setManual({ ...manual, source: e.target.value })}
                className="h-9 w-full rounded-md border border-input bg-background px-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring"
              >
                <option value="phone">Phone</option>
                <option value="website">Website form</option>
                <option value="chat">Website chat</option>
              </select>
            </Field>
            <div className="grid gap-3 sm:grid-cols-[5rem_1fr_1fr]">
              <Field label="Year">
                <Input
                  value={manual.vehicle_year}
                  inputMode="numeric"
                  onChange={(e) => setManual({ ...manual, vehicle_year: e.target.value })}
                  placeholder="2019"
                />
              </Field>
              <Field label="Make">
                <Input
                  value={manual.vehicle_make}
                  onChange={(e) => setManual({ ...manual, vehicle_make: e.target.value })}
                  placeholder="Kia"
                />
              </Field>
              <Field label="Model">
                <Input
                  value={manual.vehicle_model}
                  onChange={(e) => setManual({ ...manual, vehicle_model: e.target.value })}
                  placeholder="Sorento"
                />
              </Field>
            </div>
            <Field label="What they said">
              <textarea
                value={manual.comments}
                onChange={(e) => setManual({ ...manual, comments: e.target.value })}
                rows={2}
                placeholder="Wants a third row, can come Saturday."
                className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm shadow-xs placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              />
            </Field>
          </div>

          {createError && (
            <p className="mt-3 text-sm text-destructive">{createError.message}</p>
          )}
          {create.isSuccess && (
            <p className="mt-3 text-sm text-success">
              {create.data.merged ? 'Merged into an existing lead.' : 'Lead created.'}{' '}
              <button
                className="underline"
                onClick={() => navigate('/app/leads')}
              >
                Open the leads table
              </button>
            </p>
          )}

          <Button
            variant="primary"
            className="mt-3 w-full"
            onClick={() => create.mutate()}
            disabled={create.isPending}
          >
            {create.isPending ? 'Saving...' : 'Add lead'}
          </Button>
        </Card>
      </div>

    </main>
  )
}

function ProspectRow({
  prospect,
  skipped,
  onToggle,
}: {
  prospect: Prospect
  skipped: boolean
  onToggle: () => void
}) {
  return (
    <li className={clsx('flex gap-3 px-4 py-3', skipped && 'opacity-45')}>
      <input
        type="checkbox"
        checked={!skipped}
        onChange={onToggle}
        aria-label={`Import ${prospect.name}`}
        className="mt-1 size-4 shrink-0 accent-[var(--primary)]"
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="text-sm font-medium">{prospect.name || 'Unnamed prospect'}</span>
          {prospect.provider && (
            <span className="text-xs text-muted-foreground">via {prospect.provider}</span>
          )}
          {prospect.existing_lead && (
            <Badge tone="primary">Already on file -- will merge</Badge>
          )}
        </div>

        <p className="mt-0.5 truncate text-sm text-muted-foreground">
          {prospect.email || 'no email'}
          {prospect.phone ? ` -- ${prospect.phone}` : ''}
          {prospect.requested_at ? ` -- ${dateTime(prospect.requested_at)}` : ''}
        </p>

        {prospect.comments && (
          <p className="mt-1.5 border-l-2 border-border pl-2 text-sm">{prospect.comments}</p>
        )}

        <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
          {prospect.vehicle_label && (
            <span className="text-muted-foreground">
              Asked about <span className="text-foreground">{prospect.vehicle_label}</span>
            </span>
          )}
          {prospect.in_stock ? (
            <span className="inline-flex items-center gap-1 text-success">
              <Icon name="check" className="size-3.5" />
              In stock -- {prospect.in_stock.title} at {money(prospect.in_stock.price)}
            </span>
          ) : prospect.vehicle_label ? (
            <span className="text-muted-foreground">Not in inventory</span>
          ) : null}
          {prospect.in_stock && !prospect.in_stock.rules.discuss && (
            <Badge tone="primary">Do not discuss</Badge>
          )}
          {prospect.timeframe && (
            <span className="text-muted-foreground">Timeframe: {prospect.timeframe}</span>
          )}
        </div>

        {prospect.warnings.map((w) => (
          <p key={w} className="mt-1 text-xs text-warning-foreground">
            {w}
          </p>
        ))}
      </div>
    </li>
  )
}
