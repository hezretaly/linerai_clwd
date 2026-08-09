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
    case 'live':
      return c.status === 'active'
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
  if (c.status !== 'closed') return ['In progress', 'border-primary/30 bg-primary/10 text-primary']
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
    case 'live':
    case 'declined':
      return false
    default:
      return true
  }
}

export function leadStateOf(l: Lead): [string, string] {
  if (l.stage === 'appointment')
    return ['Appointment set', 'border-success/30 bg-success/10 text-success']
  return ['No conversation yet', 'border-border text-muted-foreground']
}
