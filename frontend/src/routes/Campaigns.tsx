import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { money } from '../lib/format'
import { Badge, Card, Spinner } from '../components/ui'
import { Icon } from '../components/Icon'
import { PageIntro } from '../components/dashboard/AppShell'
import { EmailSetupPage } from './EmailSetup'

/* Reaching a group of buyers, rather than answering one.
 *
 * Everything else in this dashboard is a conversation: somebody wrote in and
 * gets answered. This is the other direction -- a reason to go back to people
 * who already talked to this dealership, and the reasons are things the
 * database already knows. Their car came down. Their car is still here. They
 * went quiet a fortnight ago.
 *
 * **The mailbox is a section of this page, not a page of its own.** Sending an
 * email to one buyer and sending one to forty is the same act at different
 * scale, and splitting them put the composer somewhere different from the
 * reason to use it. Nothing was removed: /app/email still resolves and lands
 * here.
 *
 * **Nothing here sends yet, and every card says so.** What is real is the
 * audience -- counted from rows on each request, so "41 buyers were quoted a
 * car that is now cheaper" is either true of this database or it is not. A
 * campaign list with plausible numbers painted on it would be the one place
 * this product claimed something it cannot do.
 */

interface Campaign {
  key: string
  name: string
  why: string
  channel: string
  audience: number | null
  examples: { lead_id: string; name: string; vehicle?: string; was?: number; now?: number; saving?: number; last_seen?: string }[]
  ready: boolean
  blocked_by: string
}

type IconName = Parameters<typeof Icon>[0]['name']

const CHANNEL_ICON: Record<string, IconName> = {
  email: 'mail',
  instagram: 'chat',
  facebook: 'chat',
  sms: 'chat',
}

type Tab = 'campaigns' | 'mailbox'

export function CampaignsPage() {
  const [tab, setTab] = useState<Tab>('campaigns')

  const { data, isLoading } = useQuery({
    queryKey: ['campaigns'],
    queryFn: () => api.get<{
      campaigns: Campaign[]
      note: string
      cold_days: number
    }>('/api/campaigns'),
  })

  return (
    <main className="p-4 md:p-6">
      <PageIntro
        title="Campaigns"
        subtitle="Going back to buyers who already talked to you, and the mail that comes with it."
      />

      <div className="mb-6 flex flex-wrap gap-1.5">
        {([['campaigns', 'Campaigns'], ['mailbox', 'Mailbox']] as [Tab, string][]).map(
          ([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={clsx(
                'rounded-md border px-3 py-1.5 text-sm font-medium transition-colors',
                tab === key
                  ? 'border-foreground bg-foreground text-background'
                  : 'border-input bg-background text-muted-foreground hover:bg-accent hover:text-accent-foreground',
              )}
            >
              {label}
            </button>
          ),
        )}
      </div>

      {tab === 'mailbox' ? (
        // The whole mailbox, unchanged -- it is a section here rather than a
        // page of its own. Its own PageIntro renders below this one, which is
        // the honest seam: the two are different things sharing a route.
        <EmailSetupPage />
      ) : isLoading || !data ? (
        <Spinner />
      ) : (
        <>
          {/* Said once, at the top, rather than repeated on every card. */}
          <div className="mb-6 rounded-md border border-warning/30 bg-warning-muted p-3">
            <p className="text-xs leading-relaxed text-warning-foreground">{data.note}</p>
          </div>

          <div className="grid min-w-0 gap-4 lg:grid-cols-2">
            {data.campaigns.map((c) => (
              <CampaignCard key={c.key} campaign={c} />
            ))}
          </div>
        </>
      )}
    </main>
  )
}

function CampaignCard({ campaign: c }: { campaign: Campaign }) {
  return (
    <Card className="min-w-0 p-5">
      <div className="flex flex-wrap items-start gap-2">
        <Icon
          name={CHANNEL_ICON[c.channel] ?? 'mail'}
          className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
        />
        <h2 className="min-w-0 flex-1 text-sm font-semibold">{c.name}</h2>
        {/* Not "coming soon". A card that cannot run says which dependency is
            missing, because that is the whole cost of an unbuilt integration:
            the hour spent working out what it needs. */}
        <Badge tone={c.ready ? 'primary' : 'warning'}>
          {c.ready ? 'ready to run' : 'not built'}
        </Badge>
      </div>

      <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{c.why}</p>

      {c.blocked_by ? (
        <p className="mt-3 rounded-md border border-border bg-muted/40 p-2.5 text-xs leading-relaxed text-muted-foreground">
          {c.blocked_by}
        </p>
      ) : c.audience === null ? (
        // Deliberately uncounted rather than shown as zero. A sale is a
        // decision about the dealership's calendar, and "everyone" beside it
        // invites exactly the untargeted blast the other cards avoid.
        <p className="mt-3 text-xs text-muted-foreground">
          Who gets this is a person's call, so there is no audience to count.
        </p>
      ) : (
        <div className="mt-3">
          <p className="text-sm">
            <span className="tnum text-2xl font-semibold">{c.audience}</span>
            <span className="ml-1.5 text-xs text-muted-foreground">
              {c.audience === 1 ? 'buyer' : 'buyers'} right now
            </span>
          </p>
          {c.examples.length > 0 && (
            <ul className="mt-2 space-y-0.5">
              {c.examples.map((e) => (
                <li key={`${e.lead_id}-${e.vehicle ?? e.last_seen}`} className="min-w-0 truncate text-xs">
                  <Link
                    to={`/app/leads/${e.lead_id}`}
                    className="font-medium text-primary hover:underline"
                  >
                    {e.name}
                  </Link>
                  {e.vehicle && <span className="text-muted-foreground"> · {e.vehicle}</span>}
                  {typeof e.saving === 'number' && (
                    <span className="text-success">
                      {' '}· {money(e.was ?? 0)} → {money(e.now ?? 0)}
                    </span>
                  )}
                  {e.last_seen && (
                    <span className="text-muted-foreground"> · last heard {e.last_seen}</span>
                  )}
                </li>
              ))}
              {c.audience > c.examples.length && (
                <li className="text-xs text-muted-foreground">
                  and {c.audience - c.examples.length} more
                </li>
              )}
            </ul>
          )}
        </div>
      )}
    </Card>
  )
}
