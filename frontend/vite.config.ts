import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'node:path'

/**
 * `/` serves landing.html; everything else serves the SPA.
 *
 * The landing page is a standalone document, not a React route: its JS is
 * written for a page load (infinite animation loops, an interval and scroll
 * listeners, none with teardown), so it has to unload the way it expects. It
 * never loads main.tsx and never imports liner-theme.css, so React and the
 * shadcn tokens cannot reach it and Tailwind emits nothing for its class names.
 */
function landingAtRoot(): Plugin {
  return {
    name: 'landing-at-root',
    configureServer(server) {
      // configureServer middleware runs before Vite's internals, so this
      // rewrite lands before the SPA html fallback sees the request. Matching
      // only the exact root leaves /chat, /call, /login and /app/* to fall
      // through to index.html as usual.
      server.middlewares.use((req, _res, next) => {
        const path = (req.url ?? '/').split('?')[0]
        if (path === '/' || path === '/index.html') req.url = '/landing.html'
        next()
      })
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), landingAtRoot()],
  build: {
    rollupOptions: {
      input: {
        landing: resolve(__dirname, 'landing.html'),
        app: resolve(__dirname, 'index.html'),
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
})
