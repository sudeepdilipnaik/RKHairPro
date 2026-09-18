/* Live slot picker shared by the public booking page and the staff booking form */
function initBooking(cfg) {
  var form = document.getElementById(cfg.form);
  if (!form) return;
  var q = function (n) { return form.querySelector('[name="' + n + '"]'); };
  var branch = q("branch_id"), dateEl = q("date"), emp = q("employee_id"), time = q("time");
  var msg = document.getElementById("slot-msg"), sum = document.getElementById("sum-box");
  var seq = 0, wantTime = cfg.preTime || (time && time.dataset.selected) || "", wantEmp = cfg.preEmp || (emp && emp.dataset.selected) || "";
  function svcIds() { return Array.prototype.map.call(form.querySelectorAll('input[name="services"]:checked'), function (i) { return i.value; }); }
  function summary() {
    var n = 0, tot = 0, dur = 0, names = [];
    form.querySelectorAll('input[name="services"]:checked').forEach(function (i) { n++; tot += +i.dataset.price; dur += +i.dataset.dur; names.push(i.dataset.name); });
    if (sum) {
      sum.querySelector("[data-s=list]").innerHTML = names.length ? names.map(function (x) { return "<div class='stat-row'><span>" + x + "</span></div>"; }).join("") : "<div class='muted'>No service selected yet.</div>";
      sum.querySelector("[data-s=total]").textContent = window.inr(tot);
      sum.querySelector("[data-s=dur]").textContent = dur ? (Math.floor(dur / 60) ? Math.floor(dur / 60) + " hr " : "") + (dur % 60 ? dur % 60 + " min" : "") : "—";
    }
  }
  function refresh() {
    summary();
    if (!branch.value || !dateEl.value || !svcIds().length) {
      time.innerHTML = "<option value=''>Choose branch, services and date first</option>"; if (msg) msg.textContent = ""; return;
    }
    var my = ++seq, url = "/api/slots?branch=" + branch.value + "&date=" + dateEl.value + "&services=" + svcIds().join(",") + "&employee=" + (emp.value || "") +
      (cfg.exclude ? "&exclude=" + cfg.exclude : "") + (cfg.public ? "&public=1" : "");
    time.innerHTML = "<option value=''>Checking availability…</option>";
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      if (my !== seq) return;
      // stylist dropdown
      var cur = emp.value || wantEmp;
      var html = "<option value=''>Any available stylist</option>";
      (d.employees || []).forEach(function (e) { html += "<option value='" + e.id + "'" + (String(e.id) === String(cur) ? " selected" : "") + ">" + e.name + " · " + e.role + "</option>"; });
      emp.innerHTML = html; wantEmp = "";
      // time dropdown
      var t = "<option value=''>" + (d.slots && d.slots.length ? "Select a time slot (" + d.slots.length + " available)" : "No slots available") + "</option>";
      (d.slots || []).forEach(function (s) { t += "<option value='" + s.time + "'" + (s.time === wantTime ? " selected" : "") + ">" + s.label + "</option>"; });
      time.innerHTML = t; wantTime = "";
      if (msg) { msg.textContent = d.message || ""; msg.style.display = d.message ? "block" : "none"; }
    });
  }
  ["change"].forEach(function (ev) {
    if (branch.tagName === "SELECT") branch.addEventListener(ev, function () { emp.value = ""; refresh(); });
    dateEl.addEventListener(ev, function () { emp.value = ""; refresh(); });
    emp.addEventListener(ev, function () { wantTime = time.value; refresh(); });
    form.querySelectorAll('input[name="services"]').forEach(function (i) { i.addEventListener(ev, function () { wantTime = time.value; refresh(); }); });
  });
  var mob = q("mobile");
  if (mob && cfg.lookup) {
    mob.addEventListener("input", window.rkDebounce(function () {
      var v = mob.value.replace(/\D/g, "").slice(-10);
      if (v.length === 10) fetch("/api/client?mobile=" + v).then(function (r) { return r.json(); }).then(function (c) {
        var hint = document.getElementById("client-hint");
        if (c.found) { q("name").value = c.name; if (q("email") && !q("email").value) q("email").value = c.email; if (hint) hint.innerHTML = "Existing client · " + c.points + " loyalty points"; }
        else if (hint) hint.textContent = "New client";
      });
    }, 350));
  }
  refresh();
}
