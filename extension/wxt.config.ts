import { defineConfig } from 'wxt';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  modules: ['@wxt-dev/module-react'],
  manifest: {
    name: 'AI Browser Assistant',
    description: 'AI-powered browser assistant with chat and automation',
    permissions: [
      'storage',
      'identity',
      'sidePanel',
      'tabs',
      'activeTab',
      'scripting',
    ],
    host_permissions: [
      'http://localhost:*/*',
      'https://*/*',
      'http://*/*',
    ],
    action: {
      default_title: 'AI Browser Assistant',
    },
    side_panel: {
      default_path: 'sidepanel.html',
    },
  },
  vite: () => ({
    plugins: [tailwindcss()],
  }),
});
