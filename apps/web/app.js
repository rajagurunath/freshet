/* Context Hub landing — progressive enhancement only.
   The page is fully readable with JS disabled; this file just:
   1. enables the scroll-reveal styles (.js gate),
   2. pauses the 12s protocol animation while it is off-screen,
   3. keeps the footer year honest.
   No libraries, no animation code — all motion lives in CSS. */

(function () {
  "use strict";

  document.documentElement.classList.add("js");

  var reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  // Scroll reveal for the feature sections.
  var revealEls = Array.prototype.slice.call(document.querySelectorAll(".reveal"));
  if (reduced || typeof IntersectionObserver === "undefined") {
    revealEls.forEach(function (el) { el.classList.add("in"); });
  } else {
    var revealObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            entry.target.classList.add("in");
            revealObserver.unobserve(entry.target);
          }
        });
      },
      { rootMargin: "0px 0px -10% 0px", threshold: 0.05 }
    );
    revealEls.forEach(function (el) { revealObserver.observe(el); });
  }

  // Pause the protocol loop while it is out of view (saves CPU, keeps the
  // loop starting near a clean state when the visitor scrolls back).
  var proto = document.getElementById("proto");
  if (proto && !reduced && typeof IntersectionObserver !== "undefined") {
    var protoObserver = new IntersectionObserver(
      function (entries) {
        entries.forEach(function (entry) {
          proto.classList.toggle("paused", !entry.isIntersecting);
        });
      },
      { threshold: 0.1 }
    );
    protoObserver.observe(proto);
  }

  var year = document.getElementById("year");
  if (year) year.textContent = String(new Date().getFullYear());
})();
