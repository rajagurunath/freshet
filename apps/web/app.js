/* Context Hub landing — progressive enhancement only.
   The page is fully readable and styled with JS disabled. This script:
   1. fires the hero load animation,
   2. toggles the nav background after 80px scroll,
   3. duplicates the trust marquee for a seamless loop,
   4. reveals sections + draws the protocol flow on scroll (IntersectionObserver),
   5. switches the product-mock states via the toggle tabs.
   All motion is suppressed when prefers-reduced-motion is set. */

(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  /* ---- 1. Hero load animation ---- */
  document.documentElement.classList.add('loaded');

  /* ---- 2. Nav background on scroll ---- */
  var nav = document.getElementById('nav');
  function onScroll() {
    if (window.scrollY > 80) nav.classList.add('scrolled');
    else nav.classList.remove('scrolled');
  }
  window.addEventListener('scroll', onScroll, { passive: true });
  onScroll();

  /* ---- 3. Seamless marquee (duplicate the track contents) ---- */
  var track = document.getElementById('marquee-track');
  if (track) {
    var items = track.innerHTML;
    track.innerHTML = items + items; // 2x content so translateX(-50%) loops cleanly
    track.setAttribute('aria-hidden', 'false');
  }

  /* ---- 4. Scroll reveal + flow animation ---- */
  var revealTargets = document.querySelectorAll('.sr, .sr-stagger');
  var flow = document.getElementById('flow');

  if (reduceMotion || !('IntersectionObserver' in window)) {
    // Show everything in its final state.
    revealTargets.forEach(function (el) { el.classList.add('in-view'); });
    if (flow) flow.classList.add('in-view');
  } else {
    // Stagger children of .sr-stagger via inline transition-delay.
    document.querySelectorAll('.sr-stagger').forEach(function (group) {
      var step = group.classList.contains('steps') ? 120
               : group.classList.contains('features') ? 60
               : 80;
      Array.prototype.forEach.call(group.children, function (child, i) {
        child.style.transitionDelay = (i * step) + 'ms';
      });
    });

    var io = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) {
          entry.target.classList.add('in-view');
          obs.unobserve(entry.target);
        }
      });
    }, { threshold: 0.12, rootMargin: '0px 0px -6% 0px' });

    revealTargets.forEach(function (el) { io.observe(el); });

    // Safety net: anything already at/above the viewport top on load should
    // never sit hidden (deep links, restored scroll, no-paint environments).
    function revealVisible() {
      var vh = window.innerHeight || document.documentElement.clientHeight;
      revealTargets.forEach(function (el) {
        if (el.classList.contains('in-view')) return;
        var r = el.getBoundingClientRect();
        if (r.top < vh * 0.92) el.classList.add('in-view');
      });
    }
    revealVisible();
    window.addEventListener('scroll', revealVisible, { passive: true });
    window.addEventListener('resize', revealVisible, { passive: true });
    // Final guarantee: content is never permanently hidden. If the observer
    // somehow misses an element (odd scroll restoration, headless capture),
    // reveal everything after a grace period.
    window.setTimeout(function () {
      revealTargets.forEach(function (el) { el.classList.add('in-view'); });
    }, 2600);

    /* Protocol flow: draw arrows with a staggered dashoffset, then pulse nodes. */
    if (flow) {
      var flowIO = new IntersectionObserver(function (entries, obs) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          obs.unobserve(entry.target);
          var arrows = entry.target.querySelectorAll('.flow-arrow');
          // Set each arrow's dash length to its own path length so the draw is accurate.
          arrows.forEach(function (a) {
            var len = a.getTotalLength ? a.getTotalLength() : 100;
            a.style.strokeDasharray = len;
            a.style.strokeDashoffset = len;
          });
          // Force reflow so the initial offset is committed before we animate.
          // eslint-disable-next-line no-unused-expressions
          entry.target.getBoundingClientRect();
          arrows.forEach(function (a, i) {
            setTimeout(function () { a.style.strokeDashoffset = '0'; }, i * 200);
          });
          // Reveal arrowheads + run the single node pulse after the last arrow draws.
          setTimeout(function () {
            entry.target.classList.add('in-view');
          }, arrows.length * 200 + 200);
        });
      }, { threshold: 0.3 });
      flowIO.observe(flow);
    }
  }

  /* ---- 5. Product-mock state tabs ---- */
  var tabs = document.querySelectorAll('.mock-tab');
  var states = document.querySelectorAll('.mock-state');
  tabs.forEach(function (tab) {
    tab.addEventListener('click', function () {
      var target = tab.getAttribute('data-state');
      tabs.forEach(function (t) {
        var on = t === tab;
        t.classList.toggle('active', on);
        t.setAttribute('aria-selected', on ? 'true' : 'false');
      });
      states.forEach(function (s) {
        s.classList.toggle('active', s.id === target);
      });
    });
  });
})();
