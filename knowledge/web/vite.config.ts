import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: 'dist',
    emptyOutDir: true
  },
  server: {
    open: true,
    host: '0.0.0.0',
    port: 5173,
    strictPort: false
  }
});
