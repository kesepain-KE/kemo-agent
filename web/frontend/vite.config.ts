import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  return {
    plugins: [react()],
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_DEV_API_TARGET || 'http://127.0.0.1:1357',
          changeOrigin: true,
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
