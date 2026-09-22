import { StrictMode, Suspense, lazy, useEffect, type ComponentType } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import './styles/liner-theme.css'

import { BuyerTheme } from './components/BuyerTheme'
import { BASENAME } from './lib/store'
import { RequireAuth } from './routes/RequireAuth'
import { Login } from './routes/Login'
import { Chat } from './routes/Chat'
import { StorefrontPage } from './routes/StorefrontPage'
import { RequireOwner } from './routes/ops/RequireOwner'
import { Spinner } from './components/ui'

/** A route loaded when it is first visited, not with the page.
 *
 *  `/chat` is an iframe on a dealership's storefront, and it used to pull the
 *  whole dashboard -- every dealer page, the ops pages, the calendar, the
 *  charts -- into one 880 KB bundle before it could draw a greeting. A buyer
 *  pressing "Chat with us" waited on code no buyer ever runs. The dealer and
 *  ops routes are their own chunks now; the buyer surfaces stay in the main
 *  one because they are the page.
 */
function page<M, P extends object>(load: () => Promise<M>, pick: (m: M) => ComponentType<P>) {
  return lazy(() => load().then((m) => ({ default: pick(m) })))
}

const Call = page(() => import('./routes/Call'), (m) => m.Call)
const AppShell = page(() => import('./components/dashboard/AppShell'), (m) => m.AppShell)
const OverviewPage = page(() => import('./routes/Overview'), (m) => m.OverviewPage)
const ConversationListPage = page(
  () => import('./routes/ConversationList'), (m) => m.ConversationListPage,
)
const LeadPage = page(() => import('./routes/LeadPage'), (m) => m.LeadPage)
const LeadRedirect = page(() => import('./routes/LeadPage'), (m) => m.LeadRedirect)
const CampaignsPage = page(() => import('./routes/Campaigns'), (m) => m.CampaignsPage)
const LeadImportPage = page(() => import('./routes/LeadImport'), (m) => m.LeadImportPage)
const CalendarPage = page(() => import('./routes/Calendar'), (m) => m.CalendarPage)
const InventoryPage = page(() => import('./routes/Inventory'), (m) => m.InventoryPage)
const ImportPage = page(() => import('./routes/Import'), (m) => m.ImportPage)
const AssistantPage = page(() => import('./routes/Assistant'), (m) => m.AssistantPage)
const TeamPage = page(() => import('./routes/Team'), (m) => m.TeamPage)
const OpsShell = page(() => import('./routes/ops/OpsShell'), (m) => m.OpsShell)
const OpsCalendarPage = page(() => import('./routes/ops/OpsCalendar'), (m) => m.OpsCalendarPage)
const OpsMailPage = page(() => import('./routes/ops/OpsMail'), (m) => m.OpsMailPage)
const OpsPhonePage = page(() => import('./routes/ops/OpsPhone'), (m) => m.OpsPhonePage)

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
})

/**
 * `/` is a standalone document served by Vite, not a route in here, so an
 * unknown path has to *leave* the SPA. A client-side <Navigate to="/"> would
 * re-enter this same catch-all and loop forever.
 */
function LeaveToLanding() {
  useEffect(() => {
    window.location.replace('/')
  }, [])
  return null
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      {/* `/alsbou/app` and `/app` are the same routes, one store apart. The
          basename is what keeps every <Link> inside the dealership the page
          was opened for -- without it the first navigation silently drops the
          prefix and lands on the default store's data. */}
      <BrowserRouter basename={BASENAME}>
        <Suspense fallback={<Spinner />}>
        <Routes>
          {/* Buyer surfaces keep the brand blue. /login is a dealer screen and
              deliberately stays on classic. */}
          {/* The dealership's own front page and their inventory list, in
              that dealership's own design: `storefronts/<slug>/`, resolved
              from the store in the URL, with a plain default for a dealership
              that has no folder yet. Each scopes .theme-buyer itself, since
              the whole page is a buyer surface rather than a route wrapped in
              one.

              `/` here is only ever reached *with a store prefix*. Unprefixed,
              `/` is Liner's own marketing document -- Vite rewrites it to
              landing.html before the SPA sees it, and `static.py` serves the
              file directly in production -- so this route is the dealership's
              root and never ours. `make smoke` asserts both. */}
          <Route path="/" element={<StorefrontPage kind="landing" />} />
          <Route path="/showroom" element={<StorefrontPage kind="showroom" />} />
          <Route path="/showroom/:vin" element={<StorefrontPage kind="vehicle" />} />
          <Route path="/chat" element={<BuyerTheme><Chat /></BuyerTheme>} />
          <Route path="/call" element={<BuyerTheme><Call /></BuyerTheme>} />
          <Route path="/login" element={<Login />} />

          <Route
            path="/app"
            element={
              <RequireAuth>
                <AppShell />
              </RequireAuth>
            }
          >
            <Route index element={<OverviewPage />} />
            {/* One list of people, one page per person. Chat, Calls and Email
                were three pages over the same buyer, so the same thread was
                readable in three places and a rep could ring someone who had
                already booked. */}
            <Route path="conversations" element={<ConversationListPage />} />
            {/* An anonymous thread -- no lead until someone books -- opens on
                its own. One that does have a lead redirects to the buyer, so a
                thread is never readable in two places. Every old link into
                /app/conversations/:id still lands somewhere right. */}
            <Route path="conversations/:id" element={<LeadRedirect />} />
            <Route path="leads" element={<Navigate to="/app/conversations" replace />} />
            <Route path="leads/:id" element={<LeadPage of="lead" />} />
            <Route path="leads/import" element={<LeadImportPage />} />
            <Route path="calendar" element={<CalendarPage />} />
            <Route path="inventory" element={<InventoryPage />} />
            <Route path="inventory/import" element={<ImportPage />} />
            <Route path="campaigns" element={<CampaignsPage />} />
            {/* The mailbox is a section of Campaigns now, not a page. Kept as
                a redirect rather than dropped: a rep may have this bookmarked,
                and a dead link lands on the catch-all, which leaves the app. */}
            <Route path="email" element={<Navigate to="/app/campaigns" replace />} />
            <Route path="assistant" element={<AssistantPage />} />
            <Route path="team" element={<TeamPage />} />
          </Route>

          {/* Liner's own dashboard, behind its own role. Not nested under
              /app: that tree is the dealership's and its shell reads
              /api/overview, which an owner has no business being served. */}
          <Route
            path="/ops"
            element={
              <RequireOwner>
                <OpsShell />
              </RequireOwner>
            }
          >
            <Route index element={<OpsCalendarPage />} />
            <Route path="mail" element={<OpsMailPage />} />
            <Route path="phone" element={<OpsPhonePage />} />
          </Route>

          <Route path="*" element={<LeaveToLanding />} />
        </Routes>
        </Suspense>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
