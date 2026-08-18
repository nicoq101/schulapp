const CACHE_NAME = "schulapp-v1";
const CORE_ASSETS = [
  "/",
  "/static/css/style.css",
  "/static/js/app.js",
  "/static/manifest.json",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(CORE_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Netzwerk zuerst, Cache als Fallback (damit Daten möglichst aktuell sind)
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  event.respondWith(
    fetch(event.request)
      .then((response) => {
        const clone = response.clone();
        caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
        return response;
      })
      .catch(() => caches.match(event.request))
  );
});

// Eingehende Push-Nachricht anzeigen
self.addEventListener("push", (event) => {
  let data = { title: "Schulapp", body: "Neue Benachrichtigung", tag: "allgemein" };
  try {
    data = event.data.json();
  } catch (e) {
    /* ignore */
  }

  event.waitUntil(
    self.registration.showNotification(data.title, {
      body: data.body,
      tag: data.tag,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png",
      vibrate: [80, 40, 80],
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: "window" }).then((clients) => {
      if (clients.length > 0) return clients[0].focus();
      return self.clients.openWindow("/");
    })
  );
});
