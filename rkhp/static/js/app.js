/* RK Hair Pro – shared front-end behaviour */
(function () {
  var csrf = (document.querySelector('meta[name="csrf"]') || {}).content || "";
  window.RK = { csrf: csrf };

  // add CSRF token to every POST form
  function armForms(root) {
    (root || document).querySelectorAll('form[method="post" i], form[method="POST"]').forEach(function (f) {
      if (!f.querySelector('input[name="_csrf"]')) {
        var i = document.createElement("input"); i.type = "hidden"; i.name = "_csrf"; i.value = csrf; f.appendChild(i);
      }
    });
  }
  armForms();

  document.addEventListener("click", function (e) {
    var t = e.target.closest("[data-modal]");
    if (t) { e.preventDefault(); var d = document.getElementById(t.getAttribute("data-modal")); if (d) { armForms(d); d.showModal(); fillModal(d, t); } return; }
    var c = e.target.closest("[data-close]");
    if (c) { var dd = c.closest("dialog"); if (dd) dd.close(); return; }
    var n = e.target.closest("[data-nav]");
    if (n) { document.body.classList.toggle("nav-open"); return; }
    if (document.body.classList.contains("nav-open") && !e.target.closest(".sidebar")) document.body.classList.remove("nav-open");
    var x = e.target.closest(".flash .x"); if (x) x.parentNode.remove();
  });
  // click on a dialog backdrop closes it
  document.addEventListener("mousedown", function (e) { if (e.target.tagName === "DIALOG") e.target.close(); });

  // fill modal inputs from data-* attributes on the trigger: data-fill-name="x"
  function fillModal(d, t) {
    Array.prototype.forEach.call(t.attributes, function (a) {
      if (a.name.indexOf("data-fill-") === 0) {
        var name = a.name.slice(10);
        var el = d.querySelector('[name="' + name + '"]');
        if (el) { if (el.type === "checkbox") el.checked = a.value === "1"; else el.value = a.value; }
        var out = d.querySelector('[data-out="' + name + '"]'); if (out) out.textContent = a.value;
      }
      if (a.name.indexOf("data-out-") === 0) {
        var o = d.querySelector('[data-out="' + a.name.slice(9) + '"]'); if (o) o.textContent = a.value;
      }
      if (a.name === "data-action") { var f = d.querySelector("form"); if (f) f.setAttribute("action", a.value); }
    });
  }

  document.addEventListener("submit", function (e) {
    var f = e.target;
    var m = f.getAttribute("data-confirm") || (e.submitter && e.submitter.getAttribute("data-confirm"));
    if (m && !window.confirm(m)) e.preventDefault();
  });

  // auto-hide flash after a while
  setTimeout(function () { document.querySelectorAll(".flash.ok,.flash.wa").forEach(function (f) { f.style.opacity = ".0"; f.style.transition = "opacity .6s"; setTimeout(function () { f.remove(); }, 700); }); }, 9000);

  window.rkPost = function (url, data) {
    var fd = new FormData(); for (var k in data) fd.append(k, data[k]); fd.append("_csrf", csrf);
    return fetch(url, { method: "POST", body: fd, headers: { "X-CSRF": csrf } }).then(function (r) { return r.json(); });
  };
  window.inr = function (v) {
    v = Number(v || 0); var neg = v < 0; v = Math.abs(v); var s = v.toFixed(v % 1 ? 2 : 0).split("."); var w = s[0];
    if (w.length > 3) { var h = w.slice(0, -3).replace(/\B(?=(\d\d)+(?!\d))/g, ","); w = h + "," + w.slice(-3); }
    return (neg ? "-" : "") + "₹" + w + (s[1] ? "." + s[1] : "");
  };
  window.rkDebounce = function (fn, ms) { var t; return function () { var a = arguments, c = this; clearTimeout(t); t = setTimeout(function () { fn.apply(c, a); }, ms); }; };
})();
