/* Hand-written to match app/schemas/serialize.py. One shaping layer on each
 * side is easier to keep honest than a generator nobody re-runs. */

/* 'adf' is written only by the lead importer, from a document a dealer
 * uploaded. The agent cannot claim it -- save_captured_fields takes the four
 * conversational values and nothing else. */
export type Provenance = 'typed' | 'listing' | 'caller_id' | 'inferred' | 'adf'
export type Stage =
  | 'opening' | 'browsing' | 'vehicle_focus' | 'objection'
  | 'qualifying' | 'slot_offered' | 'contact_capture' | 'booked' | 'escalated'

export interface User {
  id: string
  name: string
  email: string
  role: 'manager' | 'rep'
  avatar_initials: string
  daily_cap: number
  notify_channel: 'email' | 'dashboard'
  active: boolean
}

export interface TeamMember extends User {
  todays_appointments: number
  at_capacity: boolean
  next_free_at: string
}

export interface Hours { open: string; close: string }

export interface Dealership {
  id: string
  name: string
  timezone: string
  hours: Record<string, Hours | null>
  address: string
  phone: string
  website_url: string
}

export interface Vehicle {
  id: string
  vin: string
  year: number
  make: string
  model: string
  trim: string
  title: string
  price: number | null
  mileage: number | null
  body_style: string
  seats: number | null
  title_status: string
  features: string[]
  photo_url: string
  listing_url: string
  status: 'available' | 'sold' | 'removed'
  source: 'scrape' | 'manual' | 'seed'
  rules: { discuss: boolean; hold_price: boolean; mention_warranty: boolean; note: string }
  manual_fields: string[]
  mention_count: number
  first_seen_at: string | null
  last_seen_at: string | null
  quoted_to?: number
  mentions?: VehicleMention[]
}

export interface VehicleMention {
  conversation_id: string
  lead_id: string | null
  lead_name: string
  quoted_price: number | null
  created_at: string
}

export interface CapturedField {
  id: string
  key: string
  value: string
  provenance: Provenance
  verified: boolean
  updated_at: string | null
}

export interface Lead {
  id: string
  name: string
  email: string
  phone: string
  source: 'chat' | 'phone' | 'website' | 'adf'
  assigned_user_id: string | null
  assigned_to?: User | null
  contact_risk: boolean
  email_consent_at: string | null
  created_at: string
  captured_fields?: CapturedField[]
  /* Folded on by the list endpoint. A lead has no stage column -- these are
     derived from its conversations and appointments (api/leads.py). */
  stage?: 'new' | 'qualifying' | 'qualified' | 'appointment'
  flagged?: boolean
  vehicle_of_interest?: Vehicle | null
  appointment_count?: number
  unconfirmed_count?: number
  last_touch_at?: string
  conversation_id?: string | null
  appointments?: Appointment[]
  conversations?: Conversation[]
  outreach?: Outreach[]
}

export interface ToolCall { name: string; input?: unknown; result?: unknown }

export interface Message {
  id: string
  role: 'buyer' | 'assistant' | 'rep'
  content: string
  tool_calls: ToolCall[]
  via_rail_id: string | null
  created_at: string
}

export interface Conversation {
  id: string
  lead_id: string | null
  lead?: Lead | null
  channel: 'chat' | 'voice'
  status: 'active' | 'handoff' | 'closed'
  agent_paused: boolean
  stage: Stage
  focus_vehicle_id: string | null
  focus_vehicle?: Vehicle | null
  started_at: string
  ended_at: string | null
  summary: string
  message_count?: number
  messages?: Message[]
  open_escalation?: Escalation | null
  rails?: Rail[]
}

export interface Appointment {
  id: string
  lead_id: string
  lead?: Lead | null
  vehicle_id: string | null
  vehicle?: Vehicle | null
  assigned_user_id: string | null
  assigned_to?: User | null
  starts_at: string
  duration_min: number
  status: 'booked' | 'confirmed' | 'cancelled' | 'no_show'
  booked_by: 'liner' | 'rep'
  conversation_id: string | null
  created_at: string
  outreach?: Outreach[]
}

export interface Outreach {
  id: string
  appointment_id: string | null
  lead_id: string | null
  sent_by_user_id: string | null
  channel: 'email' | 'phone_logged'
  to_address: string
  subject: string
  body: string
  provider: string
  provider_message_id: string | null
  provider_thread_id: string | null
  status: 'queued' | 'sent' | 'bounced' | 'failed'
  /* False for the local outbox: the row exists, no mail was delivered. */
  delivered_externally: boolean
  kind: string
  /* The send carried a /r/<token> link, so a click can be counted at all.
     False means there was nothing to follow -- not that nobody followed it. */
  trackable: boolean
  opened: boolean
  click_count: number
  first_clicked_at: string | null
  error: string
  sent_at: string | null
  created_at: string
}

export interface HandoffRule {
  id: string
  key: string
  label: string
  description: string
  enabled: boolean
  threshold_value: number | null
  threshold_unit: string
  route_target: string
  notify: 'email_dashboard' | 'dashboard'
  fired_count: number
  updated_at: string | null
}

export interface Escalation {
  id: string
  conversation_id: string
  handoff_rule_id: string | null
  reason: string
  claimed_by_user_id: string | null
  claimed_at: string | null
  created_at: string
  rule?: HandoffRule | null
  channel?: string | null
  lead?: Lead | null
  vehicle?: Vehicle | null
}

export interface KnowledgeEntry {
  id: string
  topic: string
  answer: string
  use_count: number
  updated_at: string | null
}

export interface Rail {
  id: string
  kind: 'opener' | 'followup' | 'knowledge'
  stage: string
  label: string
  message_text: string
  requires_vehicle: boolean
  knowledge_entry_id: string | null
  advances_to: string
  sort_order: number
  enabled: boolean
}

export interface AssistantSettings {
  id: string
  version: number
  status: 'draft' | 'live' | 'archived'
  tone: string
  push_level: string
  price_mode: string
  discount_pct: number
  financing_mode: string
  after_hours_mode: string
  greeting: string
  booking_slot_length: number
  credit_application_url: string
  published_by: string | null
  published_at: string | null
  updated_at: string | null
}

export interface Kpi {
  key: string
  label: string
  value: number
  window: string
  /** The count is real but the feature behind it is not set up, so the window
   *  line says why instead of a zero reading as a quiet day. */
  unavailable?: boolean
}

export interface Overview {
  dealership: Dealership
  generated_at: string
  kpis: Kpi[]
  badges: { conversations: number; appointments: number; escalations: number; inventory: number }
  queues: {
    needs_a_person: Escalation[]
    unconfirmed_appointments: Appointment[]
    unassigned_appointments: Appointment[]
    /** Today's conversations, newest activity first. Split at
     *  `happening_now_since` -- the panel shows the last two hours and
     *  expands to the rest of the day. */
    active_conversations: (Conversation & { last_activity_at?: string })[]
    unclaimed_leads: Lead[]
    inventory_issues: Vehicle[]
  }
  mix: { channel: string; count: number }[]
  source_mix: { source: string; count: number }[]
  happening_now_since: string
  by_hour: { hour: number; count: number; open: boolean }[]
}

export interface Integration {
  key: string
  label: string
  configured: boolean
  impl: string
  missing: string[]
  detail: string
}

export interface IntegrationsPayload {
  integrations: Integration[]
  unconfigured: string[]
  demo_mode: boolean
  llm_mode: string
}

/** One <prospect> from an ADF document, after parsing and matching. */
export interface Prospect {
  name: string
  email: string
  phone: string
  provider: string
  requested_at: string
  comments: string
  timeframe: string
  vehicle_year: number | null
  vehicle_make: string
  vehicle_model: string
  vehicle_trim: string
  vehicle_vin: string
  vehicle_stock: string
  vehicle_label: string
  warnings: string[]
  source: string
  /** Same email or phone already on file -- importing merges rather than duplicates. */
  existing_lead: Lead | null
  /** The car they asked about, if it is genuinely on the lot. */
  in_stock: Vehicle | null
}

export interface AdfPreview {
  filename: string
  prospects: Prospect[]
  errors: { row?: number; error: string }[]
  found: number
}

export interface LeadDraft {
  kind: 'reminder' | 'follow_up' | 'credit_application'
  to: string
  subject: string
  body: string
  appointment_id: string | null
  note?: string
}

export interface IngestRun {
  id: string
  source_url: string
  method: string
  status: 'pending' | 'ready' | 'published' | 'failed'
  listings_found: number
  created_count: number
  updated_count: number
  removed_count: number
  diff: {
    created?: Record<string, unknown>[]
    updated?: { vin: string; changes: Record<string, { from: unknown; to: unknown }>; protected: string[] }[]
    removed?: { vin: string; title: string }[]
  }
  errors: { url?: string; row?: number; error: string }[]
  started_at: string
  finished_at: string | null
}
