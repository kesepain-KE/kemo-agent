import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { existsSync, readdirSync, readFileSync } from 'node:fs'

const pdfRoot = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json'))
const pdfFolders = ['cmaps', 'standard_fonts', 'wasm']
const pdfAssets = pdfFolders.flatMap(folder => readdirSync(join(pdfRoot, folder))
  .filter(name => /\.(bcmap|ttf|pfb|wasm)$/.test(name)).map(name => ({ folder, name })))

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react(), {
      name: 'local-pdf-preview-assets',
      generateBundle() {
        for (const { folder, name } of pdfAssets) {
          this.emitFile({ type: 'asset', fileName: `pdfjs/${folder}/${name}`,
            source: readFileSync(join(pdfRoot, folder, name)) })
        }
      },
      configureServer(server) {
        server.middlewares.use((request, response, next) => {
          const match = request.url?.split('?')[0].match(/^\/pdfjs\/(cmaps|standard_fonts|wasm)\/([^/]+)$/)
          if (!match || !pdfAssets.some(asset => asset.folder === match[1] && asset.name === match[2])) return next()
          const file = join(pdfRoot, match[1], match[2])
          if (!existsSync(file)) return next()
          response.setHeader('Content-Type', file.endsWith('.wasm') ? 'application/wasm' : 'application/octet-stream')
          response.end(readFileSync(file))
        })
      },
    }],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_DEV_API_TARGET || 'http://127.0.0.1:1357',
          changeOrigin: true,
        },
      },
    },
    build: {
      rollupOptions: {
        output: {
          manualChunks(id) {
            if (!id.includes('node_modules')) return
            if (id.includes('pdfjs-dist')) return 'vendor-pdf'
            if (/mermaid|cytoscape|dagre|elkjs/.test(id)) return 'vendor-diagram'
            if (/react-markdown|remark-|rehype-|micromark|mdast|hast|katex/.test(id)) return 'vendor-markdown'
          },
        },
      },
    },
    test: {
      environment: 'jsdom',
      setupFiles: './src/test/setup.ts',
      css: true,
      // GitHub hosted runners occasionally need more than Vitest's 5 second
      // default for the larger integration-style page tests. Keep a finite
      // ceiling while avoiding false failures caused by temporary CPU load.
      testTimeout: 15_000,
      hookTimeout: 10_000,
      // Multiple jsdom workers are memory and CPU intensive. Two workers keep
      // CI deterministic without serialising the complete frontend suite.
      maxWorkers: process.env.CI ? 2 : undefined,
    },
  }
})
