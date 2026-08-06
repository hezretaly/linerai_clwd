import type { Dealership } from './types'

export function money(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'Call for price'
  return `$${value.toLocaleString('en-US')}`
}

export function miles(value: number | null | undefined): string {
  return value === null || value === undefined ? '--' : `${value.toLocaleString('en-US')} mi`
}

export function time(iso: string | null | undefined): string {
  if (!iso) return '--'
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function dateTime(iso: string | null | undefined): string {
  if (!iso) return '--'
  return new Date(iso).toLocaleString('en-US', {
    weekday: 'short',
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

export function relative(iso: string | null | undefined): string {
  if (!iso) return '--'
  const diff = Date.now() - new Date(iso).getTime()
  const minutes = Math.round(diff / 60000)
  if (minutes < 1) return 'just now'
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.round(hours / 24)}d ago`
}

/**
 * How long something has been waiting, as `11h 01m`.
 *
 * Distinct from `relative` on purpose: "11h ago" describes when a thing
 * happened, "11h 01m" is a stopwatch on a buyer who has not been answered,
 * and the minutes matter to whoever is deciding what to pick up next.
 */
export function waited(iso: string | null | undefined): string {
  if (!iso) return '--'
  const minutes = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000))
  const hours = Math.floor(minutes / 60)
  if (!hours) return `${minutes}m`
  return `${hours}h ${String(minutes % 60).padStart(2, '0')}m`
}

/** No page states its own hours -- every surface reads the dealership row. */
export function hoursLabel(dealership: Dealership | undefined): string {
  if (!dealership) return ''
  const days = Object.entries(dealership.hours)
  const open = days.filter(([, w]) => w)
  const closed = days.filter(([, w]) => !w).map(([d]) => cap(d))
  if (!open.length) return 'Hours not set'
  const first = open[0][1]!
  const label = `${cap(open[0][0]).slice(0, 3)}-${cap(open[open.length - 1][0]).slice(0, 3)} ${short(first.open)}-${short(first.close)}`
  return closed.length ? `${label}, closed ${closed.map((d) => d.slice(0, 3)).join(', ')}` : label
}

/** Opening hour as a number, so the calendar grid never hardcodes 8 or 20. */
export function openWindow(dealership: Dealership | undefined): [number, number] {
  const first = Object.values(dealership?.hours ?? {}).find(Boolean)
  if (!first) return [8, 20]
  return [Number(first.open.slice(0, 2)), Number(first.close.slice(0, 2))]
}

export function isOpenOn(dealership: Dealership | undefined, date: Date): boolean {
  const names = ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday']
  return Boolean(dealership?.hours[names[date.getDay()]])
}

function short(value: string): string {
  const hour = Number(value.slice(0, 2))
  const suffix = hour < 12 ? 'am' : 'pm'
  return `${hour % 12 || 12}${suffix}`
}

function cap(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1)
}

export function initials(name: string): string {
  return name
    .split(' ')
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0].toUpperCase())
    .join('')
}

export const PROVENANCE_LABEL: Record<string, string> = {
  typed: 'typed',
  listing: 'from listing',
  caller_id: 'caller ID',
  inferred: 'inferred',
  // Stated by the buyer, but on a marketplace form rather than to us -- so it
  // is verified, and worth labelling differently from something they told Liner.
  adf: 'from lead feed',
}
