/* AI 现场拍摄指导助手 · Service Worker（离线壳 + demo 兜底）
 *
 * 目标：
 * 1) 手机"添加到主屏幕"后，断网/服务没起时也能打开界面（App Shell），而不是浏览器报错页。
 * 2) 断网时仍可看内置的 5 个 demo 场景（帧 + 场景清单缓存），至少能演示取景/建议界面。
 * 3) 接口永远走网络优先：/api/* 不缓存（除 /api/demo 有离线兜底），避免用户看到过期建议。
 *
 * 版本策略：改前端静态文件时把 VERSION 加一，activate 时清掉旧缓存。
 */

const VERSION = 'v3';
const SHELL_CACHE = `lb-shell-${VERSION}`;
const DATA_CACHE = `lb-data-${VERSION}`;

/* App Shell：这些是"打开就能看见界面"的最小集合 */
const SHELL_ASSETS = [
  '/',
  '/index.html',
  '/app.js',
  '/app-config.js',
  '/styles.css',
  '/manifest.json',
  '/icon.svg',
  '/icon-192.png',
  '/icon-512.png',
  '/icon-180.png'
];

/* demo 场景帧（竖屏 720×1280）：离线也能演示 */
const DEMO_FRAMES = [
  '/static/demo/backlit_portrait.jpg',
  '/static/demo/cluttered_portrait.jpg',
  '/static/demo/product_still.jpg',
  '/static/demo/landscape_mood.jpg',
  '/static/demo/closeup_portrait.jpg'
];

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const shell = await caches.open(SHELL_CACHE);
    // 单个失败不要拖垮整个安装（例如某张图标还没生成）
    await Promise.all(SHELL_ASSETS.map(async (url) => {
      try { await shell.add(new Request(url, { cache: 'reload' })); } catch (err) { /* ignore */ }
    }));
    const data = await caches.open(DATA_CACHE);
    await Promise.all(DEMO_FRAMES.map(async (url) => {
      try { await data.add(new Request(url, { cache: 'reload' })); } catch (err) { /* ignore */ }
    }));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys
      .filter((key) => key.startsWith('lb-') && key !== SHELL_CACHE && key !== DATA_CACHE)
      .map((key) => caches.delete(key)));
    await self.clients.claim();
  })());
});

/** 网络优先：拿到就更新缓存；失败则回缓存（没有缓存才报错）。 */
async function networkFirst(request, cacheName, cacheKey) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response && response.ok) {
      try { await cache.put(cacheKey || request, response.clone()); } catch (err) { /* ignore */ }
    }
    return response;
  } catch (err) {
    const cached = await cache.match(cacheKey || request);
    if (cached) return cached;
    throw err;
  }
}

/** 缓存优先：静态资源命中直接返回，后台顺手用网络响应刷新缓存。 */
async function cacheFirst(request, cacheName) {
  const cache = await caches.open(cacheName);
  const cached = await cache.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response && response.ok) {
    try { await cache.put(request, response.clone()); } catch (err) { /* ignore */ }
  }
  return response;
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  let url;
  try { url = new URL(request.url); } catch (err) { return; }
  if (url.origin !== self.location.origin) return;   // 跨源（Capacitor 调 API）交给网络层，不拦

  // 1) 页面导航：网络优先，断网回退到缓存的 App Shell
  if (request.mode === 'navigate') {
    event.respondWith(networkFirst(request, SHELL_CACHE, '/index.html').catch(async () => {
      const cache = await caches.open(SHELL_CACHE);
      return (await cache.match('/')) || (await cache.match('/index.html')) || Response.error();
    }));
    return;
  }

  // 2) 演示场景清单：网络优先 + 离线兜底（没有服务时还能选场景）
  if (url.pathname === '/api/demo') {
    event.respondWith(networkFirst(request, DATA_CACHE, '/api/demo'));
    return;
  }

  // 3) 其它接口：只走网络（建议/记录/粗剪必须是新鲜的），失败由前端 toast
  if (url.pathname.startsWith('/api/')) return;

  // 4) 静态资源：缓存优先
  if (url.pathname.startsWith('/static/')) {
    event.respondWith(cacheFirst(request, DATA_CACHE).catch(() => Response.error()));
    return;
  }

  // 5) 其它同源静态文件（js/css/图标/manifest）：缓存优先 + 网络兜底
  event.respondWith(cacheFirst(request, SHELL_CACHE).catch(() => Response.error()));
});
