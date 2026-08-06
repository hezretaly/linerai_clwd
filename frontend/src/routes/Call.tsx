import { useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '../lib/api'
import { Button, Card } from '../components/ui'

/**
 * The call UI shell. There is deliberately no fake provider behind it.
 *
 * A scripted transcript on a timer would make this screen look like it worked
 * while proving nothing about the only things a voice provider decides:
 * latency, barge-in and whether the voice sounds like a 2015 IVR. So the button
 * calls the real endpoint, and the real endpoint says what is missing.
 */
export function Call() {
  const [error, setError] = useState<ApiError | null>(null)
  const [pending, setPending] = useState(false)

  const start = async () => {
    setPending(true)
    setError(null)
    try {
      await api.post('/api/voice/sessions')
    } catch (exc) {
      setError(exc as ApiError)
    } finally {
      setPending(false)
    }
  }

  const missing = error?.notConfigured

  return (
    <div className="flex h-full items-center justify-center bg-muted/40 px-4">
      <Card className="w-full max-w-md p-6 text-center">
        <h1 className="text-lg font-semibold">Call Riverside Auto</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Same assistant, same inventory, same booking -- over the phone.
        </p>

        <div className="my-6 flex items-end justify-center gap-1" aria-hidden="true">
          {[10, 22, 34, 46, 34, 22, 10].map((height, index) => (
            <span
              key={index}
              style={{ height }}
              className="w-1.5 rounded-full bg-border"
            />
          ))}
        </div>

        <Button variant="primary" onClick={start} disabled={pending} className="w-full">
          {pending ? 'Connecting...' : 'Start a call'}
        </Button>

        {missing && (
          <div className="mt-5 rounded-lg bg-warning-muted p-4 text-left">
            <p className="text-sm font-semibold text-warning-foreground">
              Voice is not configured
            </p>
            <p className="mt-1 text-sm text-warning-foreground/90">{missing.detail}</p>
            <p className="mt-2 text-xs text-warning-foreground/80">
              Missing:{' '}
              {missing.missing.map((key) => (
                <code key={key} className="mr-1 font-mono">
                  {key}
                </code>
              ))}
            </p>
          </div>
        )}

        {error && !missing && (
          <p className="mt-4 text-sm text-destructive">{error.message}</p>
        )}

        <p className="mt-5 text-xs text-muted-foreground">
          The call UI, session mint and tool relay are built and the relay is tested. Only the
          audio provider is missing -- there is no simulated call here on purpose.
        </p>

        <Link to="/chat" className="mt-4 inline-block text-sm text-primary hover:underline">
          Use the chat instead
        </Link>
      </Card>
    </div>
  )
}
