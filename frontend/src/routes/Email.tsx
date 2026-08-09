import { Link } from 'react-router-dom'

import { PageIntro } from '../components/dashboard/AppShell'
import { Card } from '../components/ui'

/**
 * Email has no thread to show.
 *
 * A conversation in this system is `chat` or `voice` -- a back-and-forth with
 * a stage, a transcript and something to take over. Email is `outreach`: a
 * one-way send a rep composes, recorded against the lead it went to. There is
 * no inbound mailbox, so nothing arrives to reply to.
 *
 * This page says that rather than showing an empty inbox, which would read as
 * "no email yet" when the truth is "email does not work that way here". What
 * has been sent is on the lead, next to the person it was sent to.
 */
export function EmailPage() {
  return (
    <main className="p-4 md:p-6">
      <PageIntro
        accent
        title="Email"
        subtitle="Nothing is threaded here yet"
      />
      <Card className="max-w-2xl p-6 shadow-sm">
        <p className="text-sm text-foreground">
          Email in Liner is outbound only. A rep writes to a lead -- a follow-up, a
          reminder, a credit application -- and the send is recorded against that
          lead, with whether the link was opened.
        </p>
        <p className="mt-3 text-sm text-muted-foreground">
          There is no inbound mailbox, so there is no thread to reply to and nothing
          for Liner to answer. Until one exists this page has nothing of its own to
          show, and an empty inbox here would read as a quiet day rather than as a
          feature that is not built.
        </p>
        <p className="mt-4 text-sm">
          <Link to="/app/conversations" className="font-medium text-primary hover:underline">
            Everything sent is on the lead
          </Link>{' '}
          <span className="text-muted-foreground">
            -- and the Emails sent card on the overview counts them.
          </span>
        </p>
      </Card>
    </main>
  )
}
