import { StrictMode, useEffect } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Route, Routes } from 'react-router-dom'

import './styles/liner-theme.css'

import { AppShell } from './components/dashboard/AppShell'
import { BuyerTheme } from './components/BuyerTheme'
import { RequireAuth } from './routes/RequireAuth'
import { Login } from './routes/Login'
import { Chat } from './routes/Chat'
import { Call } from './routes/Call'
import { OverviewPage } from './routes/Overview'
import { ConversationsPage } from './routes/Conversations'
import { LeadsPage } from './routes/Leads'
import { CalendarPage } from './routes/Calendar'
import { InventoryPage } from './routes/Inventory'
import { ImportPage } from './routes/Import'
import { AssistantPage } from './routes/Assistant'
import { TeamPage } from './routes/Team'

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
            <Route path="conversations" element={<ConversationsPage />} />
            <Route path="conversations/:id" element={<ConversationsPage />} />
            <Route path="leads" element={<LeadsPage />} />
            <Route path="calendar" element={<CalendarPage />} />
            <Route path="inventory" element={<InventoryPage />} />
            <Route path="inventory/import" element={<ImportPage />} />
            <Route path="assistant" element={<AssistantPage />} />
            <Route path="team" element={<TeamPage />} />
          </Route>

          <Route path="*" element={<LeaveToLanding />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
