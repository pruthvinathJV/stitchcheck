/* StitchCheck service worker. Bump VERSION and the ?v= links in index.html
   together so updates never mix versions. */
var VERSION = "stitchcheck-v9";
var SHELL = [
  "./",
  "./index.html",
  "./index.html?v=9",
  "./app.js?v=9",
  "./strings.js?v=9",
  "./config.js?v=9",
  "./styles.css?v=9",
  "./manifest.json?v=9",
  "./icons/192.png?v=9",
  "./icons/512.png?v=9"
];

var SHELL_NAMES = ["/", "/index.html", "/app.js", "/strings.js",
  "/config.js", "/styles.css", "/manifest.json",
  "/icons/192.png", "/icons/512.png"];

function isShell(url) {
  var p = new URL(url).pathname;
  for (var i = 0; i < SHELL_NAMES.length; i++) {
    if (p === SHELL_NAMES[i] || p.endsWith(SHELL_NAMES[i])) { return true; }
  }
  return false;
}

self.addEventListener("install", function (e) {
  e.waitUntil(
    caches.open(VERSION).then(function (c) { return c.addAll(SHELL); })
      .then(function () { return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function (e) {
  e.waitUntil(
    caches.keys().then(function (keys) {
      return Promise.all(keys.map(function (k) {
        if (k !== VERSION) { return caches.delete(k); }
        return null;
      }));
    }).then(function () { return self.clients.claim(); })
  );
});

self.addEventListener("message", function (e) {
  if (e.data === "SKIP_WAITING") { self.skipWaiting(); }
});

// Never cache or intercept API calls or /media/: network only.
self.addEventListener("fetch", function (e) {
  var url = new URL(e.request.url);
  if (e.request.method !== "GET") { return; }
  if (url.pathname.indexOf("/media/") === 0) { return; }
  if (!isShell(e.request.url) && e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(function () { return caches.match("./index.html"); }));
    return;
  }
  if (!isShell(e.request.url)) { return; }
  e.respondWith(
    fetch(e.request).then(function (res) {
      var copy = res.clone();
      caches.open(VERSION).then(function (c) { c.put(e.request, copy); });
      return res;
    }).catch(function () { return caches.match(e.request); })
  );
});
