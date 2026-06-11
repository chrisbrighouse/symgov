import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const packageJson = JSON.parse(readFileSync(resolve(import.meta.dirname, 'package.json'), 'utf8'));

export function formatSymgovBuildStamp(date = new Date()) {
  const year = date.getUTCFullYear();
  const month = String(date.getUTCMonth() + 1).padStart(2, '0');
  const day = String(date.getUTCDate()).padStart(2, '0');

  return `${year}-${month}-${day}.01`;
}

export function resolveSymgovBuildStamp(env = process.env) {
  return env.SYMGOV_BUILD_STAMP?.trim() || formatSymgovBuildStamp();
}

const buildStamp = resolveSymgovBuildStamp();

function symgovBuildStampPlugin() {
  return {
    name: 'symgov-build-stamp',
    transformIndexHtml(html) {
      return html.replace(
        /<meta name="symgov-build" content="[^"]*" \/>/,
        `<meta name="symgov-build" content="${buildStamp}" />`
      );
    }
  };
}

export default defineConfig({
  root: 'frontend',
  plugins: [react(), symgovBuildStampPlugin()],
  base: './',
  build: {
    outDir: '../dist',
    emptyOutDir: true
  },
  define: {
    __SYMGOV_VERSION__: JSON.stringify(packageJson.version)
  }
});
