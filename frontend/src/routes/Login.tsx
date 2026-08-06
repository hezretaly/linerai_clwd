import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { api, ApiError } from '../lib/api'
import { Button, Card, Field, Input } from '../components/ui'

export function Login() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [email, setEmail] = useState('dana.mercer@example.invalid')
  const [password, setPassword] = useState('liner-dev')

  const login = useMutation({
    mutationFn: () => api.post('/api/auth/login', { email, password }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['me'] })
      navigate('/app')
    },
  })

  return (
    <div className="flex h-full items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-sm p-6">
        <h1 className="text-lg font-semibold">Sign in to Liner</h1>
        <p className="mt-1 text-sm text-muted-foreground">Riverside Auto</p>

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

        <p className="mt-4 text-xs text-muted-foreground">
          Seeded accounts use @example.invalid, which RFC 2606 reserves so mail can never
          reach a real person. Password is <code className="font-mono">liner-dev</code>.
        </p>
      </Card>
    </div>
  )
}
