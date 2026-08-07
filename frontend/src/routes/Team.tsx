import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../lib/api'
import { hoursLabel, time } from '../lib/format'
import type { Dealership, TeamMember } from '../lib/types'
import { Badge, Card, Spinner, Switch } from '../components/ui'
import { Avatar, PageHeader } from '../components/dashboard/AppShell'

export function TeamPage() {
  const queryClient = useQueryClient()

  const { data } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
  })
  const { data: dealership } = useQuery({
    queryKey: ['dealership'],
    queryFn: () => api.get<Dealership>('/api/dealership'),
  })

  const patch = useMutation({
    mutationFn: ({ id, ...payload }: { id: string } & Record<string, unknown>) =>
      api.patch(`/api/team/${id}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['team'] }),
  })

  if (!data) return <Spinner />

  return (
    <>
      <PageHeader title="Team" subtitle={`${data.members.length} people`} />

      <div className="space-y-6 p-6">
        <Card>
          <header className="border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">People</h2>
            <p className="text-xs text-muted-foreground">
              Auto-assign is round-robin over reps who are under their daily cap.
            </p>
          </header>
          <div className="scroll-thin overflow-x-auto">
          <table className="w-full min-w-[560px] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs text-muted-foreground">
                <th className="px-4 py-2 font-medium">Name</th>
                <th className="px-4 py-2 font-medium">Role</th>
                <th className="px-4 py-2 font-medium">Today</th>
                <th className="px-4 py-2 font-medium">Next free</th>
                <th className="px-4 py-2 font-medium">Notify by</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.members.map((member) => (
                <tr key={member.id}>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <Avatar name={member.name} />
                      <div>
                        <p className="font-medium">{member.name}</p>
                        <p className="text-xs text-muted-foreground">{member.email}</p>
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-2.5 capitalize text-muted-foreground">
                    {member.role}
                  </td>
                  <td className="px-4 py-2.5">
                    <span className="tabular-nums">
                      {member.todays_appointments}/{member.daily_cap}
                    </span>
                    {member.at_capacity && (
                      <Badge tone="warning" className="ml-2">
                        At cap
                      </Badge>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-muted-foreground">
                    {time(member.next_free_at)}
                  </td>
                  <td className="px-4 py-2.5">
                    {/* Two options only. SMS is out of scope everywhere. */}
                    <div className="flex items-center gap-2">
                      <Switch
                        checked={member.notify_channel === 'email'}
                        onChange={(value) =>
                          patch.mutate({
                            id: member.id,
                            notify_channel: value ? 'email' : 'dashboard',
                          })
                        }
                        label={`Notify ${member.name} by email`}
                      />
                      <span className="text-xs text-muted-foreground">
                        {member.notify_channel === 'email' ? 'Email + dashboard' : 'Dashboard only'}
                      </span>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </Card>

        <Card className="p-4">
          <h2 className="text-sm font-semibold">Dealership</h2>
          <dl className="mt-2 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt className="text-xs text-muted-foreground">Name</dt>
              <dd>{dealership?.name}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Phone</dt>
              <dd>{dealership?.phone}</dd>
            </div>
            <div className="sm:col-span-2">
              <dt className="text-xs text-muted-foreground">Address</dt>
              <dd>{dealership?.address}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Hours</dt>
              <dd>{hoursLabel(dealership)}</dd>
            </div>
            <div>
              <dt className="text-xs text-muted-foreground">Timezone</dt>
              <dd>{dealership?.timezone}</dd>
            </div>
          </dl>
          <p className="mt-3 text-xs text-muted-foreground">
            These come from <code className="font-mono">backend/config/dealership.yaml</code> --
            the one file you edit per dealership. Every screen reads its hours from here.
          </p>
        </Card>
      </div>
    </>
  )
}
