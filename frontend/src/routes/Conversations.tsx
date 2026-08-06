import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import clsx from 'clsx'

import { api } from '../lib/api'
import { PROVENANCE_LABEL, dateTime, money, relative, time } from '../lib/format'
import type { Conversation } from '../lib/types'
import { Badge, Button, Empty, Input, Spinner } from '../components/ui'
import { Avatar, PageHeader } from '../components/dashboard/AppShell'

export function ConversationsPage() {
  const { id } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [reply, setReply] = useState('')

  const { data, isLoading } = useQuery({
    queryKey: ['conversations'],
    queryFn: () => api.get<{ conversations: Conversation[] }>('/api/conversations'),
  })

  const conversations = data?.conversations ?? []
  const selectedId = id ?? conversations[0]?.id

  const { data: detail } = useQuery({
    queryKey: ['conversations', selectedId],
    queryFn: () => api.get<Conversation>(`/api/conversations/${selectedId}`),
    enabled: Boolean(selectedId),
  })

  useEffect(() => {
    setReply('')
  }, [selectedId])

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ['conversations'] })
    void queryClient.invalidateQueries({ queryKey: ['overview'] })
  }

  const takeover = useMutation({
    mutationFn: () => api.post(`/api/conversations/${selectedId}/takeover`),
    onSuccess: invalidate,
  })
  const handback = useMutation({
    mutationFn: () => api.post(`/api/conversations/${selectedId}/handback`),
    onSuccess: invalidate,
  })
  const send = useMutation({
    mutationFn: () => api.post(`/api/conversations/${selectedId}/messages`, { content: reply }),
    onSuccess: () => {
      setReply('')
      invalidate()
    },
  })

  if (isLoading) return <Spinner />

  return (
    <>
      <PageHeader title="Conversations" subtitle={`${conversations.length} total`} />

      <div className="flex h-[calc(100%-4.5rem)]">
        {/* List */}
        <aside className="w-72 shrink-0 overflow-y-auto border-r border-border bg-background">
          {conversations.length === 0 ? (
            <Empty title="No conversations yet" />
          ) : (
            <ul className="divide-y divide-border">
              {conversations.map((conversation) => (
                <li key={conversation.id}>
                  <button
                    onClick={() => navigate(`/app/conversations/${conversation.id}`)}
                    className={clsx(
                      'w-full px-4 py-3 text-left transition-colors duration-150 hover:bg-muted',
                      conversation.id === selectedId && 'bg-accent/60',
                    )}
                  >
                    <div className="flex items-center gap-2">
                      <Avatar name={conversation.lead?.name ?? 'Unknown caller'} />
                      <span className="min-w-0 flex-1 truncate text-sm font-medium">
                        {conversation.lead?.name ?? 'Unknown caller'}
                      </span>
                      {conversation.open_escalation && <Badge tone="destructive">!</Badge>}
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {conversation.summary || 'No messages yet'}
                    </p>
                    <div className="mt-1.5 flex items-center gap-2">
                      <Badge tone={conversation.channel === 'voice' ? 'primary' : 'neutral'}>
                        {conversation.channel}
                      </Badge>
                      <span className="text-[11px] text-muted-foreground">
                        {relative(conversation.started_at)}
                      </span>
                    </div>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </aside>

        {/* Thread */}
        <section className="flex min-w-0 flex-1 flex-col bg-background">
          {!detail ? (
            <Empty title="Select a conversation" />
          ) : (
            <>
              <div className="flex-1 space-y-3 overflow-y-auto p-6">
                {(detail.messages ?? []).map((message) => (
                  <div
                    key={message.id}
                    className={clsx(
                      'flex',
                      message.role === 'buyer' ? 'justify-start' : 'justify-end',
                    )}
                  >
                    <div
                      className={clsx(
                        'max-w-[70%] rounded-2xl px-4 py-2.5 text-sm whitespace-pre-wrap',
                        message.role === 'buyer' && 'bg-muted text-foreground',
                        message.role === 'assistant' &&
                          'bg-primary text-primary-foreground',
                        message.role === 'rep' &&
                          'border border-primary/30 bg-accent text-accent-foreground',
                      )}
                    >
                      {message.role === 'rep' && (
                        <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide opacity-70">
                          Sent by a person
                        </p>
                      )}
                      {message.content}
                      {message.tool_calls.length > 0 && (
                        <p className="mt-1.5 text-[11px] opacity-70">
                          {message.tool_calls.map((call) => call.name).join(' - ')}
                        </p>
                      )}
                      <p className="mt-1 text-[11px] opacity-60">{time(message.created_at)}</p>
                    </div>
                  </div>
                ))}
              </div>

              {/* Composer lock. agent_paused is what actually stops Liner. */}
              <div className="border-t border-border p-4">
                {detail.agent_paused ? (
                  <>
                    <div className="mb-2 rounded-lg bg-accent px-3 py-2 text-sm text-accent-foreground">
                      You are replying as Riverside Auto.
                    </div>
                    <form
                      className="flex gap-2"
                      onSubmit={(event) => {
                        event.preventDefault()
                        if (reply.trim()) send.mutate()
                      }}
                    >
                      <Input
                        value={reply}
                        onChange={(e) => setReply(e.target.value)}
                        placeholder="Write a reply..."
                      />
                      <Button type="submit" variant="primary" disabled={!reply.trim()}>
                        Send
                      </Button>
                      <Button type="button" onClick={() => handback.mutate()}>
                        Hand back
                      </Button>
                    </form>
                  </>
                ) : (
                  <div className="flex items-center justify-between gap-4 rounded-lg bg-muted px-3 py-2.5">
                    <p className="text-sm text-muted-foreground">
                      Liner is holding this conversation -- it won't reply again until someone
                      takes over or hands it back.
                    </p>
                    <Button variant="primary" onClick={() => takeover.mutate()}>
                      Take over
                    </Button>
                  </div>
                )}
              </div>
            </>
          )}
        </section>

        {/* Context rail */}
        {detail && (
          <aside className="w-72 shrink-0 space-y-4 overflow-y-auto border-l border-border bg-background p-4">
            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Lead
              </h3>
              <p className="mt-1 text-sm font-medium">
                {detail.lead?.name ?? 'Unknown caller'}
              </p>
              <p className="text-sm text-muted-foreground">
                {detail.lead?.email || 'No email on file'}
              </p>
              {detail.lead?.contact_risk && (
                <Badge tone="destructive" className="mt-1.5">
                  No way to reach them
                </Badge>
              )}
            </div>

            <div>
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                Stage
              </h3>
              <p className="mt-1 text-sm capitalize">{detail.stage.replace('_', ' ')}</p>
            </div>

            {detail.focus_vehicle && (
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Looking at
                </h3>
                <img
                  src={detail.focus_vehicle.photo_url}
                  alt=""
                  className="mt-1.5 w-full rounded-lg border border-border"
                />
                <p className="mt-1.5 text-sm font-medium">{detail.focus_vehicle.title}</p>
                <p className="text-sm text-muted-foreground">
                  {money(detail.focus_vehicle.price)}
                </p>
              </div>
            )}

            {detail.lead?.captured_fields && detail.lead.captured_fields.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  What we know
                </h3>
                <ul className="mt-1.5 space-y-1.5">
                  {detail.lead.captured_fields.map((field) => (
                    <li key={field.id} className="text-sm">
                      <span className="text-muted-foreground capitalize">
                        {field.key.replace('_', ' ')}:{' '}
                      </span>
                      {/* Inferred values render italic -- the difference between
                          reporting what we know and laundering a guess. */}
                      <span className={clsx(!field.verified && 'italic')}>{field.value}</span>
                      <Badge
                        tone={field.verified ? 'neutral' : 'warning'}
                        className="ml-1.5"
                      >
                        {PROVENANCE_LABEL[field.provenance]}
                      </Badge>
                    </li>
                  ))}
                </ul>
                {detail.lead.captured_fields.some((f) => !f.verified) && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    Italic values were inferred. Check them before using them on a call.
                  </p>
                )}
              </div>
            )}

            {detail.open_escalation && (
              <div className="rounded-lg bg-destructive-muted p-3">
                <h3 className="text-xs font-semibold uppercase tracking-wide text-destructive">
                  {detail.open_escalation.rule?.label ?? 'Needs a person'}
                </h3>
                <p className="mt-1 text-sm text-foreground">{detail.open_escalation.reason}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {dateTime(detail.open_escalation.created_at)}
                </p>
              </div>
            )}
          </aside>
        )}
      </div>
    </>
  )
}
