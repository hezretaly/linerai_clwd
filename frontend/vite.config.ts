import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { resolve } from 'node:path'
import { readFile, writeFile } from 'node:fs/promises'
import { transform } from 'esbuild'

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
        const [path, query] = (req.url ?? '/').split('?')
        if (path === '/' || path === '/index.html') req.url = '/landing.html'
        // The website chat's loader under a dealer's prefix, which is the tag
        // the Liner setup card hands out. In production `StorePrefix` strips
        // the prefix and the API serves the file; here `public/` serves it at
        // the root only, and anything else falls through to index.html -- so
        // the card's own tag answered with HTML in development.
        else if (/^\/[^/]+\/embed\.js$/.test(path)) req.url = '/embed.js' + (query ? `?${query}` : '')
        next()
      })
    },
  }
}

/**
 * `public/embed.js` is written to be read and shipped to be small.
 *
 * It is the one file that runs on somebody else's homepage, pasted there once
 * and loaded on every page view of their site -- and its reasoning lives in
 * its comments, which are most of its bytes. Vite copies `public/` untouched,
 * so the build minifies that one copy after the fact: 38 KB as written, a
 * third of that served. ASCII out, because a script with no declared charset
 * is read in the host page's encoding.
 */
function minifyLoader(): Plugin {
  let outDir = 'dist'
  return {
    name: 'minify-loader',
    apply: 'build',
    configResolved(config) {
      outDir = resolve(config.root, config.build.outDir)
    },
    async closeBundle() {
      const file = resolve(outDir, 'embed.js')
      const source = await readFile(file, 'utf8')
      const { code } = await transform(source, {
        minify: true,
        target: 'es2015',
        charset: 'ascii',
        legalComments: 'none',
      })
      await writeFile(file, code)
    },
  }
}

export default defineConfig({
  plugins: [react(), tailwindcss(), landingAtRoot(), minifyLoader()],
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
      // The counted link hops -- an emailed application link and the
      // storefront's Financing links. Anchored, because a bare '/r' prefix
      // would also swallow a store slug that starts with an r.
      '^/r/': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      // A store-prefixed call -- `/alsbou/api/overview`, `/alsbou/ws/dealer`.
      // Matched by regex because the slug is not known here: a literal list
      // would be a third copy of the profile directory, and it would go stale
      // the day somebody adds a dealership. Anything else under a prefix is
      // left to Vite's history fallback, which serves index.html and is what
      // makes `/alsbou/app` work in development at all.
      '^/[^/]+/(api|ws|r)/': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        ws: true,
      },
    },
  },
})
