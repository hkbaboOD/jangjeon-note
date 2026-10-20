/* 서비스 워커: 오프라인 캐시 + 웹 푸시 수신 */
const VERSION = "v9";
const SHELL = ["./", "index.html", "styles.css", "chart.js", "divplan.js", "app.js", "config.js", "manifest.webmanifest", "icons/icon-192.png", "icons/icon-180.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open("shell-" + VERSION).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => !k.endsWith(VERSION)).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  // 데이터는 네트워크 우선(최신), 실패 시 마지막 캐시
  if (url.pathname.includes("/data/")) {
    const key = url.origin + url.pathname; // 쿼리스트링 무시
    e.respondWith(
      fetch(e.request).then((r) => {
        const copy = r.clone();
        caches.open("data-" + VERSION).then((c) => c.put(key, copy));
        return r;
      }).catch(() => caches.match(key))
    );
    return;
  }
  // 앱 셸·폰트는 캐시 우선 + 백그라운드 갱신
  e.respondWith(
    caches.match(e.request).then((hit) => {
      const net = fetch(e.request).then((r) => {
        if (r.ok && (url.origin === location.origin || url.host.includes("gstatic") || url.host.includes("googleapis"))) {
          const copy = r.clone();
          caches.open("shell-" + VERSION).then((c) => c.put(e.request, copy));
        }
        return r;
      }).catch(() => hit);
      return hit || net;
    })
  );
});

self.addEventListener("push", (e) => {
  let p = { title: "장전노트", body: "새 알림" };
  try { p = e.data.json(); } catch { if (e.data) p.body = e.data.text(); }
  e.waitUntil(self.registration.showNotification(p.title, {
    body: p.body, icon: "icons/icon-192.png", badge: "icons/icon-192.png", data: { url: p.url || "./" },
  }));
});

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const target = new URL(e.notification.data?.url || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((list) => {
    for (const c of list) { if ("focus" in c) { c.navigate(target); return c.focus(); } }
    return self.clients.openWindow(target);
  }));
});
