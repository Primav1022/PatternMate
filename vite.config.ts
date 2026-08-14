import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  base: process.env.VITE_BASE || '/',
  // The sandbox used by automated checks cannot create nested public folders;
  // production builds keep the default public directory behavior.
  publicDir: process.env.NO_PUBLIC_COPY ? false : 'public'
});
