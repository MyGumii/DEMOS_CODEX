import { defineConfig } from 'vite';

export default defineConfig({
  base: './',
  server: { proxy: { '/atlas-api': 'http://127.0.0.1:4174' } },
  preview: { proxy: { '/atlas-api': 'http://127.0.0.1:4174' } },
  build: { target: 'es2022', chunkSizeWarningLimit: 700 },
});
