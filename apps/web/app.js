/* Freshet landing — progressive enhancement only.
   The page is fully readable and styled with JS disabled. This script:
   1. fires the hero load animation,
   2. toggles the nav background after 80px scroll,
   3. draws the ambient knowledge-graph canvas behind the hero,
   4. reveals sections on scroll (IntersectionObserver),
   5. lights the AICP protocol ladder + runs the level-by-level packet,
   6. adds a lightweight tilt to feature cards,
   7. switches the product-mock states via the toggle tabs.
   All motion is suppressed when prefers-reduced-motion is set. */

(function () {
  'use strict';

  var reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  var isTouch = window.matchMedia('(hover: none)').matches;

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

  /* ---- 3. Hero knowledge-graph canvas ----
     Drifting nodes + edges between near neighbours, with the brand
     accent on a few "hub" nodes and a context pulse that travels an
     edge now and then. Faint enough that hero text stays crisp. */
  (function heroGraph() {
    var canvas = document.getElementById('hero-graph');
    if (!canvas) return;
    var ctx = canvas.getContext('2d');
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    var W = 0, H = 0;
    var nodes = [];
    var pulses = [];
    var LINK_DIST = 150;

    function resize() {
      W = canvas.offsetWidth;
      H = canvas.offsetHeight;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      seed();
      if (reduceMotion) draw(); // single static frame
    }

    function seed() {
      var count = Math.max(14, Math.min(34, Math.round((W * H) / 26000)));
      nodes = [];
      for (var i = 0; i < count; i++) {
        nodes.push({
          x: Math.random() * W,
          y: Math.random() * H,
          vx: (Math.random() - 0.5) * 0.18,
          vy: (Math.random() - 0.5) * 0.18,
          r: Math.random() * 1.6 + 1.3,
          hub: Math.random() < 0.16
        });
      }
    }

    function edges(cb) {
      for (var i = 0; i < nodes.length; i++) {
        for (var j = i + 1; j < nodes.length; j++) {
          var a = nodes[i], b = nodes[j];
          var dx = a.x - b.x, dy = a.y - b.y;
          var d = Math.sqrt(dx * dx + dy * dy);
          if (d < LINK_DIST) cb(a, b, d, i, j);
        }
      }
    }

    function draw() {
      ctx.clearRect(0, 0, W, H);
      // edges
      edges(function (a, b, d) {
        var alpha = (1 - d / LINK_DIST) * 0.5;
        ctx.strokeStyle = 'rgba(180,170,156,' + alpha + ')';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      });
      // nodes
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        if (n.hub) {
          ctx.fillStyle = 'rgba(242,84,27,0.85)';
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 1.1, 0, Math.PI * 2);
          ctx.fill();
          ctx.strokeStyle = 'rgba(242,84,27,0.28)';
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2);
          ctx.stroke();
        } else {
          ctx.fillStyle = 'rgba(150,140,126,0.6)';
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
          ctx.fill();
        }
      }
      // travelling context pulses
      for (var p = pulses.length - 1; p >= 0; p--) {
        var pl = pulses[p];
        pl.t += pl.speed;
        if (pl.t >= 1) { pulses.splice(p, 1); continue; }
        var x = pl.a.x + (pl.b.x - pl.a.x) * pl.t;
        var y = pl.a.y + (pl.b.y - pl.a.y) * pl.t;
        ctx.fillStyle = 'rgba(255,138,61,' + (0.9 * (1 - Math.abs(0.5 - pl.t) * 2) + 0.1) + ')';
        ctx.beginPath();
        ctx.arc(x, y, 2.4, 0, Math.PI * 2);
        ctx.fill();
      }
    }

    function step() {
      for (var i = 0; i < nodes.length; i++) {
        var n = nodes[i];
        n.x += n.vx; n.y += n.vy;
        if (n.x < -20) n.x = W + 20; else if (n.x > W + 20) n.x = -20;
        if (n.y < -20) n.y = H + 20; else if (n.y > H + 20) n.y = -20;
      }
      // occasionally spawn a pulse along a real edge
      if (pulses.length < 4 && Math.random() < 0.04) {
        var picks = [];
        edges(function (a, b) { picks.push([a, b]); });
        if (picks.length) {
          var e = picks[(Math.random() * picks.length) | 0];
          pulses.push({ a: e[0], b: e[1], t: 0, speed: 0.006 + Math.random() * 0.006 });
        }
      }
      draw();
      raf = requestAnimationFrame(step);
    }

    var raf = null;
    window.addEventListener('resize', resize, { passive: true });
    resize();
    if (!reduceMotion) {
      // pause the loop when the hero is fully scrolled past (save battery)
      var heroEl = canvas.parentElement;
      var heroVisible = true;
      if ('IntersectionObserver' in window) {
        new IntersectionObserver(function (entries) {
          heroVisible = entries[0].isIntersecting;
          if (heroVisible && raf === null) { raf = requestAnimationFrame(step); }
          else if (!heroVisible && raf !== null) { cancelAnimationFrame(raf); raf = null; }
        }, { threshold: 0 }).observe(heroEl);
      }
      raf = requestAnimationFrame(step);
    }
  })();

  /* ---- 4. Scroll reveal ---- */
  var revealTargets = document.querySelectorAll('.sr, .sr-stagger');

  if (reduceMotion || !('IntersectionObserver' in window)) {
    revealTargets.forEach(function (el) { el.classList.add('in-view'); });
  } else {
    document.querySelectorAll('.sr-stagger').forEach(function (group) {
      var step = group.classList.contains('steps') ? 120
               : group.classList.contains('features') ? 60
               : group.classList.contains('proto-desc') ? 70
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

    // Safety net for deep links / restored scroll.
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
    window.setTimeout(function () {
      revealTargets.forEach(function (el) { el.classList.add('in-view'); });
    }, 2600);
  }

  /* ---- 5. AICP protocol ladder ---- */
  (function protocolLadder() {
    var ladder = document.getElementById('proto-ladder');
    if (!ladder) return;
    var levels = ladder.querySelectorAll('.proto-level');

    if (reduceMotion || !('IntersectionObserver' in window)) {
      ladder.classList.add('lit');
      return;
    }

    // Stagger the reveal of each tier.
    levels.forEach(function (lv, i) { lv.style.transitionDelay = (i * 90) + 'ms'; });

    var seq = null;
    var lio = new IntersectionObserver(function (entries, obs) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        obs.unobserve(entry.target);
        ladder.classList.add('lit');
        // After the tiers settle, run a repeating "packet" down the levels.
        window.setTimeout(function () { runPacket(); }, levels.length * 90 + 300);
      });
    }, { threshold: 0.4 });
    lio.observe(ladder);

    function runPacket() {
      var i = 0;
      function tick() {
        levels.forEach(function (lv) { lv.classList.remove('pulse'); });
        if (i < levels.length) {
          levels[i].classList.add('pulse');
          i++;
          seq = window.setTimeout(tick, 520);
        } else {
          // pause, then loop again
          i = 0;
          seq = window.setTimeout(tick, 2600);
        }
      }
      tick();
    }
  })();

  /* ---- 6. Lightweight tilt on feature cards ---- */
  (function tilt() {
    if (reduceMotion || isTouch) return;
    var cards = document.querySelectorAll('[data-tilt]');
    var MAX = 6; // degrees
    cards.forEach(function (card) {
      card.addEventListener('mousemove', function (e) {
        var rect = card.getBoundingClientRect();
        var px = (e.clientX - rect.left) / rect.width - 0.5;
        var py = (e.clientY - rect.top) / rect.height - 0.5;
        card.style.transform =
          'perspective(700px) rotateX(' + (-py * MAX).toFixed(2) + 'deg) rotateY(' +
          (px * MAX).toFixed(2) + 'deg) translateY(-2px)';
      });
      card.addEventListener('mouseleave', function () {
        card.style.transform = '';
      });
    });
  })();

  /* ---- 7. Product-mock state tabs ---- */
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
