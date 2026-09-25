import type { Conversation, Lead } from './types'

/** One definition of "unclaimed", "live", "appointed", shared by the three
 *  pages that filter conversations: Chat, Calls and the cross-channel list.
 *
 *  They were three copies of the same ternary chain. The copies are what makes
 *  a filter drift -- Appointed counting `stage === 'booked'` on one page and an
 *  appointment row on another gives a manager two different numbers for the
 *  same question, and no way to tell which is wrong.
 *
 *  Every predicate below reads a flag the server already computed --
 *  `c.live`, `c.lead?.unclaimed`, `l.appointment_set`, `l.declined` -- rather
 *  than re-deriving it from timestamps on the client. The one definition
 *  lives in `backend/app/threads.py`, `app/ownership.py` and
 *  `app/appointment_scope.py`; this file only asks which rows match it.
 */
export const CONVERSATION_FILTERS = [
  'all',
  'flagged',
  'unclaimed',
  'live',
  'mine',
  'declined',
  'appointed',
] as const

export type ConversationFilter = (typeof CONVERSATION_FILTERS)[number]

export const FILTER_LABEL: Record<ConversationFilter, string> = {
  all: 'All',
  // Renamed from "Needs attention" to match the KPI it links from
  // (Overview.tsx's KPI_LINKS points 'needs_a_person' at ?filter=flagged) --
  // two names for one link target read as two different things.
  flagged: 'Needs a person',
  unclaimed: 'Unclaimed',
  live: 'Live',
  mine: 'Mine',
  declined: 'Client declined',
  appointed: 'Appointed',
}

/** Emphasised chips: work waiting on a person, rather than a way to slice. */
export const FILTER_TONE: Partial<Record<ConversationFilter, 'primary'>> = {
  flagged: 'primary',
  unclaimed: 'primary',
}

export function matches(
  c: Conversation,
  filter: ConversationFilter,
  meId: string | undefined,
): boolean {
  switch (filter) {
    // "Needs a person" for a thread means it is one of the escalations behind
    // the server's waiting_on_person() -- open_escalation is per-thread
    // display data built from that same set, so this still means the right
    // thing for both a lead's thread and an anonymous one.
    case 'flagged':
      return Boolean(c.open_escalation)
    // A thread with no lead can never be unclaimed -- there is nothing to
    // claim until a lead exists. `c.lead?.unclaimed` is false whenever
    // `c.lead` is null, which is the fix: the old rule (`!c.lead?.assigned_user_id`)
    // was true for every anonymous thread, so the chip counted every live
    // chat that had not yet booked or filled in a details card.
    case 'unclaimed':
      return Boolean(c.lead?.unclaimed)
    // The server's one definition of live (threads.is_live): not closed, and
    // its own last activity is inside threads.LIVE_AFTER. No window is
    // re-derived here against the browser's clock.
    case 'live':
      return Boolean(c.live)
    case 'mine':
      return Boolean(meId && c.lead?.assigned_user_id === meId)
    case 'declined':
      return c.outcome === 'declined'
    // Derived, not stored: the stage is what a completed booking sets, so
    // there is no second place for it to disagree with the calendar.
    case 'appointed':
      return c.stage === 'booked'
    default:
      return true
  }
}

export function counts(
  conversations: Conversation[],
  meId: string | undefined,
): Record<ConversationFilter, number> {
  const out = {} as Record<ConversationFilter, number>
  for (const filter of CONVERSATION_FILTERS) {
    out[filter] = conversations.filter((c) => matches(c, filter, meId)).length
  }
  return out
}

/** What a row says it is, in one badge. Ordered by what a manager acts on
 *  first: how it ended beats how it is going.
 *
 *  No `destructive` here. Red on this dashboard means something broke and a
 *  rep has to fix it; a buyer who said no is an outcome, not a failure. */
export function stateOf(c: Conversation): [string, string] {
  if (c.outcome === 'declined') return ['Client declined', 'border-border text-muted-foreground']
  if (c.stage === 'booked') return ['Appointment set', 'border-success/30 bg-success/10 text-success']
  // Anything not closed, not only `active`. A thread waiting on a person sits
  // at status 'handoff', and calling that Closed next to a "Needs a person"
  // tag on the same row told a manager two opposite things at once.
  //
  // Split on `c.live`, the same flag the Live filter reads, because the badge
  // and the chip are read together: a row badged In progress that the Live
  // filter does not contain is a page arguing with itself. Gone quiet is a
  // third thing and says so -- the thread is open and still takes a reply,
  // nobody has closed it, and nothing has been said for the live window.
  if (c.status !== 'closed') {
    return c.live
      ? ['In progress', 'border-primary/30 bg-primary/10 text-primary']
      : ['Gone quiet', 'border-border text-muted-foreground']
  }
  return ['Closed', 'border-border text-muted-foreground']
}

/* ------------------------------------------------------------------------- *
 * Leads that never had a conversation.
 *
 * A lead imported from an ADF document arrives as a document, not a chat --
 * there is no thread, no stage, nothing said. It still has to be somewhere a
 * rep looks, so it sits in the same list under the same filters. What cannot
 * apply is answered false rather than fudged: a lead is never Live, because
 * nothing is running, and never declined, because nobody said no.
 * ------------------------------------------------------------------------- */

export function leadMatches(
  l: Lead,
  filter: ConversationFilter,
  meId: string | undefined,
): boolean {
  switch (filter) {
    case 'flagged':
      return Boolean(l.flagged)
    case 'unclaimed':
      return Boolean(l.unclaimed)
    case 'mine':
      return Boolean(meId && l.assigned_user_id === meId)
    case 'appointed':
      return Boolean(l.appointment_set)
    // The server's own `live` flag: true when any *one* of this lead's
    // threads is live (threads.is_live), never derived here from `open`
    // (any thread not closed) combined with `last_touch_at` (the max over
    // every thread, appointment and email) -- that combination could mix
    // "open" from one thread with "recent" from a different one, or from an
    // email or a booked appointment, and call the result Live.
    case 'live':
      return Boolean(l.live)
    case 'declined':
      return Boolean(l.declined)
    default:
      return true
  }
}

export function leadStateOf(l: Lead): [string, string] {
  if (l.declined) return ['Client declined', 'border-border text-muted-foreground']
  if (l.appointment_set)
    return ['Appointment set', 'border-success/30 bg-success/10 text-success']
  if (l.open) {
    return l.live
      ? ['In progress', 'border-primary/30 bg-primary/10 text-primary']
      : ['Gone quiet', 'border-border text-muted-foreground']
  }
  if (!l.conversation_count) return ['No conversation yet', 'border-border text-muted-foreground']
  return ['Closed', 'border-border text-muted-foreground']
}
