import type { Conversation, Lead } from './types'

/** One definition of "unclaimed", "live", "appointed", shared by the three
 *  pages that filter conversations: Chat, Calls and the cross-channel list.
 *
 *  They were three copies of the same ternary chain. The copies are what makes
 *  a filter drift -- Appointed counting `stage === 'booked'` on one page and an
 *  appointment row on another gives a manager two different numbers for the
 *  same question, and no way to tell which is wrong.
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

/**
 * How long a thread stays Live after the last thing anybody said.
 *
 * "Not closed" is not the same as live, and the gap between them is most of
 * this list. A buyer opens a chat at nine in the evening, asks one question
 * and shuts the tab; nothing closes the thread, because only the buyer can,
 * so it sat under In progress for weeks. A manager reading "14 in progress"
 * takes it to mean fourteen conversations happening -- something to walk the
 * floor about -- when thirteen of them ended days ago.
 *
 * Thirty minutes is a conversation's own patience, not a business rule: a
 * buyer comparing two cars takes a few minutes between messages and a buyer
 * who has gone is gone. Anything longer and the number stops meaning "now",
 * which is the only thing it is read for.
 *
 * A thread that goes quiet is not closed and does not pretend to be -- it
 * still has an owner, still takes a reply, and `stateOf` below badges it
 * Gone quiet rather than Closed. Only the buyer closes a thread.
 */
export const LIVE_AFTER_MINUTES = 30

/** Was anything said here inside the live window? */
export function stillGoing(lastActivity: string | null | undefined): boolean {
  if (!lastActivity) return false
  const at = new Date(lastActivity).getTime()
  if (Number.isNaN(at)) return false
  return Date.now() - at < LIVE_AFTER_MINUTES * 60_000
}

export const FILTER_LABEL: Record<ConversationFilter, string> = {
  all: 'All',
  flagged: 'Needs attention',
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
    case 'flagged':
      return Boolean(c.open_escalation)
    case 'unclaimed':
      return !c.lead?.assigned_user_id
    // Not closed, rather than 'active'. A thread at 'handoff' is one a rep is
    // standing in -- as live as it gets -- and counting only 'active' meant a
    // row badged "In progress" was missing from the In progress filter. The
    // lead side answers this from `open`, which is the same rule.
    //
    // And still being *said* -- see LIVE_AFTER_MINUTES. An abandoned tab never
    // closes its thread, so without the window this counted every chat anybody
    // ever walked away from.
    case 'live':
      return c.status !== 'closed' && stillGoing(c.last_activity_at ?? c.started_at)
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
  // Split on the same window the Live filter uses, because the badge and the
  // chip are read together: a row badged In progress that the In progress
  // filter does not contain is a page arguing with itself. Gone quiet is a
  // third thing and says so -- the thread is open and still takes a reply,
  // nobody has closed it, and nothing has been said for half an hour.
  if (c.status !== 'closed') {
    return stillGoing(c.last_activity_at ?? c.started_at)
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
      return !l.assigned_user_id
    case 'mine':
      return Boolean(meId && l.assigned_user_id === meId)
    case 'appointed':
      return l.stage === 'appointment'
    // Both derived from the lead's conversations by the API, so a person is
    // Live when any of their threads is, not when the newest one happens to
    // be -- a buyer with an open call and a closed chat is live. `open` is the
    // API's word for "not closed", so it needs the same window as a thread
    // does; `last_touch_at` is the last thing that happened across all of
    // them, which is exactly what the window asks about.
    case 'live':
      return Boolean(l.open) && stillGoing(l.last_touch_at ?? l.created_at)
    case 'declined':
      return Boolean(l.declined)
    default:
      return true
  }
}

export function leadStateOf(l: Lead): [string, string] {
  if (l.declined) return ['Client declined', 'border-border text-muted-foreground']
  if (l.stage === 'appointment')
    return ['Appointment set', 'border-success/30 bg-success/10 text-success']
  if (l.open) {
    return stillGoing(l.last_touch_at ?? l.created_at)
      ? ['In progress', 'border-primary/30 bg-primary/10 text-primary']
      : ['Gone quiet', 'border-border text-muted-foreground']
  }
  if (!l.conversation_count) return ['No conversation yet', 'border-border text-muted-foreground']
  return ['Closed', 'border-border text-muted-foreground']
}
