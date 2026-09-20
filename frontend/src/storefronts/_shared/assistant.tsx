import { createContext, useContext } from 'react'

import { withStore } from '../../lib/store'
import type { Car } from './types'

/**
 * The one thing every storefront shares about the assistant: how it is
 * opened, and that it is the real `/chat` in an iframe.
 *
 * **A storefront designs its own bubble, panel and placement.** Alsbou's is a
 * gold circle bottom-right; the next dealership's may be a bar across the
 * bottom or a tab on the side. What is not theirs to redesign is the frame
 * inside it: an iframe of `/chat?embed=1` on the same origin, with the store
 * prefix on it, so the widget and the full page are one conversation and a
 * second chat client cannot quietly drop the booking card the real one
 * renders. `ChatFrame` is that iframe, and it is the only way a storefront
 * gets the assistant.
 *
 * **The question a card opens it with is part of the iframe's URL**, never a
 * message posted into the frame -- a second channel into it would be a second
 * way for a storefront to write into a buyer's transcript. And it is *typed*
 * into the composer rather than sent: a sentence sent on the buyer's behalf is
 * in the transcript as their own words, which is the rule the rails follow.
 */

export interface Assistant {
  /** Open the widget with nothing typed. */
  open: () => void
  /** Open it already asking about one car. */
  askAbout: (car: Car) => void
}

export const AssistantContext = createContext<Assistant>({ open: () => {}, askAbout: () => {} })

/** The widget's controls, for anything inside a storefront that opens it. */
export function useAssistant(): Assistant {
  return useContext(AssistantContext)
}

/** The sentence a card opens the assistant with. The VIN is in it because a
 *  lot with three 2019 Silverados has three cards that would otherwise send
 *  the same words, and `search_inventory` matches a VIN exactly. */
export function askAboutText(car: Car): string {
  return `Tell me about the ${car.title}${car.trim ? ` ${car.trim}` : ''} (VIN ${car.vin}).`
}

/** The real `/chat`, embedded. Keyed on the question so pressing a second
 *  card's button reloads the frame with that car's sentence in the box -- the
 *  transcript comes back from localStorage, so nothing said is lost. Mount it
 *  on the first open and keep it: an iframe that exists from first paint
 *  starts a conversation for every visitor who never clicked, and one that
 *  is torn down on close reloads its document and rebuilds the thread on
 *  every reopen. The storefront hides the widget; it does not unmount it. */
export function ChatFrame({ ask, className }: { ask: string; className?: string }) {
  return (
    <iframe
      key={ask}
      src={withStore(`/chat?embed=1${ask ? `&ask=${encodeURIComponent(ask)}` : ''}`)}
      title="Chat"
      className={className}
    />
  )
}
