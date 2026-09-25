import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { hoursLabel, time } from '../lib/format'
import type {
  Dealership,
  NewRepResponse,
  RemovePreview,
  ResetPasswordResponse,
  TeamMember,
  User,
} from '../lib/types'
import { Badge, Button, Card, Field, Input, Sheet, Spinner, Switch } from '../components/ui'
import { Avatar, PageHeader } from '../components/dashboard/AppShell'

export function TeamPage() {
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [removing, setRemoving] = useState<TeamMember | null>(null)
  const [revealed, setRevealed] = useState<
    { title: string; name: string; email: string; password: string } | null
  >(null)

  const { data } = useQuery({
    queryKey: ['team'],
    queryFn: () => api.get<{ members: TeamMember[] }>('/api/team'),
  })
  const { data: dealership } = useQuery({
    queryKey: ['dealership'],
    queryFn: () => api.get<Dealership>('/api/dealership'),
  })
  // Same query AssignTo.tsx reads -- one definition of "am I the manager"
  // rather than every screen guessing at the role from something else.
  const { data: me } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<{ user: User }>('/api/auth/me'),
    staleTime: 5 * 60_000,
  })
  const canManage = me?.user.role === 'manager'

  const patch = useMutation({
    mutationFn: ({ id, ...payload }: { id: string } & Record<string, unknown>) =>
      api.patch(`/api/team/${id}`, payload),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['team'] }),
  })

  const setOut = useMutation({
    mutationFn: ({ id, out }: { id: string; out: boolean }) =>
      api.patch<TeamMember>(`/api/team/${id}/out`, { out }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['team'] }),
  })

  const resetPassword = useMutation({
    mutationFn: (member: TeamMember) =>
      api.post<ResetPasswordResponse>(`/api/team/${member.id}/reset-password`),
    // Nothing in the roster changes -- the response carries only the new
    // password -- so there is no team query to invalidate here, unlike Add.
    onSuccess: (result, member) =>
      setRevealed({ title: 'Password reset', name: member.name, email: member.email, password: result.password }),
  })

  if (!data) return <Spinner />

  return (
    <>
      <PageHeader
        title="Team"
        subtitle={`${data.members.length} people`}
        actions={
          canManage && (
            <Button variant="primary" size="sm" onClick={() => setAddOpen(true)}>
              Add rep
            </Button>
          )
        }
      />

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
                <th className="px-4 py-2 font-medium">Out today</th>
                {canManage && (
                  <th className="px-4 py-2 font-medium">
                    <span className="sr-only">Actions</span>
                  </th>
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {data.members.map((member) => {
                // Everyone can mark themselves out; only a manager can mark
                // somebody else's row -- the same split `set_out` enforces
                // server-side. A switch neither of us can move is a control
                // that can only fail, so a rep sees the plain status (already
                // said once by the badge on their name) rather than a
                // greyed-out toggle -- the same "hide it, don't disable it"
                // rule AssignTo.tsx and Calendar.tsx already follow.
                const canToggleOut = canManage || member.id === me?.user.id
                return (
                <tr key={member.id}>
                  <td className="px-4 py-2.5">
                    <div className="flex items-center gap-2">
                      <Avatar name={member.name} />
                      <div>
                        <p className="flex items-center gap-1.5 font-medium">
                          {member.name}
                          {member.out && <Badge tone="neutral">Out</Badge>}
                        </p>
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
                      <Badge tone="primary" className="ml-2">
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
                  <td className="px-4 py-2.5">
                    {canToggleOut ? (
                      <Switch
                        checked={member.out}
                        onChange={(value) => setOut.mutate({ id: member.id, out: value })}
                        label={`${member.name} is out today`}
                      />
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        {member.out ? 'Out' : '—'}
                      </span>
                    )}
                  </td>
                  {canManage && (
                    <td className="px-4 py-2.5">
                      {/* Never on the manager's own row -- a dealership has
                          exactly one, and there is no path here to remove or
                          demote them. A button that can only 403 is worse
                          than no button. */}
                      {member.role === 'rep' && (
                        <div className="flex justify-end gap-1 whitespace-nowrap">
                          <Button
                            size="sm"
                            variant="ghost"
                            disabled={resetPassword.isPending && resetPassword.variables?.id === member.id}
                            onClick={() => resetPassword.mutate(member)}
                          >
                            Reset password
                          </Button>
                          <Button size="sm" variant="ghost" onClick={() => setRemoving(member)}>
                            Remove
                          </Button>
                        </div>
                      )}
                    </td>
                  )}
                </tr>
                )
              })}
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

      {addOpen && (
        <AddRepSheet
          onClose={() => setAddOpen(false)}
          onCreated={(result) => {
            setAddOpen(false)
            setRevealed({
              title: 'Rep added',
              name: result.member.name,
              email: result.member.email,
              password: result.password,
            })
          }}
        />
      )}

      {removing && <RemoveRepSheet member={removing} onClose={() => setRemoving(null)} />}

      {revealed && (
        <PasswordRevealSheet
          title={revealed.title}
          name={revealed.name}
          email={revealed.email}
          password={revealed.password}
          onClose={() => setRevealed(null)}
        />
      )}
    </>
  )
}

/** A manager adds a rep -- name and email, nothing else; the server always
 *  makes a "rep" (there is no path here to a second manager). */
function AddRepSheet({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (result: NewRepResponse) => void
}) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')

  const add = useMutation({
    mutationFn: () => api.post<NewRepResponse>('/api/team', { name, email }),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: ['team'] })
      onCreated(result)
    },
  })

  return (
    <Sheet open onClose={onClose} title={<h2 className="text-base font-semibold">Add a rep</h2>}>
      <form
        className="space-y-4"
        onSubmit={(e) => {
          e.preventDefault()
          add.mutate()
        }}
      >
        <Field label="Name">
          <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Jordan Reyes" />
        </Field>
        <Field label="Email">
          <Input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="jordan@yourdealership.com"
          />
        </Field>
        {add.isError && (
          <p className="text-sm text-destructive">{(add.error as ApiError).message}</p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="submit"
            variant="primary"
            disabled={!name.trim() || !email.trim() || add.isPending}
          >
            {add.isPending ? 'Adding...' : 'Add rep'}
          </Button>
        </div>
      </form>
    </Sheet>
  )
}

/** What removing somebody actually does, in the words a dealer uses rather
 *  than the ones the three counts are named after. */
function blastRadius(preview: RemovePreview): string {
  const parts: string[] = []
  if (preview.leads_returned > 0) {
    parts.push(`${preview.leads_returned} ${preview.leads_returned === 1 ? 'buyer' : 'buyers'}`)
  }
  if (preview.appointments_returned > 0) {
    parts.push(
      `${preview.appointments_returned} upcoming ${
        preview.appointments_returned === 1 ? 'appointment' : 'appointments'
      }`,
    )
  }
  if (preview.escalations_reopened > 0) {
    parts.push(
      `${preview.escalations_reopened} open ${
        preview.escalations_reopened === 1 ? 'thing' : 'things'
      } they were handling`,
    )
  }
  if (!parts.length) {
    return "They're not holding anything right now -- no buyers, no upcoming appointments, nothing open."
  }
  const last = parts.pop()!
  return `This hands back ${parts.length ? `${parts.join(', ')} and ${last}` : last}.`
}

/** Removing is `PATCH .../active=false`, the same call the notify and Out
 *  switches already use -- there is no separate remove endpoint. The blast
 *  radius is shown before the decision, same as taking a car off the lot
 *  (Inventory.tsx): the moment that matters is who this hands back, and a
 *  confirmation that arrives after is a speed bump, not a warning. */
function RemoveRepSheet({ member, onClose }: { member: TeamMember; onClose: () => void }) {
  const queryClient = useQueryClient()

  const { data: preview, isError: previewFailed } = useQuery({
    queryKey: ['team', member.id, 'remove-preview'],
    queryFn: () => api.get<RemovePreview>(`/api/team/${member.id}/remove-preview`),
  })

  const remove = useMutation({
    mutationFn: () => api.patch(`/api/team/${member.id}`, { active: false }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['team'] })
      onClose()
    },
  })

  return (
    <Sheet
      open
      onClose={onClose}
      title={<h2 className="text-base font-semibold">Remove {member.name}?</h2>}
    >
      <div className="space-y-4">
        {previewFailed ? (
          <p className="text-sm text-destructive">
            Could not check what this would affect. Try again.
          </p>
        ) : (
          <p className="text-sm leading-relaxed text-muted-foreground">
            {preview ? blastRadius(preview) : 'Checking what this would affect...'}
          </p>
        )}
        <p className="text-sm leading-relaxed text-muted-foreground">
          They come off the team. Nothing is deleted, and their appointments stay on the
          calendar for somebody else to take over.
        </p>
        {remove.isError && (
          <p className="text-sm text-destructive">{(remove.error as ApiError).message}</p>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            disabled={!preview || remove.isPending}
            onClick={() => remove.mutate()}
          >
            {remove.isPending ? 'Removing...' : 'Remove'}
          </Button>
        </div>
      </div>
    </Sheet>
  )
}

/** A generated password, shown once. Shared by Add rep and Reset password --
 *  same shape, same rule: `backend/app/add_user.py` prints it once and keeps
 *  no copy anywhere but the bcrypt hash, and this says that plainly to
 *  whoever is signed in rather than in the terms that docstring uses. */
function PasswordRevealSheet({
  title,
  name,
  email,
  password,
  onClose,
}: {
  title: string
  name: string
  email: string
  password: string
  onClose: () => void
}) {
  const [copied, setCopied] = useState(false)

  return (
    <Sheet open onClose={onClose} title={<h2 className="text-base font-semibold">{title}</h2>}>
      <div className="space-y-4">
        <p className="text-sm text-muted-foreground">
          {name}'s password for {email}. This is the only time it will be shown -- write it
          down or hand it over now. Nobody here can look it back up; if it's lost, reset it
          again from this page.
        </p>
        <div className="flex items-stretch gap-2">
          <code className="min-w-0 flex-1 overflow-x-auto whitespace-nowrap rounded-md border border-border bg-muted px-3 py-2 font-mono text-sm">
            {password}
          </code>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              void navigator.clipboard?.writeText(password).then(() => {
                setCopied(true)
                setTimeout(() => setCopied(false), 2000)
              })
            }}
          >
            {copied ? 'Copied' : 'Copy'}
          </Button>
        </div>
        <div className="flex justify-end border-t border-border pt-3">
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        </div>
      </div>
    </Sheet>
  )
}
