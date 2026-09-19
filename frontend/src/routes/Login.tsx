import { useState } from 'react'
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { leaveTo } from '../lib/store'
import { useDealership } from '../lib/dealership'
import type { User } from '../lib/types'
import { Button, Card, Field, Input } from '../components/ui'
import { usePublicDemo, type Me } from './RequireAuth'

export function Login() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const { data: demo } = usePublicDemo()
  // Whose dashboard this is. Printed as a literal, a rebranded instance asked
  // a prospect's manager to sign in to Riverside Auto.
  const dealership = useDealership()
  // Arriving from the rep view, which is the only reason to be on this page
  // at all when the door is open.
  const wantsManager = params.get('as') === 'manager'
  // Liner's own staff, who land on a different dashboard entirely.
  const wantsOwner = params.get('as') === 'owner'
  // Bounced off /ops while signed in as the dealership's staff. Worth saying:
  // a login form arriving unannounced reads as "your session expired", and
  // theirs has not -- it is simply the wrong account for that door.
  const bouncedFromOps = params.get('why') === 'ops'
  // Prefilled on a laptop, blank once built. `import.meta.env.DEV` is false in
  // whatever `make build` produces, which is the only bundle a stranger ever
  // loads -- and on a public host these two fields were handing over a valid
  // username and the published development password, which is most of what a
  // login form is supposed to ask for. `make build` refuses if they survive.
  const seeded = import.meta.env.DEV
  const [email, setEmail] = useState(
    seeded ? (wantsOwner ? 'founder@linerai.us' : 'dana.mercer@example.invalid') : '',
  )
  const [password, setPassword] = useState(seeded ? 'liner-dev' : '')

  // Already signed in to the dashboard this door leads to? Go there. Asked per
  // door, because there are two: `?as=owner` is Liner's and everything else is
  // the dealership's. So the sidebar's "Dealership sign-in" link still reaches
  // the form -- an owner asking for the dealership's login has not signed in
  // to it, and that link exists precisely to swap accounts.
  //
  // Not `RequireAuth`'s fetcher, which walks through the public-demo door when
  // there is no session: that would mint a rep session for a stranger who did
  // nothing but open the login page. Plain `/api/auth/me`, like `RequireOwner`,
  // sharing the one `['me']` entry so there is one answer to who is signed in.
  const { data: session, isLoading: checking } = useQuery({
    queryKey: ['me'],
    queryFn: () => api.get<Me>('/api/auth/me'),
    retry: false,
  })

  const login = useMutation({
    mutationFn: () =>
      api.post<{ user: User; store?: string; redirect?: string }>(
        '/api/auth/login', { email, password },
      ),
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ['me'] })
      // Routed off the role that came back, not off the `?as=` that was asked
      // for. Signing in as an owner and landing on the dealership's overview
      // means every panel 403s; signing in as a rep and landing on /ops means
      // the same in the other direction.
      const fallback = data.user.role === 'owner' ? '/ops' : '/app'
      // The server decides which dealership this address belongs to -- the
      // browser cannot know, and that is the whole point of one sign-in form
      // serving every store.
      //
      // A document load rather than `navigate`, always. The router's basename
      // is fixed when the app mounts, so a client-side navigation to another
      // store resolves against the wrong one: from `/craigandlandreth`,
      // `navigate('/alsbou/app')` produces `/craigandlandreth/alsbou/app`.
      // Reloading also guarantees the new page starts with the right basename
      // and an empty query cache, which is the honest thing when the next
      // screen reads a different database. It costs one load, once.
      leaveTo(data.redirect || fallback)
    },
  })

  // After every hook, never beside the thing it is for: an early return above
  // one changes the hook count between renders and React blanks the page.
  if (checking) {
    return <div className="p-10 text-sm text-muted-foreground">Loading...</div>
  }
  const role = session?.user.role
  // `/ops` is never prefixed, so it cannot be a router navigation: under a
  // basename of `/alsbou`, <Navigate to="/ops"> resolves to `/alsbou/ops`,
  // which is not an address. Leaving the bundle is also correct -- ops is a
  // different realm reading a different table.
  if (role === 'owner' && wantsOwner) {
    leaveTo('/ops')
    return null
  }
  if (role && role !== 'owner' && !wantsOwner) return <Navigate to="/app" replace />

  return (
    <div className="flex h-full items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">
          {wantsOwner
            ? 'Sign in as Liner staff'
            : wantsManager
              ? 'Sign in as a sales manager'
              : 'Sign in to Liner'}
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          {wantsOwner
            ? 'Our own dashboard -- the demos people book with us, and the mail they send. Nothing about a dealership’s buyers is on it.'
            : wantsManager
              ? 'The team page, the assistant settings and publishing are managers only.'
              : dealership?.name || ' '}
        </p>

        {bouncedFromOps && (
          <p className="mt-3 rounded-md border border-border bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
            You are still signed in to the dealership&rsquo;s dashboard &mdash; that session
            has not gone anywhere. <code className="font-mono">/ops</code> is a separate
            account.{' '}
            <a href="/app" className="font-medium text-primary hover:underline">
              Back to the dashboard
            </a>
          </p>
        )}

        <form
          className="mt-5 space-y-4"
          onSubmit={(event) => {
            event.preventDefault()
            login.mutate()
          }}
        >
          <Field label="Email">
            <Input
              type="email"
              value={email}
              autoComplete="username"
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={password}
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>

          {login.isError && (
            <p className="text-sm text-destructive">
              {(login.error as ApiError).message}
            </p>
          )}

          <Button
            type="submit"
            variant="primary"
            className="w-full"
            disabled={login.isPending}
          >
            {login.isPending ? 'Signing in...' : 'Sign in'}
          </Button>
        </form>

        {/* The way back. Without it, a visitor who followed "log in as a
            sales manager" out of curiosity is stranded on a form they have no
            password for -- and the rep view they came from is behind a URL
            they may not have kept. */}
        {demo?.available && (
          <button
            onClick={() => {
              void api.post('/api/auth/public').then(async () => {
                await queryClient.invalidateQueries({ queryKey: ['me'] })
                navigate('/app')
              })
            }}
            className="mt-4 w-full text-center text-xs text-primary hover:underline"
          >
            Or look around as {demo.name ?? 'a sales rep'}, no password needed
          </button>
        )}

        {/* Same reason: a note naming the development password has no business
            in a bundle served from a real host. */}
        {seeded && (
          <p className="mt-4 text-xs text-muted-foreground">
            Seeded accounts use @example.invalid, which RFC 2606 reserves so mail can never
            reach a real person. Password is <code className="font-mono">liner-dev</code>.
          </p>
        )}
      </Card>
    </div>
  )
}
