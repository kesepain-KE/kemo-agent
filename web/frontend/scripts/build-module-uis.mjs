import { build } from 'vite'
import { copyFile, mkdir } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../../..')
// The Bridge host workspace uses unbundled assets with a host-injected SDK.
const bridge = resolve(root, 'expand/global/kemo_bridge/ui')
await mkdir(resolve(bridge, 'dist'), { recursive: true })
for (const entry of ['index.html', 'src/main.js', 'src/style.css']) {
  await copyFile(resolve(bridge, entry), resolve(bridge, 'dist', entry.split('/').at(-1)))
}

for (const relative of ['expand/global/kemo_gateway_status/ui', 'expand/global/kemo_graph/ui', 'template/sense-web/ui']) {
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
