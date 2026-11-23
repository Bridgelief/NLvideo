/* 运行时配置（默认空 = 同源请求，PWA/浏览器直连本机服务时不需要填）。
 *
 * Capacitor / 分体部署时：把本文件内容替换为
 *   window.__LB_API_BASE__ = "https://192.168.x.x:8443";
 * 或在 URL 上加 ?api=https://192.168.x.x:8443（会记住到 localStorage，便于临时调试）。
 *
 * 注意：本文件必须在 app.js 之前加载（index.html 里已按顺序引入）。
 */
window.__LB_API_BASE__ = "";
