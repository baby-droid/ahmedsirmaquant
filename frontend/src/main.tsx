import '@fontsource-variable/geist'
import '@fontsource-variable/geist-mono'
import './index.css'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { RouterProvider } from '@tanstack/react-router'
import { Toaster } from 'sonner'
import { ApiError } from '@/api/http'
import { useLive } from '@/lib/live'
import { C } from '@/lib/theme'
import { telemetry } from '@/lib/ws'
import { router } from '@/router'

/** Any call BRAIN answers with a verification demand raises the global banner. */
const noteVerification = (error: unknown) => {
  if (error instanceof ApiError && error.body.verificationUrl) useLive.setState({ verificationUrl: error.body.verificationUrl })
}

const queryClient = new QueryClient({
  queryCache: new QueryCache({ onError: noteVerification }),
  mutationCache: new MutationCache({ onError: noteVerification }),
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
    },
  },
})

telemetry.connect()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{ style: { background: C.surface3, border: `1px solid ${C.hairlineStrong}`, color: C.ink, borderRadius: 8 } }}
      />
    </QueryClientProvider>
  </StrictMode>,
)
