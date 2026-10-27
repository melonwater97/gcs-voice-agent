import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({
  base: './', plugins: [react()],
  server: { host: '127.0.0.1', port: 5180, strictPort: true, watch: { ignored: ['**/release/**', '**/.data/**'] },
    proxy: { '/api': { target: 'http://127.0.0.1:' + (process.env.DESKTOP_PORT || 18301), timeout: 0 } } },
});
