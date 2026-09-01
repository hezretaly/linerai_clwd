import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import './styles/liner-theme.css'

import { AppShell } from './components/dashboard/AppShell'
import { BuyerTheme } from './components/BuyerTheme'
import { RequireAuth } from './routes/RequireAuth'
import { Login } from './routes/Login'
import { Chat } from './routes/Chat'
import { Showroom } from './routes/Showroom'
import { Call } from './routes/Call'
import { OverviewPage } from './routes/Overview'
import { ConversationListPage } from './routes/ConversationList'
import { LeadPage, LeadRedirect } from './routes/LeadPage'
import { CampaignsPage } from './routes/Campaigns'
import { LeadImportPage } from './routes/LeadImport'
import { CalendarPage } from './routes/Calendar'
import { InventoryPage } from './routes/Inventory'
import { ImportPage } from './routes/Import'
import { AssistantPage } from './routes/Assistant'
import { TeamPage } from './routes/Team'
import { OpsShell } from './routes/ops/OpsShell'
import { RequireOwner } from './routes/ops/RequireOwner'
import { OpsCalendarPage } from './routes/ops/OpsCalendar'
import { OpsMailPage } from './routes/ops/OpsMail'

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
      <BrowserRouter>
        <Routes>
          {/* Buyer surfaces keep the brand blue. /login is a dealer screen and
              deliberately stays on classic. */}
          {/* The dealership's own front page, for a demo: their brand, their
              real lot, and the chat widget where it would really sit. It
              scopes .theme-buyer itself, since the whole page is a buyer
              surface rather than a route wrapped in one. */}
          <Route path="/showroom" element={<Showroom />} />
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
          </Route>

          <Route path="*" element={<LeaveToLanding />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
