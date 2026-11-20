/* Intentionally does not implement background tracking, caching, or auto-resume. */
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', event => event.waitUntil(self.clients.claim()));
