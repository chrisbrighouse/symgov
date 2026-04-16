import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const packageJson = JSON.parse(readFileSync(resolve(import.meta.dirname, 'package.json'), 'utf8'));

export default defineConfig({
  root: 'frontend',
  plugins: [react()],
  base: './',
  build: {
    outDir: '../dist',
    emptyOutDir: true
  },
  define: {
    __SYMGOV_VERSION__: JSON.stringify(packageJson.version)
  }
});
