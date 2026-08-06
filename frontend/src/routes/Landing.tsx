import { Link } from 'react-router-dom'

const TRIGGERS = [
  ['Out-the-door price', 'A total with tax, title and fees is a negotiation, not a question.'],
  ['Financing or credit trouble', 'Anything about scores, approvals or repossession.'],
  ['Asking for a manager', 'Taken literally, every time, with no attempt to resolve it first.'],
  ['Urgency', 'Needs a vehicle inside a few days.'],
  ['Ready to sign', 'Explicit intent to buy today or put money down.'],
]

const STEPS = [
  ['A buyer messages at 11:47 PM', 'Nobody is at the dealership. Liner is.'],
  ['It searches your actual inventory', 'Every price and mileage comes from your listings, not a guess.'],
  ['It books the appointment', 'Two concrete times, name and email captured, straight onto the calendar.'],
  ['Your rep wakes up to a confirmed visit', 'Assigned, confirmed, and already emailed.'],
]

export function Landing() {
  return (
    <div className="min-h-full bg-background">
      <header className="mx-auto flex max-w-5xl items-center justify-between px-6 py-5">
        <span className="text-lg font-semibold">Liner AI</span>
        <nav className="flex items-center gap-4 text-sm">
          <Link to="/chat" className="text-muted-foreground hover:text-foreground">
            Try the chat
          </Link>
          <Link
            to="/login"
            className="rounded-lg bg-primary px-4 py-2 font-medium text-primary-foreground"
          >
            Dealer sign in
          </Link>
        </nav>
      </header>

      <section className="mx-auto max-w-3xl px-6 pt-16 pb-20 text-center">
        <h1 className="text-5xl font-semibold leading-tight tracking-tight">
          Your buyers don't shop at 9 to 5.
        </h1>
        <p className="mx-auto mt-5 max-w-xl text-lg text-muted-foreground">
          Liner answers every message and every call, quotes only what's actually on your lot,
          and books the appointment before the buyer moves on to the next dealership.
        </p>
        <div className="mt-8 flex items-center justify-center gap-3">
          {/* Not a scripted mock -- this opens the real assistant. */}
          <Link
            to="/chat"
            className="rounded-xl bg-primary px-6 py-3 font-medium text-primary-foreground transition-opacity duration-150 hover:opacity-90"
          >
            Test your Liner AI
          </Link>
          <Link
            to="/call"
            className="rounded-xl border border-border px-6 py-3 font-medium transition-colors duration-150 hover:bg-muted"
          >
            Hear it on a call
          </Link>
        </div>
      </section>

      <section className="border-y border-border bg-muted/40 py-16">
        <div className="mx-auto max-w-5xl px-6">
          <h2 className="text-2xl font-semibold">How it works</h2>
          <ol className="mt-8 grid gap-6 md:grid-cols-4">
            {STEPS.map(([title, body], index) => (
              <li key={title}>
                <span className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-primary text-sm font-semibold text-primary-foreground">
                  {index + 1}
                </span>
                <p className="mt-3 font-medium">{title}</p>
                <p className="mt-1 text-sm text-muted-foreground">{body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="mx-auto max-w-5xl px-6 py-16">
        <h2 className="text-2xl font-semibold">It knows when to stop</h2>
        <p className="mt-2 max-w-2xl text-muted-foreground">
          Five situations hand the conversation to a person immediately. You choose which,
          and Liner tells the buyer a human is coming rather than improvising.
        </p>
        <ul className="mt-8 grid gap-4 md:grid-cols-2">
          {TRIGGERS.map(([title, body]) => (
            <li key={title} className="rounded-card border border-border p-4">
              <p className="font-medium">{title}</p>
              <p className="mt-1 text-sm text-muted-foreground">{body}</p>
            </li>
          ))}
        </ul>
      </section>

      <section className="border-t border-border bg-muted/40 py-16">
        <div className="mx-auto max-w-3xl px-6 text-center">
          <h2 className="text-2xl font-semibold">It only says what it can source</h2>
          <p className="mt-3 text-muted-foreground">
            Every price, mileage and availability claim is checked against your inventory before
            the buyer sees it. If Liner can't back a number up, it doesn't send it -- it gets a
            rep instead.
          </p>
          <Link
            to="/chat"
            className="mt-8 inline-block rounded-xl bg-primary px-6 py-3 font-medium text-primary-foreground"
          >
            Try it on your own inventory
          </Link>
        </div>
      </section>

      <footer className="mx-auto max-w-5xl px-6 py-10 text-sm text-muted-foreground">
        Liner AI -- built for Riverside Auto.
      </footer>
    </div>
  )
}
