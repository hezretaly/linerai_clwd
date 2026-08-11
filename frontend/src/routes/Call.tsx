import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import clsx from 'clsx'

import { api, ApiError } from '../lib/api'
import { Button, Card } from '../components/ui'

/**
 * A real call, over WebRTC.
 *
 * The shape of it, because it is unusual and the unusual part is deliberate:
 * the browser asks *our* server for an ephemeral secret, then talks audio
 * straight to OpenAI with it. Audio never passes through the backend. Proxying
 * it would add a round trip to every syllable, and on a phone call latency is
 * the product -- half a second of lag is the difference between a conversation
 * and a hold queue.
 *
 * That leaves one thing to be careful about. The model runs on the far side of
 * a connection we are not in, so anything that must be *guaranteed* cannot
 * live in the prompt: tool calls come back over the data channel and are
 * relayed to /api/voice/tools, where the same executors as chat filter a
 * do-not-discuss vehicle and refuse a double booking. What cannot be
 * guaranteed is the wording -- the reply guard runs on the transcript after
 * the words are already out, and raises a rep rather than pretending to have
 * stopped them.
 *
 * There is still no fake provider. Without a key this page says what is
 * missing, because a scripted transcript on a timer would prove nothing about
 * the only three things a voice vendor decides: latency, barge-in, and whether
 * it sounds like a 2015 IVR.
 */

interface Session {
  conversation_id: string
  provider: string
  client_secret: string
  expires_in: number
  calls_url: string
  model: string
}

interface Line {
  id: string
  role: 'buyer' | 'assistant'
  text: string
}

type Phase = 'idle' | 'connecting' | 'live' | 'ended'

export function Call() {
  const [error, setError] = useState<ApiError | null>(null)
  const [failed, setFailed] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [lines, setLines] = useState<Line[]>([])
  const [muted, setMuted] = useState(false)
  const [speaking, setSpeaking] = useState(false)

  const pc = useRef<RTCPeerConnection | null>(null)
  const channel = useRef<RTCDataChannel | null>(null)
  const mic = useRef<MediaStream | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const convo = useRef('')
  const transcript = useRef<HTMLDivElement | null>(null)

  // Hanging up on unmount is not tidiness. Without it, navigating away leaves
  // the microphone live and the meter lit in the browser chrome, which is the
  // single most alarming thing a website can do.
  useEffect(() => () => teardown(), [])

  useEffect(() => {
    transcript.current?.scrollTo({ top: transcript.current.scrollHeight })
  }, [lines])

  const teardown = () => {
    channel.current?.close()
    pc.current?.close()
    mic.current?.getTracks().forEach((t) => t.stop())
    channel.current = null
    pc.current = null
    mic.current = null
  }

  const say = (role: Line['role'], text: string) => {
    if (!text.trim()) return
    setLines((prev) => [...prev, { id: `${role}-${prev.length}-${Date.now()}`, role, text }])
    void api.post('/api/voice/transcript', {
      conversation_id: convo.current, role, content: text,
    }).catch(() => {
      /* The call is the thing. A transcript row that failed to save must not
         interrupt it -- the buyer is mid-sentence. */
    })
  }

  /** Tool calls come back over the data channel and are executed on our side.
   *
   *  This is the part that makes a call as safe as a chat. The model asks for
   *  `search_inventory`; the executor decides what it gets, so a do-not-discuss
   *  vehicle never reaches it whatever the prompt says. The result goes back as
   *  a conversation item, and `response.create` is what makes the model
   *  actually speak it -- without that it sits silent holding an answer. */
  const runTool = async (name: string, callId: string, rawArgs: string) => {
    let input: Record<string, unknown> = {}
    try {
      input = JSON.parse(rawArgs || '{}')
    } catch {
      input = {}
    }
    let output: unknown
    try {
      output = await api.post('/api/voice/tools', {
        conversation_id: convo.current, name, input, tool_call_id: callId,
      })
    } catch (exc) {
      // Handed back as a result rather than swallowed. A model told the tool
      // errored asks the buyer something else; a model told nothing waits.
      output = { error: (exc as ApiError).message }
    }
    channel.current?.send(JSON.stringify({
      type: 'conversation.item.create',
      item: { type: 'function_call_output', call_id: callId, output: JSON.stringify(output) },
    }))
    channel.current?.send(JSON.stringify({ type: 'response.create' }))
  }

  const onEvent = (event: Record<string, any>) => {
    switch (event.type) {
      case 'response.function_call_arguments.done':
        void runTool(event.name, event.call_id, event.arguments)
        break
      // The buyer's own words. Without a transcription model configured
      // server-side this event never fires and the dealer's transcript is a
      // monologue -- which is why the session asks for one.
      case 'conversation.item.input_audio_transcription.completed':
        say('buyer', event.transcript || '')
        break
      case 'response.output_audio_transcript.done':
        say('assistant', event.transcript || '')
        break
      case 'output_audio_buffer.started':
        setSpeaking(true)
        break
      case 'output_audio_buffer.stopped':
      case 'output_audio_buffer.cleared':
        setSpeaking(false)
        break
      case 'error':
        setFailed(event.error?.message || 'The provider reported an error.')
        break
      default:
        break
    }
  }

  const start = async () => {
    setError(null)
    setFailed('')
    setLines([])
    setPhase('connecting')
    try {
      // Ours, and the only request that carries the real key. What comes back
      // is good for about a minute and for one call.
      const session = await api.post<Session>('/api/voice/sessions')
      convo.current = session.conversation_id

      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      mic.current = stream

      const connection = new RTCPeerConnection()
      pc.current = connection

      // The model's voice. Attached before the offer, because the track can
      // arrive as soon as the answer is set.
      connection.ontrack = (e) => {
        if (audio.current) audio.current.srcObject = e.streams[0]
      }
      stream.getTracks().forEach((track) => connection.addTrack(track, stream))

      const events = connection.createDataChannel('oai-events')
      channel.current = events
      events.onmessage = (e) => onEvent(JSON.parse(e.data))
      // Liner opens. Without this the line is live and silent, and a buyer who
      // hears nothing hangs up before saying anything for the model to answer.
      events.onopen = () => events.send(JSON.stringify({ type: 'response.create' }))

      const offer = await connection.createOffer()
      await connection.setLocalDescription(offer)

      const answer = await fetch(`${session.calls_url}?model=${encodeURIComponent(session.model)}`, {
        method: 'POST',
        body: offer.sdp,
        headers: {
          Authorization: `Bearer ${session.client_secret}`,
          'Content-Type': 'application/sdp',
        },
      })
      if (!answer.ok) {
        throw new Error(`The provider refused the connection (${answer.status}).`)
      }
      await connection.setRemoteDescription({
        type: 'answer', sdp: await answer.text(),
      })

      connection.onconnectionstatechange = () => {
        if (['failed', 'disconnected', 'closed'].includes(connection.connectionState)) {
          setPhase((p) => (p === 'live' ? 'ended' : p))
        }
      }
      setPhase('live')
    } catch (exc) {
      teardown()
      setPhase('idle')
      if ((exc as ApiError).notConfigured) setError(exc as ApiError)
      else setFailed((exc as Error).message || 'Could not start the call.')
    }
  }

  const hangUp = async () => {
    teardown()
    setPhase('ended')
    setSpeaking(false)
    if (convo.current) {
      await api.post(`/api/voice/sessions/${convo.current}/end`, {}).catch(() => {})
    }
  }

  const toggleMute = () => {
    const next = !muted
    mic.current?.getAudioTracks().forEach((t) => { t.enabled = !next })
    setMuted(next)
  }

  const missing = error?.notConfigured

  return (
    <div className="flex h-full items-center justify-center bg-muted/40 px-4 py-6">
      <Card className="flex max-h-[calc(100dvh-3rem)] w-full max-w-md flex-col p-6">
        <div className="text-center">
          <h1 className="text-lg font-semibold">Call Riverside Auto</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            {phase === 'live'
              ? 'Connected. Just talk -- you can interrupt at any time.'
              : 'Same assistant, same inventory, same booking -- out loud.'}
          </p>
        </div>

        <Meter live={phase === 'live'} speaking={speaking} />

        {/* Only rendered while there is something to show, so an idle page is
            the same calm card it always was. */}
        {lines.length > 0 && (
          <div
            ref={transcript}
            className="scroll-thin mt-4 min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border border-border bg-background p-3 text-left"
          >
            {lines.map((line) => (
              <p key={line.id} className="text-sm leading-relaxed">
                <span
                  className={clsx(
                    'mr-1.5 text-xs font-medium',
                    line.role === 'buyer' ? 'text-muted-foreground' : 'text-primary',
                  )}
                >
                  {line.role === 'buyer' ? 'You' : 'Liner'}
                </span>
                {line.text}
              </p>
            ))}
          </div>
        )}

        <div className="mt-5 shrink-0">
          {phase === 'live' ? (
            <div className="flex gap-2">
              <Button onClick={toggleMute} className="flex-1">
                {muted ? 'Unmute' : 'Mute'}
              </Button>
              <Button variant="destructive" onClick={hangUp} className="flex-1">
                Hang up
              </Button>
            </div>
          ) : (
            <Button
              variant="primary"
              onClick={start}
              disabled={phase === 'connecting'}
              className="w-full"
            >
              {phase === 'connecting'
                ? 'Connecting...'
                : phase === 'ended' ? 'Call again' : 'Start a call'}
            </Button>
          )}
        </div>

        {phase === 'connecting' && (
          <p className="mt-3 text-center text-xs text-muted-foreground">
            Your browser will ask for the microphone.
          </p>
        )}

        {missing && (
          <div className="mt-5 rounded-lg bg-warning-muted p-4 text-left">
            <p className="text-sm font-semibold text-warning-foreground">
              Voice is not configured
            </p>
            <p className="mt-1 text-sm text-warning-foreground/90">{missing.detail}</p>
            {missing.missing.length > 0 && (
              <p className="mt-2 text-xs text-warning-foreground/80">
                Missing:{' '}
                {missing.missing.map((key) => (
                  <code key={key} className="mr-1 font-mono">{key}</code>
                ))}
              </p>
            )}
          </div>
        )}

        {failed && <p className="mt-4 text-sm text-destructive">{failed}</p>}

        <Link to="/chat" className="mt-4 block text-center text-sm text-primary hover:underline">
          Use the chat instead
        </Link>
      </Card>

      {/* The model's audio. Not hidden with `display: none` -- Safari has
          historically refused to play a track on a detached element -- so it is
          present and simply has nothing to draw. */}
      <audio ref={audio} autoPlay className="hidden" />
    </div>
  )
}

/** Seven bars. Flat when idle, moving while Liner is speaking.
 *
 *  Driven by the provider's own audio-buffer events rather than by analysing
 *  the waveform: it is the difference between "is sound playing" and "did the
 *  server decide to speak", and on a call with any lag those are not the same
 *  moment. Purely decorative -- `aria-hidden`, because the transcript below is
 *  what a screen reader should follow. */
function Meter({ live, speaking }: { live: boolean; speaking: boolean }) {
  const heights = [10, 22, 34, 46, 34, 22, 10]
  return (
    <div className="my-6 flex h-12 items-center justify-center gap-1" aria-hidden="true">
      {heights.map((height, index) => (
        <span
          key={index}
          style={{
            height: speaking ? height : 10,
            transitionDelay: `${index * 60}ms`,
          }}
          className={clsx(
            'w-1.5 rounded-full transition-all duration-300',
            speaking ? 'bg-primary' : live ? 'bg-primary/40' : 'bg-border',
          )}
        />
      ))}
    </div>
  )
}
