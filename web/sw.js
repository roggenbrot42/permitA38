/* permitA38 – service worker.
 *
 *  - App shell (index.html, scripts, styles, icons): cache-first, cache name derived from the app version.
 *  - /api/*: NEVER cached and not even intercepted – the browser talks to the network directly.
 *  - Updates: a new worker waits; the page shows "Update available" and sends SKIP_WAITING when the user agrees.
 *  - Background Sync ('permita38-sync'): replays the offline time-tracking queue (see pwa-queue.js).
 *
 * The server replaces __APP_VERSION__ when it serves /sw.js, so every release changes this file's bytes. */
'use strict';
importScripts('pwa-queue.js');

const VERSION = '__APP_VERSION__';
const VERSIONED = VERSION.indexOf('__') !== 0;            // false when served by a plain static host
const SHELL_CACHE = 'permita38-shell-' + (VERSIONED ? VERSION : 'dev');
const EXTRA = ['offline.html', 'manifest.webmanifest', 'pwa-queue.js', 'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png'];

/* The precache list is generated from index.html itself, so new scripts/styles are picked up automatically. */
async function shellUrls() {
  const res = await fetch('index.html', { cache: 'reload' });
  if (!res.ok) throw new Error('index.html ' + res.status);
  const html = await res.clone().text();
  const urls = new Set(['./', 'index.html']);
  for (const m of html.matchAll(/(?:src|href)="([^"#?]+)"/g)) {
    const u = m[1];
    if (/^(?:[a-z]+:|\/\/|data:)/i.test(u)) continue;     // external / data: URLs
    if (u.startsWith('/api/')) continue;
    urls.add(u.replace(/^\//, ''));
  }
  EXTRA.forEach(u => urls.add(u));
  return { urls: [...urls], index: res };
}

self.addEventListener('install', event => {
  event.waitUntil((async () => {
    const { urls, index } = await shellUrls();
    const cache = await caches.open(SHELL_CACHE);
    await cache.put('index.html', index.clone());
    await Promise.all(urls.filter(u => u !== 'index.html' && u !== './').map(async u => {
      const r = await fetch(u, { cache: 'reload' });
      if (!r.ok) throw new Error(u + ' ' + r.status);
      await cache.put(u, r);
    }));
    // no skipWaiting(): the page decides when to switch (update toast)
  })());
});

self.addEventListener('activate', event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) if (key.startsWith('permita38-shell-') && key !== SHELL_CACHE) await caches.delete(key);
    await self.clients.claim();
  })());
});

self.addEventListener('message', event => {
  if (event.data && event.data.type === 'SKIP_WAITING') self.skipWaiting();
  if (event.data && event.data.type === 'VERSION' && event.ports[0]) event.ports[0].postMessage({ version: VERSION, cache: SHELL_CACHE });
});

self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return;                        // other origins (separately hosted API): untouched
  if (url.pathname.startsWith('/api/') || url.pathname === '/sw.js') return;   // network only, never cached
  event.respondWith(shell(req, url));
});

async function shell(req, url) {
  const cache = await caches.open(SHELL_CACHE);
  const navigation = req.mode === 'navigate';
  const key = navigation ? 'index.html' : url.pathname.replace(/^\//, '');
  const hit = await cache.match(key, { ignoreSearch: true });
  if (hit) {
    // not versioned (plain static host): serve from cache but refresh in the background so deployments reach users
    if (!VERSIONED) fetch(req).then(r => r.ok && cache.put(key, r)).catch(() => {});
    return hit;
  }
  try {
    const res = await fetch(req);
    if (res.ok && !navigation) cache.put(key, res.clone());
    return res;
  } catch {
    if (navigation) return (await cache.match('offline.html')) || new Response('Offline', { status: 503 });
    return new Response('', { status: 504, statusText: 'Offline' });
  }
}

/* Background Sync: replay queued time tracking even when no tab is open (Chromium). The token comes from IndexedDB. */
self.addEventListener('sync', event => {
  if (event.tag !== 'permita38-sync') return;
  event.waitUntil((async () => {
    const out = await self.P38Queue.replayLocked({ base: '/api/' });
    const clients = await self.clients.matchAll({ includeUncontrolled: true });
    clients.forEach(c => c.postMessage({ type: 'SYNCED', result: out }));
    if (out.stopped === 'offline' || out.stopped === 'server') throw new Error('retry');   // lets the browser retry later
  })());
});
