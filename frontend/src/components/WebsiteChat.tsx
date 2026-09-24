import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../lib/api'
import { relative } from '../lib/format'
import { Badge, Button, Card } from './ui'
import { Icon } from './Icon'

/* The chat bubble on the dealership's own website: the tag to install, the
 * switch, and what the tag has reported from the sites it is running on.
 *
 * **Everything a dealer's website provider needs is on this card**, because
 * they are the person who has to act on it and they will not read a runbook:
 * the one line to paste, which of the dealership's sites it will run on, and
 * -- once it is live -- whether it is, what version, and whether another chat
 * product is running beside it. That last is the one a dealer notices before
 * we do, when a buyer answers in the other chat and the lead lands somewhere
 * nobody looks.
 */

interface Install {
  origin: string
  first_seen_at: string
  last_seen_at: string
  last_page: string
  loader_version: string
  outdated: boolean
  others: string[]
  duplicate_tag: boolean
  gtm_present: boolean
  reports: number
}

interface InstallsPayload {
  dealer: string
  switch: string
  origins: string[]
  loader: string
  version: string
  settings: {
    label: string
    title: string
    side: string
    offset: number
    gtm: boolean
    events: 'asc' | 'liner' | 'both'
  }
  installs: Install[]
}

/** What the dealer's Tag Manager is sent, by the names it is sent under. */
const EVENTS: [string, string, string][] = [
  ['The chat is opened', 'asc_comm_engagement (start)', 'liner_chat_open'],
  ['The buyer writes', 'asc_comm_engagement (engage)', 'liner_chat_start'],
  ['A number or an address is on file', 'asc_comm_submission, asc_comm_submission_sales', 'liner_lead'],
  ['A visit is booked', 'asc_comm_submission_sales_appt', 'liner_appointment'],
  ['The finance application is opened', 'asc_cta_interaction', 'liner_credit_app'],
]

export function WebsiteChatCard() {
  const queryClient = useQueryClient()
  const { data } = useQuery({
    queryKey: ['widget-installs'],
    queryFn: () => api.get<InstallsPayload>('/api/widget/installs'),
  })
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: { role: string } }>('/api/auth/me'),
    staleTime: 5 * 60_000,
  })
  const manager = me?.user.role === 'manager'
  const [copied, setCopied] = useState(false)

  const toggle = useMutation({
    mutationFn: (value: 'on' | 'off') => api.post('/api/widget/switch', { value }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['widget-installs'] }),
  })

  if (!data) return null
  const on = data.switch === 'on'
  const tag = `<script src="${data.loader || `${window.location.origin}${data.dealer ? `/${data.dealer}` : ''}/embed.js`}" data-dealer="${data.dealer}" async></script>`
  const style = data.settings.events

  return (
    <Card className="min-w-0 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">Website chat</h2>
        <Badge tone={on ? 'success' : 'warning'}>{on ? 'on' : 'switched off'}</Badge>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {manager ? (
            <Button
              variant={on ? 'ghost' : 'primary'}
              size="sm"
              disabled={toggle.isPending}
              onClick={() => toggle.mutate(on ? 'off' : 'on')}
            >
              {toggle.isPending ? 'Saving...' : on ? 'Switch off' : 'Switch on'}
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">Only a manager can switch it</span>
          )}
        </div>
      </div>
      <p className="mt-1 text-sm text-muted-foreground">
        The chat bubble on the dealership's own website. Paste the tag once; the label, the
        colour, which side it sits on and whether it shows at all are read from Liner on every
        page, so nothing on the site needs editing again. Switching it off hides the bubble
        within a minute everywhere it is installed.
      </p>

      {/* The one line to paste, with its own copy button: whoever installs it
          is copying it into a template or a Tag Manager tag, and a line
          retyped by hand is a line with a typo in it. */}
      {data.dealer ? (
        <div className="mt-4">
          <p className="text-xs font-medium text-muted-foreground">
            The tag -- in the site template before {'</body>'}, or as a Custom HTML tag on All
            Pages in Google Tag Manager
          </p>
          <div className="mt-1.5 flex min-w-0 items-stretch gap-2">
            <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-md border border-border bg-muted px-3 py-2 font-mono text-xs">
              {tag}
            </code>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                void navigator.clipboard?.writeText(tag).then(() => {
                  setCopied(true)
                  setTimeout(() => setCopied(false), 2000)
                })
              }}
            >
              {copied ? 'Copied' : 'Copy'}
            </Button>
          </div>
        </div>
      ) : (
        // The single-file default store has no dealership name to put in a
        // tag, and `/widget/` needs one. Said, rather than a tag reading
        // `data-dealer=""` that a provider would paste and never see work.
        <p className="mt-4 flex items-start gap-1.5 text-sm text-warning-foreground">
          <Icon name="alert" className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
          The website chat is installed per dealership, and this page is open for the
          deployment's default store, which has no dealership name of its own. Open Liner setup
          under the dealership's address -- /&lt;dealer&gt;/app/assistant -- for its tag.
        </p>
      )}

      <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">Runs on</p>
          {data.origins.length ? (
            <ul className="mt-1 space-y-0.5">
              {data.origins.map((o) => (
                <li key={o} className="truncate font-mono text-xs">{o}</li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 flex items-start gap-1.5 text-xs text-warning-foreground">
              <Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
              No website is listed for this dealership yet, so the bubble shows nowhere. Liner
              adds the site's address to the dealership's profile.
            </p>
          )}
          <p className="mt-2 text-xs text-muted-foreground">
            Any other site is refused by the browser, even with the tag copied onto it. A second
            domain, or a staging copy of the site, has to be added here by Liner first.
          </p>
        </div>

        <div className="min-w-0">
          <p className="text-xs font-medium text-muted-foreground">
            Google Tag Manager{' '}
            {data.settings.gtm ? '' : <span className="text-warning-foreground">(off)</span>}
          </p>
          {data.settings.gtm ? (
            <>
              <ul className="mt-1 space-y-1">
                {EVENTS.map(([when, asc, ours]) => (
                  <li key={when} className="min-w-0 text-xs">
                    <span className="text-muted-foreground">{when}: </span>
                    <span className="break-words font-mono">
                      {style === 'liner' ? ours : style === 'both' ? `${asc}; ${ours}` : asc}
                    </span>
                  </li>
                ))}
              </ul>
              <p className="mt-2 text-xs text-muted-foreground">
                Pushed to the site's own data layer, with the car's VIN, year, make and model.
                Never a name, a number, an address or anything the buyer typed.
              </p>
            </>
          ) : (
            <p className="mt-1 text-xs text-muted-foreground">
              Nothing is sent to the site's Tag Manager. Liner can turn it on in the
              dealership's profile.
            </p>
          )}
        </div>
      </div>

      <div className="mt-5">
        <p className="text-xs font-medium text-muted-foreground">Seen live</p>
        {data.installs.length === 0 ? (
          <p className="mt-1 text-sm text-muted-foreground">
            Not seen on any of the dealership's sites yet. Once the tag is on a page, the first
            visitor's browser reports it here within a few seconds.
          </p>
        ) : (
          <ul className="mt-1 divide-y divide-border rounded-md border border-border">
            {data.installs.map((row) => (
              <li key={row.origin} className="min-w-0 space-y-1.5 px-3 py-2.5">
                <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="truncate font-mono text-xs font-medium">{row.origin}</span>
                  <span className="text-xs text-muted-foreground">
                    last seen {relative(row.last_seen_at)}
                  </span>
                  {row.outdated && (
                    <Badge tone="warning">
                      tag v{row.loader_version}, current is v{data.version}
                    </Badge>
                  )}
                  {!row.gtm_present && data.settings.gtm && (
                    <Badge tone="neutral">no Tag Manager on the page</Badge>
                  )}
                </div>
                {row.others.length > 0 && (
                  <p className="flex items-start gap-1.5 text-xs text-warning-foreground">
                    <Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                    <span className="min-w-0">
                      Another chat is loaded on this site too: {row.others.join(', ')}. A buyer
                      offered two chats answers in one of them, and the lead may land in the
                      other system. Ask the website provider to take it off the pages Liner is on.
                    </span>
                  </p>
                )}
                {row.duplicate_tag && (
                  <p className="flex items-start gap-1.5 text-xs text-warning-foreground">
                    <Icon name="alert" className="mt-0.5 h-3.5 w-3.5 shrink-0 text-warning" />
                    The tag is on the page twice -- in the template and in Tag Manager, most
                    likely. Only one copy runs; remove the other.
                  </p>
                )}
                {row.last_page && (
                  <p className="truncate text-xs text-muted-foreground">
                    last page: {row.last_page}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  )
}
