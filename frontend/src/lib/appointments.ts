import type { Appointment } from './types'

/**
 * One definition of "is this appointment off" for the Calendar's list view
 * and its week/agenda views, which used to keep separate copies:
 * `BookedList` computed `status === 'cancelled' || status === 'no_show'`
 * itself, while the phone Agenda and the desktop grid filtered only by day
 * and treated every status alike -- so a cancelled visit read "1 booked" on
 * the week view and vanished from the list's default "still to come" count
 * in the same breath.
 *
 * Reads the server's own flag (`app.appointment_scope.OFF_STATUSES`, sent as
 * `appointment.off`) rather than re-deriving it, so a status this list does
 * not know how to classify (e.g. 'completed') cannot silently be miscounted
 * as either live or off by a rule written on the frontend alone.
 */
export function isOff(a: Appointment): boolean {
  return a.off
}

/**
 * "Still to come" -- the visit has not finished yet. `a.upcoming` (from
 * `GET /api/appointments`, computed on the dealership's own wall clock) is
 * used where it is present; the Calendar always fetches from that endpoint,
 * so this is the common path. The fallback exists only for a row from
 * elsewhere that never carried the flag, and it keeps the list's original
 * rule -- finished means start + duration has passed -- but is not the one
 * to trust across timezones: it parses `starts_at`, a naive dealership
 * wall-clock string, with `new Date()`, which reads it in the *viewer's*
 * zone.
 */
export function isPast(a: Appointment, now: number = Date.now()): boolean {
  if (typeof a.upcoming === 'boolean') return !a.upcoming && !isOff(a)
  return new Date(a.starts_at).getTime() + a.duration_min * 60_000 < now
}
