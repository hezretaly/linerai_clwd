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
  greeting: string
  greeting_audio: string
  transcribed: boolean
}

interface Line {
  id: string
  role: 'buyer' | 'assistant'
  text: string
}

type Phase = 'idle' | 'connecting' | 'live' | 'ended'

/* A Bluetooth headset is the most common reason a call goes badly, and the
 * reason is not obvious enough for anyone to guess it. Opening the microphone
 * on AirPods forces the link out of A2DP into the hands-free profile, which
 * collapses *both* directions to telephone quality -- so the buyer sounds
 * broken to the model and the model sounds thin to the buyer, at once. The
 * cure is to leave the headset as output and take input from the built-in
 * microphone. Matched on the label because there is no capability flag for it. */
const BLUETOOTH = /airpods|bluetooth|\bbt\b|headset|beats|galaxy buds|\bwf-|\bwh-/i

/** Above this the meter counts as speech rather than room noise. Only used for
 *  the idle clock and the "hearing you" line -- the real turn detection happens
 *  at the provider, on the audio itself. */
const SPEECH = 0.06

/** Two minutes of silence in both directions ends the call. */
const IDLE_MS = 2 * 60 * 1000

/** What this browser can actually record, best first.
 *
 *  Not one format. Safari's MediaRecorder produces mp4 and Chrome's produces
 *  webm, and asking for the wrong one throws -- so the answer is discovered
 *  rather than assumed, and the type that won is uploaded with the file
 *  because serving mp4 bytes as webm plays silence. */
const RECORD_TYPES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/mp4',
  'audio/ogg;codecs=opus',
]

function recordableType(): string {
  const supported = (t: string) =>
    typeof MediaRecorder !== 'undefined' && MediaRecorder.isTypeSupported?.(t)
  return RECORD_TYPES.find(supported) ?? ''
}

/** The opening the buyer hears, before the microphone opens.
 *
 *  Played here rather than spoken by the model, for four reasons that all
 *  point the same way: it is the same words every call, it cannot be
 *  improvised into the customer's line, it cannot be cut off by the connection
 *  settling, and output audio is the dearest thing on a realtime call so not
 *  generating it every time is free money.
 *
 *  A tone until a real recording exists. Two notes rather than one because a
 *  single beep is a machine noise and a rising pair reads as "go ahead" --
 *  which is exactly the thing the buyer needs to know. */
async function preroll(ctx: AudioContext, url: string, into?: AudioNode | null) {
  if (url) {
    const element = new Audio(url)
    element.crossOrigin = 'anonymous'
    await element.play().catch(() => {})
    await new Promise<void>((done) => {
      element.onended = () => done()
      // A recording that never fires `onended` -- a bad URL, a stalled fetch
      // -- must not hold the microphone shut for the whole call.
      setTimeout(done, 8000)
    })
    return
  }

  const now = ctx.currentTime
  for (const [at, hz] of [[0, 660], [0.16, 990]] as [number, number][]) {
    const tone = ctx.createOscillator()
    const level = ctx.createGain()
    tone.frequency.value = hz
    // Ramped, not switched. A square edge on a sine is an audible click, and
    // a click is what a broken connection sounds like.
    level.gain.setValueAtTime(0, now + at)
    level.gain.linearRampToValueAtTime(0.18, now + at + 0.02)
    level.gain.linearRampToValueAtTime(0, now + at + 0.14)
    tone.connect(level)
    level.connect(ctx.destination)
    if (into) level.connect(into)
    tone.start(now + at)
    tone.stop(now + at + 0.16)
  }
  await new Promise((done) => setTimeout(done, 380))
}

function clock(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export function Call() {
  const [error, setError] = useState<ApiError | null>(null)
  const [failed, setFailed] = useState('')
  const [phase, setPhase] = useState<Phase>('idle')
  const [lines, setLines] = useState<Line[]>([])
  const [muted, setMuted] = useState(false)
  const [speaking, setSpeaking] = useState(false)
  const [level, setLevel] = useState(0)
  const [mics, setMics] = useState<MediaDeviceInfo[]>([])
  const [micId, setMicId] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [transcribed, setTranscribed] = useState(true)
  /** '' while deciding, then what is actually being captured. Shown, because
   *  a call that silently failed to record looks identical to one that did. */
  const [recording, setRecording] = useState<'both' | 'mic' | 'off'>('off')

  const pc = useRef<RTCPeerConnection | null>(null)
  const channel = useRef<RTCDataChannel | null>(null)
  const mic = useRef<MediaStream | null>(null)
  const meter = useRef<AudioContext | null>(null)
  const audio = useRef<HTMLAudioElement | null>(null)
  const convo = useRef('')
  const transcript = useRef<HTMLDivElement | null>(null)

  /* Only one response may be generating at a time.
   *
   * This shipped wrong and it sounded like the assistant changing voice
   * mid-sentence. A turn that called two tools fired
   * `response.function_call_arguments.done` twice, and each handler answered
   * with its own `response.create` -- so two responses generated audio into
   * the same track and the buyer heard both at once. The rule is: submit every
   * outstanding tool result, then ask for exactly one response, and only once
   * the response that requested them has finished. */
  const outstanding = useRef(0)
  const owed = useRef(false)
  const generating = useRef(false)

  /* The microphone is held shut until the greeting has been spoken.
   *
   * This shipped wrong and it produced two greetings. The first was cut off
   * mid-word -- "Let me know what" -- because something triggered the turn
   * detector while it was still talking: the Bluetooth link switching profile,
   * gain settling, or the greeting itself leaking back. Interrupting cancels
   * the response, and the silence afterwards is a completed turn, so the
   * server generated a fresh greeting. Nobody had said anything.
   *
   * Nothing is lost by waiting. A buyer cannot meaningfully interrupt a
   * sentence they have not heard yet, and from the moment it finishes they can
   * cut in whenever they like. */
  const micOpen = useRef(false)
  const openTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)

  /* Set when close_conversation succeeds. The buyer said they were done, so the
   * goodbye is allowed to finish and then the line goes down. */
  const closing = useRef(false)
  const lastHeard = useRef(Date.now())
  // Read from the event handlers that open the mic, where the `muted` state
  // captured at render time may be a version ago.
  const mutedRef = useRef(false)

  /* Both halves of the call, mixed into one track and recorded.
   *
   * Mixed rather than two files: a rep listening back wants the conversation,
   * not two monologues to line up by hand. The mix happens in the same
   * AudioContext the level meter already runs in -- a second context is a
   * second lot of audio hardware for no gain. */
  const mixer = useRef<MediaStreamAudioDestinationNode | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const chunks = useRef<Blob[]>([])
  const startedAt = useRef(0)

  // Hanging up on unmount is not tidiness. Without it, navigating away leaves
  // the microphone live and the meter lit in the browser chrome, which is the
  // single most alarming thing a website can do.
  useEffect(() => () => teardown(), [])

  useEffect(() => {
    // Labels are blank until permission has been granted once, so this fills
    // in properly only after the first call. Listed anyway: an unnamed device
    // is still selectable, and on most machines the order is stable.
    void navigator.mediaDevices?.enumerateDevices?.()
      .then((all) => setMics(all.filter((d) => d.kind === 'audioinput')))
      .catch(() => {})
  }, [phase])

  useEffect(() => {
    transcript.current?.scrollTo({ top: transcript.current.scrollHeight })
  }, [lines])

  /* A closed tab is the most likely way a call ends, and it used to take the
   * recording with it: `hangUp` never runs, so nothing is ever uploaded.
   * `sendBeacon` is the only thing a page can rely on during unload -- a
   * normal fetch is cancelled with the document. */
  useEffect(() => {
    const rescue = () => {
      const rec = recorder.current
      if (!rec || rec.state === 'inactive' || !convo.current) return
      rec.requestData()
      rec.stop()
      if (!chunks.current.length) return
      const form = new FormData()
      const type = rec.mimeType.split(';')[0]
      form.append('file', new File(chunks.current, 'call', { type }), 'call')
      navigator.sendBeacon(
        `/api/voice/recording/${convo.current}` +
        `?duration_ms=${Math.max(Date.now() - startedAt.current, 0)}`,
        form,
      )
      chunks.current = []
      recorder.current = null
    }
    window.addEventListener('pagehide', rescue)
    return () => window.removeEventListener('pagehide', rescue)
  }, [])

  /* Nobody has said anything for a long time, in either direction.
   *
   * A forgotten tab is not a harmless one here: the microphone stays live and
   * the provider bills by the minute, so a call left open on a phone in a
   * pocket is both a privacy problem and a bill. Long enough that a caller
   * thinking, or reading their own email back to themselves, is never cut off. */
  useEffect(() => {
    if (phase !== 'live') return
    const timer = setInterval(() => {
      setSeconds(Math.floor((Date.now() - startedAt.current) / 1000))
      if (Date.now() - lastHeard.current > IDLE_MS) void hangUp()
    }, 1000)
    return () => clearInterval(timer)
  }, [phase])

  const teardown = () => {
    clearTimeout(openTimer.current)
    micOpen.current = false
    closing.current = false
    channel.current?.close()
    pc.current?.close()
    mic.current?.getTracks().forEach((t) => t.stop())
    void meter.current?.close().catch(() => {})
    channel.current = null
    pc.current = null
    mic.current = null
    meter.current = null
    mixer.current = null
    setLevel(0)
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

  /** The microphone is live only when it is open *and* not muted. One place
   *  decides, because two booleans over the same tracks is how Unmute turns
   *  the mic on before the greeting has finished. */
  const applyMic = () => {
    mic.current?.getAudioTracks().forEach((t) => {
      t.enabled = micOpen.current && !mutedRef.current
    })
  }

  const openMic = () => {
    if (micOpen.current) return
    clearTimeout(openTimer.current)
    micOpen.current = true
    applyMic()
  }

  /** Ask for one reply, once everything it is waiting on has been answered. */
  const respondWhenReady = () => {
    if (outstanding.current > 0 || !owed.current || generating.current) return
    owed.current = false
    channel.current?.send(JSON.stringify({ type: 'response.create' }))
  }

  /** Tool calls come back over the data channel and are executed on our side.
   *
   *  This is the part that makes a call as safe as a chat. The model asks for
   *  `search_inventory`; the executor decides what it gets, so a do-not-discuss
   *  vehicle never reaches it whatever the prompt says. */
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
    // The buyer said they were done and the executor agreed. Saying goodbye is
    // not hanging up -- the line, and their microphone, stay open until
    // something closes them. The goodbye is allowed to finish first.
    if (name === 'close_conversation' && (output as any)?.result?.closed) {
      closing.current = true
    }
    outstanding.current = Math.max(outstanding.current - 1, 0)
    respondWhenReady()
  }

  const onEvent = (event: Record<string, any>) => {
    switch (event.type) {
      case 'response.created':
        generating.current = true
        break
      case 'response.done':
        generating.current = false
        // What that response cost, straight from the provider. Relayed rather
        // than estimated from wall-clock: a realtime call bills the whole
        // conversation so far as input on every turn, so per-turn counts are
        // the only way to see the curve -- and the curve, not the per-minute
        // average, is what a bill is made of.
        if (event.response?.usage) {
          void api.post('/api/voice/usage', {
            conversation_id: convo.current,
            response_id: event.response.id ?? '',
            usage: event.response.usage,
          }).catch(() => {
            /* Never at the expense of the call. A missing cost row is a gap in
               a report; a failed call is a lost buyer. */
          })
        }
        // The response that asked for the tools has finished emitting them, so
        // this is the earliest moment a new one may be requested.
        respondWhenReady()
        break
      case 'response.function_call_arguments.done':
        outstanding.current += 1
        owed.current = true
        void runTool(event.name, event.call_id, event.arguments)
        break
      // The buyer's own words. A side channel: the model hears the raw audio
      // and never reads this, so a wrong transcript means a poor microphone
      // rather than an assistant that misunderstood.
      case 'conversation.item.input_audio_transcription.completed':
        say('buyer', event.transcript || '')
        break
      case 'response.output_audio_transcript.done':
        say('assistant', event.transcript || '')
        break
      case 'output_audio_buffer.started':
        setSpeaking(true)
        lastHeard.current = Date.now()
        break
      case 'output_audio_buffer.stopped':
      case 'output_audio_buffer.cleared':
        setSpeaking(false)
        lastHeard.current = Date.now()
        // The greeting is over, so the buyer may now be heard. Anything
        // earlier is the connection settling, and the turn detector reads that
        // as an interruption.
        openMic()
        if (closing.current) void hangUp()
        break
      case 'error':
        setFailed(event.error?.message || 'The provider reported an error.')
        break
      default:
        break
    }
  }

  /** A live input level, which is the only way to answer "can it hear me?".
   *
   *  Without this, a microphone that is muted at the operating system, or a
   *  headset that handed over a dead input, is indistinguishable from an
   *  assistant that is ignoring you. */
  const watchLevel = (stream: MediaStream) => {
    const ctx = new AudioContext()
    meter.current = ctx
    const analyser = ctx.createAnalyser()
    analyser.fftSize = 512
    const source = ctx.createMediaStreamSource(stream)
    source.connect(analyser)

    // The recording mix. The buyer goes in here; Liner joins when the remote
    // track arrives, which is after this runs -- a WebAudio node can be
    // connected to a live destination at any time, so the late arrival costs
    // nothing but the first half-second of ringing.
    const mix = ctx.createMediaStreamDestination()
    mixer.current = mix
    source.connect(mix)
    // Safari starts an AudioContext suspended unless it was created inside a
    // user gesture, and `start()` has awaited getUserMedia by the time this
    // runs -- a suspended context produces silence through the mix, and the
    // level meter reads zero with it.
    void ctx.resume().catch(() => {})
    startRecording(mix.stream, stream)
    const buffer = new Uint8Array(analyser.frequencyBinCount)
    const tick = () => {
      if (meter.current !== ctx) return
      analyser.getByteTimeDomainData(buffer)
      let peak = 0
      for (const sample of buffer) peak = Math.max(peak, Math.abs(sample - 128))
      const loudness = Math.min(peak / 60, 1)
      setLevel(loudness)
      if (loudness > SPEECH) lastHeard.current = Date.now()
      requestAnimationFrame(tick)
    }
    tick()
  }

  /** Record the call, and say which half of it if not both.
   *
   *  Every step here can fail on its own, and every one of them used to fail
   *  silently -- which is how a finished call showed "No audio was captured"
   *  with nothing to suggest why. A browser may have no MediaRecorder, may
   *  support no container this can produce, or may refuse to record a stream
   *  that came out of WebAudio (Safari has, historically). The mixed stream is
   *  tried first and the bare microphone is the fallback, because half a
   *  conversation is worth having and none of it is not. */
  const startRecording = (mixed: MediaStream, raw: MediaStream) => {
    const type = recordableType()
    if (!type) {
      setRecording('off')
      return
    }
    for (const [stream, kind] of [[mixed, 'both'], [raw, 'mic']] as const) {
      try {
        const rec = new MediaRecorder(stream, { mimeType: type })
        chunks.current = []
        rec.ondataavailable = (e) => { if (e.data.size) chunks.current.push(e.data) }
        // A timeslice, so a tab closed mid-call still leaves most of the audio
        // in hand rather than one chunk that was never flushed.
        rec.start(5000)
        recorder.current = rec
        setRecording(kind)
        return
      } catch {
        /* Try the next one. */
      }
    }
    setRecording('off')
  }

  /** Stop and upload. Awaited on hang-up so the last chunk is included. */
  const finishRecording = async (conversationId: string) => {
    const rec = recorder.current
    recorder.current = null
    if (!rec || rec.state === 'inactive') return
    const done = new Promise<void>((resolve) => { rec.onstop = () => resolve() })
    rec.stop()
    await done
    if (!chunks.current.length) return
    const blob = new Blob(chunks.current, { type: rec.mimeType.split(';')[0] })
    chunks.current = []
    const ms = Math.max(Date.now() - startedAt.current, 0)
    try {
      await api.upload(
        `/api/voice/recording/${conversationId}?duration_ms=${ms}`,
        new File([blob], 'call', { type: blob.type }),
      )
    } catch {
      /* The call happened whether or not its audio was filed. */
    }
  }

  const start = async () => {
    setError(null)
    setFailed('')
    setLines([])
    setPhase('connecting')
    startedAt.current = Date.now()
    setSeconds(0)
    mutedRef.current = false
    setMuted(false)
    outstanding.current = 0
    owed.current = false
    generating.current = false
    try {
      // Ours, and the only request that carries the real key. What comes back
      // is good for about a minute and for one call.
      const session = await api.post<Session>('/api/voice/sessions')
      convo.current = session.conversation_id

      // Constraints, not `audio: true`. Echo cancellation is the load-bearing
      // one: without it the model hears its own voice coming back, transcribes
      // it as the buyer, and answers itself.
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          ...(micId ? { deviceId: { exact: micId } } : {}),
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      })
      mic.current = stream
      watchLevel(stream)
      // Labels are only readable once permission exists, so the picker below
      // is populated with real names from here on.
      void navigator.mediaDevices.enumerateDevices()
        .then((all) => setMics(all.filter((d) => d.kind === 'audioinput')))
        .catch(() => {})

      const connection = new RTCPeerConnection({
        // One public STUN server. The SDP exchange here is a single HTTP round
        // trip with nowhere to trickle a late candidate to, so anything not in
        // the offer is lost -- and on a network that needs a reflexive
        // candidate, that is the whole connection.
        iceServers: [{ urls: 'stun:stun.l.google.com:19302' }],
      })
      pc.current = connection

      connection.ontrack = (e) => {
        // Into the recording mix as well as into the speaker. Safari needs the
        // stream attached to a media element before a MediaStreamSource on it
        // produces anything, which the line below happens to satisfy.
        if (meter.current && mixer.current) {
          try {
            meter.current.createMediaStreamSource(e.streams[0]).connect(mixer.current)
          } catch {
            /* One-sided audio is worth having; no call is not. */
          }
        }
        if (!audio.current) return
        audio.current.srcObject = e.streams[0]
        // Safari will not autoplay a stream attached after the click that
        // started this, even though a gesture did occur. Asking explicitly is
        // the difference between a call and a silent one.
        void audio.current.play().catch(() => {})
      }
      // Added but not yet sending. Opened on the first
      // `output_audio_buffer.stopped`, and unconditionally after six seconds so
      // that a greeting which never plays cannot leave a caller unheard.
      micOpen.current = false
      stream.getAudioTracks().forEach((t) => { t.enabled = false })
      stream.getTracks().forEach((track) => connection.addTrack(track, stream))
      openTimer.current = setTimeout(openMic, 6000)

      const events = connection.createDataChannel('oai-events')
      channel.current = events
      events.onmessage = (e) => onEvent(JSON.parse(e.data))
      // Nothing is asked of the model here. The buyer hears the pre-roll
      // below, then speaks, and the model answers that -- so it never has to
      // invent an opening, which is where it invented the customer's.
      setTranscribed(session.transcribed !== false)

      const offer = await connection.createOffer()
      await connection.setLocalDescription(offer)
      // Wait for the candidates before posting. There is no second channel to
      // send them on afterwards, so an offer posted the instant the local
      // description is set advertises no way to reach this browser -- which
      // shows up as a call that takes seconds to connect, or does not.
      await gathered(connection)

      const answer = await fetch(`${session.calls_url}?model=${encodeURIComponent(session.model)}`, {
        method: 'POST',
        body: connection.localDescription?.sdp ?? offer.sdp,
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

      // The line is up: greet, then listen. Awaited, so the microphone opens
      // only once the buyer has actually heard the opening -- otherwise the
      // turn detector fires on the greeting itself.
      if (meter.current) {
        await preroll(meter.current, session.greeting_audio || '', mixer.current)
      }
      openMic()
    } catch (exc) {
      teardown()
      setPhase('idle')
      if ((exc as ApiError).notConfigured) setError(exc as ApiError)
      else setFailed((exc as Error).message || 'Could not start the call.')
    }
  }

  const hangUp = async () => {
    // Before teardown: stopping the microphone first would cut the last few
    // seconds off the recording, which is usually the part with the
    // appointment in it.
    const id = convo.current
    await finishRecording(id)
    teardown()
    setPhase('ended')
    setSpeaking(false)
    if (id) {
      await api.post(`/api/voice/sessions/${id}/end`, {}).catch(() => {})
    }
  }

  const toggleMute = () => {
    const next = !muted
    mutedRef.current = next
    // Through applyMic, so pressing Unmute during the greeting does not open
    // the microphone before the greeting it would interrupt has finished.
    applyMic()
    setMuted(next)
  }

  const missing = error?.notConfigured
  const chosen = mics.find((d) => d.deviceId === micId) ?? mics[0]
  const overBluetooth = Boolean(chosen?.label && BLUETOOTH.test(chosen.label))

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
          {(phase === 'live' || phase === 'ended') && (
            <p className="tnum mt-2 text-2xl font-semibold">{clock(seconds)}</p>
          )}
        </div>

        <Meter live={phase === 'live'} speaking={speaking} level={level} />

        {phase === 'live' && (
          <p className="-mt-4 mb-2 text-center text-xs text-muted-foreground">
            {muted
              ? 'Muted.'
              : level > SPEECH
                ? 'Hearing you.'
                : 'Not picking anything up -- check the microphone below.'}
          </p>
        )}

        {phase === 'live' && !transcribed && (
          /* A one-sided transcript is the most confusing thing this page can
             show without explaining itself: Liner's half alone reads exactly
             like an assistant holding a conversation with nobody. */
          <p className="-mt-2 mb-2 text-center text-xs text-muted-foreground">
            Only Liner&apos;s side is written down -- VOICE_TRANSCRIBE is off.
          </p>
        )}

        {lines.length > 0 && (
          <div
            ref={transcript}
            className="scroll-thin mt-2 min-h-0 flex-1 space-y-2 overflow-y-auto rounded-lg border border-border bg-background p-3 text-left"
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

        {mics.length > 1 && (
          <label className="mt-4 block text-left">
            <span className="text-xs font-medium text-muted-foreground">Microphone</span>
            <select
              value={micId || mics[0]?.deviceId || ''}
              onChange={(e) => setMicId(e.target.value)}
              disabled={phase === 'live' || phase === 'connecting'}
              className="mt-1 h-9 w-full rounded-md border border-input bg-background px-2.5 text-sm outline-none focus:border-ring focus:ring-1 focus:ring-ring disabled:opacity-60"
            >
              {mics.map((device, index) => (
                <option key={device.deviceId} value={device.deviceId}>
                  {device.label || `Microphone ${index + 1}`}
                </option>
              ))}
            </select>
          </label>
        )}

        {overBluetooth && (
          /* Not a warning about our own software. This is what wireless
             earbuds do to any call, in any app, and the fix is a dropdown away
             -- so it is worth saying plainly rather than letting a buyer
             conclude the assistant is broken. */
          <div className="mt-3 rounded-md border border-warning/30 bg-warning-muted p-2.5 text-left">
            <p className="text-xs leading-relaxed text-warning-foreground">
              Using a wireless headset&apos;s microphone drops the whole call to
              telephone quality in both directions -- your voice arrives broken and
              the reply sounds thin. Pick the built-in microphone above and keep
              the earbuds for listening.
            </p>
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

        {/* Before the button, and unconditional. Several US states require
            every party to a call to consent to being recorded; whether that
            applies is the dealership's call to take with its own counsel, but
            nobody should be recorded without being told, and this is the only
            place a buyer would ever read it. */}
        {phase !== 'ended' && (
          <p className="mt-3 text-center text-xs text-muted-foreground">
            {phase === 'live' && recording === 'off'
              // Said out loud rather than discovered afterwards on an empty
              // player. A browser that cannot record is a fact about the
              // browser, not a fault in the call.
              ? 'This browser cannot record audio, so this call is transcript only.'
              : phase === 'live' && recording === 'mic'
                ? 'Recording your side only -- this browser will not mix in the reply.'
                : 'Calls are recorded so the team can follow up accurately.'}
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
      <audio ref={audio} autoPlay playsInline className="hidden" />
    </div>
  )
}

/** ICE gathering, or two seconds, whichever comes first.
 *
 *  Two seconds because a hung STUN server must not hold a call hostage: an
 *  offer with host candidates only still connects on most networks, and a
 *  degraded connection beats a button that never responds. */
function gathered(connection: RTCPeerConnection): Promise<void> {
  if (connection.iceGatheringState === 'complete') return Promise.resolve()
  return new Promise((resolve) => {
    const done = () => {
      clearTimeout(timer)
      connection.removeEventListener('icegatheringstatechange', onChange)
      resolve()
    }
    const onChange = () => {
      if (connection.iceGatheringState === 'complete') done()
    }
    const timer = setTimeout(done, 2000)
    connection.addEventListener('icegatheringstatechange', onChange)
  })
}

/** Seven bars: the buyer's own voice while they speak, Liner's while it does.
 *
 *  Two signals, one meter, because they never happen at once and a caller
 *  needs both answers from the same place -- "is it hearing me" and "is it
 *  talking". The input half is measured from the microphone; the output half
 *  comes from the provider's audio-buffer events rather than from analysing
 *  the returning waveform, because that is the difference between "sound is
 *  playing" and "it decided to speak". */
function Meter({
  live,
  speaking,
  level,
}: {
  live: boolean
  speaking: boolean
  level: number
}) {
  const heights = [10, 22, 34, 46, 34, 22, 10]
  return (
    <div className="my-6 flex h-12 items-center justify-center gap-1" aria-hidden="true">
      {heights.map((height, index) => (
        <span
          key={index}
          style={{
            height: speaking
              ? height
              : live ? Math.max(10, height * level) : 10,
            transitionDelay: speaking ? `${index * 60}ms` : '0ms',
          }}
          className={clsx(
            'w-1.5 rounded-full transition-all',
            speaking ? 'bg-primary duration-300'
              : live ? 'bg-primary/60 duration-75' : 'bg-border duration-300',
          )}
        />
      ))}
    </div>
  )
}
