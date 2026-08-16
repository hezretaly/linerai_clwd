import { useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import type { User } from '../lib/types'
import { Button, Card, Field, Input } from '../components/ui'
import { usePublicDemo } from './RequireAuth'

export function Login() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params] = useSearchParams()
  const { data: demo } = usePublicDemo()
  // Arriving from the rep view, which is the only reason to be on this page
  // at all when the door is open.
  const wantsManager = params.get('as') === 'manager'
  // Liner's own staff, who land on a different dashboard entirely.
  const wantsOwner = params.get('as') === 'owner'
  const [email, setEmail] = useState(
    wantsOwner ? 'founder@linerai.us' : 'dana.mercer@example.invalid',
  )
  const [password, setPassword] = useState('liner-dev')

  const login = useMutation({
    mutationFn: () => api.post<{ user: User }>('/api/auth/login', { email, password }),
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ['me'] })
      // Routed off the role that came back, not off the `?as=` that was asked
      // for. Signing in as an owner and landing on the dealership's overview
      // means every panel 403s; signing in as a rep and landing on /ops means
      // the same in the other direction.
      navigate(data.user.role === 'owner' ? '/ops' : '/app')
    },
  })

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
              : 'Riverside Auto'}
        </p>

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

        <p className="mt-4 text-xs text-muted-foreground">
          Seeded accounts use @example.invalid, which RFC 2606 reserves so mail can never
          reach a real person. Password is <code className="font-mono">liner-dev</code>.
        </p>
      </Card>
    </div>
  )
}
