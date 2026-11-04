/* Service worker - Gestion des etudiants
 *
 * Strategie volontairement conservatrice :
 *   - App shell (page, manifeste, icones) : reseau d'abord, cache en secours
 *     pour que l'application s'ouvre hors ligne.
 *   - Tout le reste (API /students, /pay, exports) : reseau uniquement.
 *     On ne sert JAMAIS de donnees metier perimees depuis le cache.
 */
const CACHE = "students-shell-v4";
const SHELL = [
  "/",
  "/manifest.json",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) => cache.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Donnees metier : jamais depuis le cache.
  const isData =
    url.pathname.startsWith("/students") ||
    url.pathname.startsWith("/pay") ||
    url.pathname.startsWith("/generate_report") ||
    url.pathname.startsWith("/health");
  if (isData) return;

  const isShell = request.mode === "navigate" || SHELL.includes(url.pathname);
  if (!isShell) return;

  event.respondWith(
    fetch(request)
      .then((response) => {
        const copy = response.clone();
        caches.open(CACHE).then((cache) => cache.put(request, copy)).catch(() => {});
        return response;
      })
      .catch(() =>
        caches.match(request).then((cached) => cached || caches.match("/"))
      )
  );
});
