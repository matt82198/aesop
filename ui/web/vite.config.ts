import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Read API port from environment variable (dev-only; production uses built dist)
const apiPort = process.env.VITE_API_PORT || '8770';
const apiTarget = `http://localhost:${apiPort}`;

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/data': apiTarget,
      '/api': apiTarget,
      '/agent': apiTarget,
      '/events': apiTarget,
      '/submit': apiTarget,
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: undefined,
      },
    },
  },
});
