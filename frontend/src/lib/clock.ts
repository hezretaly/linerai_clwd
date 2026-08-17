import { useEffect, useState } from 'react'

/**
 * A clock that actually moves, costing the server nothing.
 *
 * The header read `const now = new Date()` during render, so it froze at
 * whatever minute the page happened to load and only moved when something
 * else made React re-render -- which on a quiet dashboard is never. It looked
 * like a stopped clock because it was one.
 *
 * This is a `setTimeout` in the browser and nothing else: no fetch, no poll,
 * no socket traffic. The cost is one state update per minute per open tab,
 * and browsers throttle even that to roughly once a minute in a background
 * tab, which is exactly the resolution wanted.
 *
 * It waits for the *start of the next minute* rather than ticking every 60s
 * from load. A fixed interval drifts by up to a minute against the real
 * clock, so a display showing 8:53 would flip to 8:54 at some arbitrary
 * moment inside that minute -- visibly wrong next to a phone.
 */
export function useNow(): Date {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>

    const schedule = () => {
      const next = 60_000 - (Date.now() % 60_000)
      timer = setTimeout(() => {
        setNow(new Date())
        schedule()
      }, next + 50) // a hair past the boundary, so it never lands on :59.999
    }

    schedule()
    return () => clearTimeout(timer)
  }, [])

  return now
}

/**
 * The dealership's wall clock, not the viewer's device.
 *
 * Every timestamp this system stores is naive and means dealership-local, and
 * the calendar draws slots straight from `hours_json` in that frame. The one
 * exception was `new Date()` for "now": a real instant, formatted with no
 * `timeZone`, which JavaScript renders in whatever the browser's OS is set to.
 *
 * That is fine while everyone is in Cedar Falls and wrong the moment somebody
 * is not -- a manager checking the dashboard from a different state saw a
 * header clock an hour off the appointments underneath it, and the calendar's
 * "now" line drawn at the wrong height for the same reason.
 *
 * `timezone` comes from the dealership row (or `demo_timezone` on /ops), so
 * this is the same frame as everything around it. Undefined falls back to the
 * viewer's, which is the honest answer before that row has loaded.
 */
export function zonedParts(at: Date, timezone?: string) {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone || undefined,
    hour: 'numeric',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(at)
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value ?? 0)
  // `hour12: false` still yields 24 in some engines for midnight; fold it.
  return { hour: get('hour') % 24, minute: get('minute') }
}

/** `Mon, Aug 17 · 8:53 PM`, in the dealership's zone. */
export function zonedStamp(at: Date, timezone?: string): string {
  const zone = timezone || undefined
  const day = at.toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric', timeZone: zone,
  })
  const clock = at.toLocaleTimeString('en-US', {
    hour: 'numeric', minute: '2-digit', timeZone: zone,
  })
  return `${day} · ${clock}`
}

/**
 * The short zone name (`CDT`), but only when the viewer is somewhere else.
 *
 * Always showing it is noise for the nine people out of ten sitting in the
 * showroom; never showing it is a wrong clock for the tenth. Comparing the
 * resolved zones rather than the offsets is deliberate -- two zones can share
 * an offset today and diverge at the next daylight-saving change.
 */
export function foreignZoneLabel(at: Date, timezone?: string): string | null {
  if (!timezone) return null
  const mine = Intl.DateTimeFormat().resolvedOptions().timeZone
  if (!mine || mine === timezone) return null
  const label = at
    .toLocaleTimeString('en-US', { timeZone: timezone, timeZoneName: 'short' })
    .split(' ')
    .pop()
  return label ?? null
}
