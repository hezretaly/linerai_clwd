import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'

import './styles/liner-theme.css'

import { AppShell } from './components/dashboard/AppShell'
import { RequireAuth } from './routes/RequireAuth'
import { Landing } from './routes/Landing'
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

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<Landing />} />
          <Route path="/chat" element={<Chat />} />
          <Route path="/call" element={<Call />} />
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

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
