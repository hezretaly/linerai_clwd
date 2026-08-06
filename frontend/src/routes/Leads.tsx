import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { PROVENANCE_LABEL, dateTime, relative } from '../lib/format'
import type { Lead } from '../lib/types'
import { Badge, Card, Empty, Sheet, Spinner, Tabs } from '../components/ui'
import { Avatar, PageHeader } from '../components/dashboard/AppShell'

export function LeadsPage() {
  const [tab, setTab] = useState('all')
  const [openId, setOpenId] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['leads'],
    queryFn: () => api.get<{ leads: Lead[] }>('/api/leads'),
  })

  const { data: detail } = useQuery({
    queryKey: ['leads', openId],
    queryFn: () => api.get<Lead>(`/api/leads/${openId}`),
    enabled: Boolean(openId),
  })

  if (isLoading || !data) return <Spinner />

  const leads = data.leads
  const atRisk = leads.filter((lead) => lead.contact_risk)
  const shown = tab === 'risk' ? atRisk : tab === 'all' ? leads : leads.filter((l) => l.source === tab)

  return (
    <>
      <PageHeader title="Leads" subtitle={`${leads.length} total`} />

      <div className="p-6">
        <Card>
          <div className="px-4 pt-2">
            <Tabs
              active={tab}
              onChange={setTab}
              tabs={[
                { id: 'all', label: 'All', count: leads.length },
                { id: 'chat', label: 'Chat', count: leads.filter((l) => l.source === 'chat').length },
                { id: 'phone', label: 'Phone', count: leads.filter((l) => l.source === 'phone').length },
                { id: 'risk', label: 'No way to reach', count: atRisk.length },
              ]}
            />
          </div>

          {shown.length === 0 ? (
            <Empty title="Nothing here" />
          ) : (
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-xs text-muted-foreground">
                  <th className="px-4 py-2 font-medium">Name</th>
                  <th className="px-4 py-2 font-medium">Contact</th>
                  <th className="px-4 py-2 font-medium">Source</th>
                  <th className="px-4 py-2 font-medium">Assigned</th>
                  <th className="px-4 py-2 font-medium">Created</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border">
                {shown.map((lead) => (
                  <tr
                    key={lead.id}
                    onClick={() => setOpenId(lead.id)}
                    className="cursor-pointer transition-colors duration-150 hover:bg-muted"
                  >
                    <td className="px-4 py-2.5">
                      <div className="flex items-center gap-2">
                        <Avatar name={lead.name} />
                        <span className="font-medium">{lead.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-2.5">
                      {lead.email ? (
                        <span className="text-muted-foreground">{lead.email}</span>
                      ) : (
                        /* contact_risk inverted when SMS came out: no email is
                           what makes a lead unreachable now. */
                        <Badge tone="destructive">No email -- call back</Badge>
                      )}
                    </td>
                    <td className="px-4 py-2.5 capitalize text-muted-foreground">
                      {lead.source}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {lead.assigned_to?.name ?? '--'}
                    </td>
                    <td className="px-4 py-2.5 text-muted-foreground">
                      {relative(lead.created_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>

      <Sheet
        open={Boolean(openId)}
        onClose={() => setOpenId(null)}
        title={
          <>
            <h2 className="text-base font-semibold">{detail?.name ?? 'Lead'}</h2>
            <p className="text-sm text-muted-foreground">
              {detail?.email || 'No email on file'}
              {detail?.phone ? ` -- ${detail.phone}` : ''}
            </p>
          </>
        }
      >
        {!detail ? (
          <Spinner />
        ) : (
          <div className="space-y-6">
            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                What we know
              </h3>
              {detail.captured_fields?.length ? (
                <>
                  <ul className="mt-2 flex flex-wrap gap-2">
                    {detail.captured_fields.map((field) => (
                      <li
                        key={field.id}
                        className="rounded-lg border border-border px-2.5 py-1.5"
                      >
                        <span className="text-xs text-muted-foreground capitalize">
                          {field.key.replace('_', ' ')}
                        </span>
                        <p className={clsx('text-sm', !field.verified && 'italic')}>
                          {field.value}
                        </p>
                        <Badge tone={field.verified ? 'neutral' : 'warning'}>
                          {PROVENANCE_LABEL[field.provenance]}
                        </Badge>
                      </li>
                    ))}
                  </ul>
                  {detail.captured_fields.some((f) => !f.verified) && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      Italic values were inferred, not stated. Check them before using them on
                      a call.
                    </p>
                  )}
                </>
              ) : (
                <p className="mt-1 text-sm text-muted-foreground">Nothing captured yet.</p>
              )}
            </section>

            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Appointments
              </h3>
              <ul className="mt-2 space-y-2">
                {detail.appointments?.length ? (
                  detail.appointments.map((appointment) => (
                    <li key={appointment.id} className="rounded-lg border border-border p-3">
                      <div className="flex items-center justify-between">
                        <p className="text-sm font-medium">{dateTime(appointment.starts_at)}</p>
                        <Badge
                          tone={appointment.status === 'confirmed' ? 'success' : 'warning'}
                        >
                          {appointment.status}
                        </Badge>
                      </div>
                      {appointment.vehicle && (
                        <p className="text-sm text-muted-foreground">
                          {appointment.vehicle.title}
                        </p>
                      )}
                    </li>
                  ))
                ) : (
                  <li className="text-sm text-muted-foreground">None yet.</li>
                )}
              </ul>
            </section>

            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                History
              </h3>
              <ul className="mt-2 space-y-2">
                {detail.outreach?.length ? (
                  detail.outreach.map((item) => (
                    <li key={item.id} className="rounded-lg border border-border p-3">
                      <p className="text-sm font-medium">{item.subject}</p>
                      <p className="text-xs text-muted-foreground">
                        {item.channel === 'phone_logged' ? 'Call logged' : 'Email'} --{' '}
                        {dateTime(item.sent_at ?? item.created_at)}
                        {!item.delivered_externally && item.channel === 'email' && (
                          <> -- recorded locally, not delivered</>
                        )}
                      </p>
                    </li>
                  ))
                ) : (
                  <li className="text-sm text-muted-foreground">No outreach yet.</li>
                )}
              </ul>
            </section>

            <section>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Conversations
              </h3>
              <ul className="mt-2 space-y-1">
                {detail.conversations?.map((conversation) => (
                  <li key={conversation.id}>
                    <Link
                      to={`/app/conversations/${conversation.id}`}
                      className="text-sm text-primary hover:underline"
                    >
                      {conversation.channel} -- {relative(conversation.started_at)}
                    </Link>
                  </li>
                ))}
              </ul>
            </section>
          </div>
        )}
      </Sheet>
    </>
  )
}
