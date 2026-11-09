import { build } from 'vite'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
for (const relative of ['expand/global/kemo_app/ui', 'expand/global/kemo_gateway_status/ui', 'expand/global/kemo_graph/ui', 'template/sense-web/ui']) {
  await build({
    configFile: false, root: resolve(root, relative), base: './',
    build: {
      target: 'es2022', emptyOutDir: true,
      rollupOptions: {
        external: ['/__kemo_bridge__'],
        output: { paths: { '/__kemo_bridge__': '../__kemo__/bridge.js' } },
      },
    },
  })
}
