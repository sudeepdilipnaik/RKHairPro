/* RK Hair Pro – public website behaviour: header, reveal, service tabs, sliders, video cards */
(function () {
  document.documentElement.classList.add("js");
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };

  // header turns solid on scroll
  var head = $("#pubhead");
  function onScroll() { if (head) head.classList.toggle("scrolled", window.scrollY > 24); }
  window.addEventListener("scroll", onScroll, { passive: true }); onScroll();

  // scroll-reveal
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (es) { es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("in"); io.unobserve(e.target); } }); }, { threshold: 0.12 });
    $$(".reveal").forEach(function (el) { io.observe(el); });
  } else { $$(".reveal").forEach(function (el) { el.classList.add("in"); }); }

  // toast
  var toastEl = $("#toastMsg"), toastT;
  function toast(msg) { if (!toastEl) return; toastEl.textContent = msg; toastEl.classList.add("show"); clearTimeout(toastT); toastT = setTimeout(function () { toastEl.classList.remove("show"); }, 3200); }

  // hero logo animation: autoplay muted; falls back to the static logo on data-saver / slow networks / reduced motion / blocked autoplay
  var hb = $("#heroBrand"), hv = $("#heroAnim"), snd = $("#animSound");
  if (hb && hv) {
    var srcStore = hv.getAttribute("src"), label = snd && $("span", snd), soundOpt = !!snd && snd.dataset.sound === "1";
    var conn = navigator.connection || {};
    var reduced = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
    var skip = conn.saveData || /(^|-)2g$/.test(conn.effectiveType || "") || reduced;
    // one small button under the logo: "Play logo animation" (autoplay blocked / skipped), "Play with sound", "Sound on – tap to mute"
    var setBtn = function (mode) {
      if (!snd) return;
      var t = { play: "Play logo animation", sound: "Play with sound", mute: "Sound on – tap to mute" }[mode];
      if (!t) { snd.hidden = true; return; }
      label.textContent = t; snd.dataset.mode = mode; snd.hidden = false;
    };
    var startWithSound = function () {              // runs from a tap, so phones always allow it
      if (!hv.getAttribute("src")) hv.setAttribute("src", srcStore);
      hv.muted = false; try { hv.currentTime = 0; } catch (e) { /* not ready yet */ }
      var p = hv.play();
      if (p && p.catch) p.catch(function () { hv.muted = true; var p2 = hv.play(); if (p2 && p2.catch) p2.catch(function () {}); });
    };
    hv.addEventListener("playing", function () { hb.classList.add("playing-anim"); var hs = hb.closest(".hero2"); if (hs) hs.classList.add("anim-on"); setBtn(hv.muted ? (soundOpt ? "sound" : "") : "mute"); });
    hv.addEventListener("error", function () { hb.classList.remove("has-anim"); setBtn(""); });
    if (snd) snd.addEventListener("click", function () {
      if (snd.dataset.mode === "mute") { hv.muted = true; setBtn(soundOpt ? "sound" : ""); } else { startWithSound(); }
    });
    if (skip) {                                      // data-saver / very slow network / reduced-motion: don't download, offer a tap instead
      hv.pause(); hv.removeAttribute("src"); hv.load(); setBtn("play");
    } else {
      var tryPlay = function () { if (hv.paused && !hv.ended && hv.getAttribute("src")) { var q = hv.play(); if (q && q.catch) q.catch(function () { /* autoplay blocked: the tap button below appears */ }); } };
      tryPlay();
      hv.addEventListener("canplay", tryPlay);
      document.addEventListener("visibilitychange", function () { if (!document.hidden) tryPlay(); });
      ["pointerdown", "touchstart", "scroll", "keydown"].forEach(function (ev) { window.addEventListener(ev, tryPlay, { once: true, passive: true }); });
      setTimeout(function () { if (hv.paused && !hb.classList.contains("playing-anim")) setBtn("play"); }, 3500);   // still not playing: let the visitor start it
    }
  }

  // service category tabs
  $$("[data-tabs]").forEach(function (g) {
    $$("button", g).forEach(function (b) {
      b.addEventListener("click", function () {
        $$("button", g).forEach(function (x) { x.classList.toggle("active", x === b); });
        $$(".tabpanel").forEach(function (p) { p.classList.toggle("active", p.id === b.dataset.tab); });
      });
    });
  });

  // sliders: arrows, dots, swipe (native scroll-snap), optional autoplay
  $$("[data-slider]").forEach(function (sl) {
    var tr = $(".track", sl), cards = $$(".track > *", sl), dots = $(".dots", sl);
    if (!tr || !cards.length) return;
    function step() { var g = parseFloat(getComputedStyle(tr).columnGap) || 16; return cards[0].getBoundingClientRect().width + g; }
    var prev = $(".prev", sl), next = $(".next", sl);
    if (prev) prev.addEventListener("click", function () { tr.scrollBy({ left: -step(), behavior: "smooth" }); });
    if (next) next.addEventListener("click", function () { tr.scrollBy({ left: step(), behavior: "smooth" }); });
    if (dots) {
      cards.forEach(function (_, i) { var d = document.createElement("i"); d.addEventListener("click", function () { tr.scrollTo({ left: i * step(), behavior: "smooth" }); }); dots.appendChild(d); });
      var setDots = function () { var i = Math.round(tr.scrollLeft / step()); $$("i", dots).forEach(function (d, k) { d.classList.toggle("on", k === i); }); };
      tr.addEventListener("scroll", setDots, { passive: true }); setDots();
    }
    var auto = parseInt(sl.dataset.auto || "0", 10), paused = false;
    if (auto) {
      ["mouseenter", "touchstart", "focusin"].forEach(function (e) { sl.addEventListener(e, function () { paused = true; }, { passive: true }); });
      ["mouseleave", "touchend", "focusout"].forEach(function (e) { sl.addEventListener(e, function () { paused = false; }, { passive: true }); });
      setInterval(function () {
        if (paused || document.hidden) return;
        if (tr.scrollLeft + tr.clientWidth >= tr.scrollWidth - 6) tr.scrollTo({ left: 0, behavior: "smooth" });
        else tr.scrollBy({ left: step(), behavior: "smooth" });
      }, auto);
    }
  });

  // video cards: tap to play (one at a time), sound toggle, YouTube on demand, pause when scrolled away
  var vcards = $$(".vcard");
  function pauseAll(except) {
    vcards.forEach(function (c) {
      if (c === except) return;
      var v = $("video", c); if (v && !v.paused) v.pause();
      c.classList.remove("playing");
    });
  }
  vcards.forEach(function (c) {
    var v = $("video", c), mute = $(".vmute", c);
    c.addEventListener("click", function (e) {
      if (e.target.closest(".vmute")) return;
      if (v) {
        if (v.paused) { pauseAll(c); v.muted = false; if (mute) mute.classList.remove("muted"); var p = v.play(); if (p && p.catch) p.catch(function () { v.muted = true; v.play(); if (mute) mute.classList.add("muted"); }); c.classList.add("playing"); }
        else { v.pause(); c.classList.remove("playing"); }
      } else if (c.dataset.yt) {
        pauseAll(c);
        c.innerHTML = '<iframe src="https://www.youtube.com/embed/' + c.dataset.yt + '?autoplay=1&rel=0&playsinline=1" allow="autoplay; encrypted-media; picture-in-picture" allowfullscreen></iframe>';
        c.classList.add("playing");
      } else if (c.dataset.link) { window.open(c.dataset.link, "_blank", "noopener"); }
      else { toast("Sample tile – upload RK's real videos under Marketing → Website videos."); }
    });
    if (mute && v) mute.addEventListener("click", function () { v.muted = !v.muted; mute.classList.toggle("muted", v.muted); });
    if (v) { v.addEventListener("pause", function () { c.classList.remove("playing"); }); }
  });
  if ("IntersectionObserver" in window) {
    var vio = new IntersectionObserver(function (es) { es.forEach(function (e) { if (!e.isIntersecting) { var v = $("video", e.target); if (v && !v.paused) v.pause(); } }); }, { threshold: 0.25 });
    vcards.forEach(function (c) { vio.observe(c); });
  }

  // copy phone number helper (call chooser on desktop)
  $$("[data-copy]").forEach(function (b) { b.addEventListener("click", function (e) { e.preventDefault(); e.stopPropagation(); if (navigator.clipboard) navigator.clipboard.writeText(b.dataset.copy); toast("Number copied"); }); });
})();
