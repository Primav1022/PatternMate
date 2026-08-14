import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Shared static assets remain at the repository root after the web app moved
  // into apps/web.
  publicDir: process.env.NO_PUBLIC_COPY ? false : '../../public'
});
