(() => {
const STATUSES = ["New","Contacted","Consultation Requested","Confirmed","Proposal Sent","In Progress","Completed","Declined"];
const $ = s => document.querySelector(s);
let csrf = "", page = 1, per = 25, total = 0, cur = null;
// DOM builder: everything is inserted with textContent, so client-submitted text can never run as HTML.
const h = (t, a = {}, ...k) => { const e = document.createElement(t);
  for (const [x, v] of Object.entries(a)) x.startsWith("on") ? e.addEventListener(x.slice(2), v) : x === "class" ? e.className = v : e.setAttribute(x, v);
  k.flat().forEach(c => e.append(c instanceof Node ? c : document.createTextNode(c ?? ""))); return e; };
async function api(path, opt = {}) {
  const r = await fetch("/admin/api" + path, { credentials: "same-origin", ...opt,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf, ...(opt.headers || {}) } });
  let j = {}; try { j = await r.json(); } catch (_) {}
  if (r.status === 401 && path !== "/login") { showLogin(); throw new Error("Signed out"); }
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : "Request failed (" + r.status + ")");
  return j;
}
const fmt = s => s ? new Date(s).toLocaleString() : "—";
function showLogin() { $("#app").classList.add("hide"); $("#login").classList.remove("hide"); }
function showApp() { $("#login").classList.add("hide"); $("#app").classList.remove("hide"); load(); }
async function boot() { try { csrf = (await api("/me")).csrf; showApp(); } catch (_) { showLogin(); } }
$("#go").onclick = async () => { $("#le").textContent = ""; try {
  csrf = (await api("/login", { method: "POST", body: JSON.stringify({ username: $("#u").value, password: $("#p").value }) })).csrf;
  $("#p").value = ""; showApp(); } catch (e) { $("#le").textContent = e.message; } };
$("#p").addEventListener("keydown", e => e.key === "Enter" && $("#go").click());
$("#out").onclick = async () => { await api("/logout", { method: "POST" }).catch(() => {}); csrf = ""; showLogin(); };
$("#sf").append(h("option", { value: "" }, "All statuses"), ...STATUSES.map(s => h("option", { value: s }, s)));
let tm; $("#q").oninput = () => { clearTimeout(tm); tm = setTimeout(() => { page = 1; load(); }, 300); };
$("#sf").onchange = () => { page = 1; load(); };
$("#pv").onclick = () => { if (page > 1) { page--; load(); } }; $("#nx").onclick = () => { if (page * per < total) { page++; load(); } };
$("#bAv").onclick = openAvail;

async function load() {
  show("list");
  try { const j = await api(`/inquiries?q=${encodeURIComponent($("#q").value)}&status=${encodeURIComponent($("#sf").value)}&page=${page}&per=${per}`);
    total = j.total; $("#pg").textContent = `Page ${page} · ${total} inquiries`;
    const rows = $("#rows"); rows.replaceChildren(...(j.items.length ? j.items.map(i => {
      const a = i.appointment; const tr = h("tr", { class: "i", tabindex: "0", onclick: () => open(i.ref), onkeydown: e => e.key === "Enter" && open(i.ref) },
        h("td", {}, i.ref, h("div", { class: "hc", style: "color:#9b9692;font-size:.78rem" }, fmt(i.createdAt))),
        h("td", {}, i.name, h("div", { style: "color:#9b9692;font-size:.8rem" }, i.email)), h("td", { class: "hc" }, i.service),
        h("td", {}, h("span", { class: "tag" }, i.status)),
        h("td", { class: "hc" }, a ? h("span", { class: "tag " + (a.status === "requested" ? "r" : "") }, `${a.date} ${a.time} · ${a.status}`) : "—"),
        h("td", { class: "hc" }, h("span", { class: "tag " + (i.emailStatus === "sent" ? "" : "r") }, i.emailStatus))); return tr; })
      : [h("tr", {}, h("td", { colspan: "6" }, "No inquiries found."))]));
  } catch (e) { if (e.message !== "Signed out") alert(e.message); } }
function show(id) { ["list", "detail", "avail"].forEach(x => $("#" + x).classList.toggle("hide", x !== id)); }

async function open(ref) { cur = await api("/inquiries/" + encodeURIComponent(ref)); render(); }
async function act(path, opt) { try { cur = await api(`/inquiries/${cur.ref}${path}`, opt); render(); } catch (e) { alert(e.message); } }
function render() {
  show("detail"); const i = cur, d = i.data || {}, a = i.appointment, el = $("#detail");
  const J = v => (v && v.length ? [].concat(v).join(", ") : "—");
  const dl = rows => h("dl", {}, rows.flatMap(([k, v]) => [h("dt", {}, k), h("dd", {}, v || "—")]));
  const wa = "https://wa.me/" + i.whatsapp.replace(/\D/g, "");
  const msgConfirm = a && `Hello ${i.name}, this is Gill Studio. Your consultation is confirmed for ${a.date} at ${a.time} (${i.tzLabel}) via ${a.method}. Reference ${i.ref}.`;
  const slotDate = h("input", { type: "date", value: a ? a.date : "" }), slotTime = h("input", { type: "time", value: a ? a.time : "" });
  const status = h("select", { onchange: e => act("/status", { method: "PATCH", body: JSON.stringify({ status: e.target.value }) }) },
    STATUSES.map(s => h("option", { value: s, ...(s === i.status ? { selected: "" } : {}) }, s)));
  const note = h("textarea", { placeholder: "Internal note (never shown to the client)", maxlength: "2000" });
  el.replaceChildren(
    h("div", { class: "g", style: "margin:.8rem 0" }, h("button", { onclick: load }, "‹ Back"), h("h2", {}, i.ref + " · " + i.name)),
    h("div", { class: "card" }, h("div", { class: "g" }, h("label", {}, "Status "), status,
      h("a", { href: wa, target: "_blank", rel: "noopener" }, h("button", {}, "Open WhatsApp")), h("a", { href: "mailto:" + i.email }, h("button", {}, "Email client")))),
    h("div", { class: "card" }, h("h3", {}, "Appointment"), a ? h("div", {},
      dl([["Requested", `${a.date} ${a.time} (${i.tzLabel})`], ["Status", a.status], ["Method", a.method], ["Client time zone", a.clientTz], ["Agenda", a.agenda]]),
      h("div", { class: "g", style: "margin-top:.7rem" },
        h("button", { class: "p", onclick: () => act("/appointment/confirm", { method: "POST" }) }, "Confirm"),
        slotDate, slotTime, h("button", { onclick: () => slotDate.value && slotTime.value && act("/appointment/reschedule", { method: "POST", body: JSON.stringify({ date: slotDate.value, time: slotTime.value }) }) }, "Reschedule & confirm"),
        h("button", { class: "d", onclick: () => confirm("Decline this appointment request?") && act("/appointment/decline", { method: "POST" }) }, "Decline")),
      a.status === "confirmed" ? h("p", { style: "margin-top:.7rem" }, h("a", { href: wa + "?text=" + encodeURIComponent(msgConfirm), target: "_blank", rel: "noopener" }, "Send confirmation via WhatsApp (opens a prefilled chat — you press send)")) : "")
      : h("p", { class: "tag" }, "No appointment requested")),
    h("div", { class: "card" }, h("h3", {}, "Inquiry"), dl([["Email", i.email], ["WhatsApp", i.whatsapp], ["Prefers", i.contactMethod], ["Business", i.business],
      ["Website", d.website], ["Social", d.social], ["Service", i.service], ["Package", i.package], ["Type", d.projectType], ["Redesign notes", d.redesignNotes],
      ["Purpose", d.purpose], ["Audience", d.audience], ["Features", J(d.features)], ["Pages", d.pages], ["Domain / hosting", `${d.domain} / ${d.hosting}`],
      ["Assets", J(d.assets)], ["References", d.references], ["Budget", d.budget], ["Desired date", d.dueDate], ["Urgency", d.urgency], ["Deadline notes", d.deadlineNotes],
      ["Details", d.details], ["Marketing consent", i.marketingConsent ? "Yes" : "No"], ["Submitted", fmt(i.createdAt)]])),
    h("div", { class: "card" }, h("h3", {}, "Owner email notification"), h("p", { class: i.emailStatus === "sent" ? "ok" : "" },
      i.emailStatus + (i.emailError ? " — " + i.emailError : "") + (i.emailSentAt ? " · " + fmt(i.emailSentAt) : "")),
      h("button", { style: "margin-top:.5rem", onclick: () => act("/resend-email", { method: "POST" }) }, "Send notification again")),
    h("div", { class: "card" }, h("h3", {}, "Notes & history"), ...i.notes.map(n => h("div", { class: "note " + (n.system ? "" : "u") }, h("small", {}, fmt(n.at) + (n.system ? " · system" : " · you")), h("div", {}, n.text))),
      note, h("button", { class: "p", style: "margin-top:.5rem", onclick: () => note.value.trim() && act("/notes", { method: "POST", body: JSON.stringify({ text: note.value.trim() }) }) }, "Add note")));
}

async function openAvail() {
  show("avail"); const c = await api("/availability"); const el = $("#avail"), DN = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
  const days = DN.map((n, k) => h("label", {}, h("input", { type: "checkbox", value: k, ...(c.days.includes(k) ? { checked: "" } : {}) }), n));
  const slots = h("input", { value: c.slots.join(", ") }), notice = h("input", { type: "number", value: c.min_notice_hours }),
    ahead = h("input", { type: "number", value: c.max_days_ahead }), pend = h("input", { type: "number", value: c.max_pending_per_slot }),
    blocked = h("input", { value: c.blocked_dates.join(", "), placeholder: "YYYY-MM-DD, YYYY-MM-DD" }), msg = h("p", {});
  el.replaceChildren(h("div", { class: "g", style: "margin:.8rem 0" }, h("button", { onclick: load }, "‹ Back"), h("h2", {}, "Consultation availability")),
    h("div", { class: "card", style: "display:grid;gap:.8rem" }, h("p", { class: "tag" }, "Times are in " + c.tzLabel),
      h("div", { class: "chips" }, "Working days: ", ...days), h("label", {}, "Time slots (24h, comma separated)", slots),
      h("label", {}, "Minimum notice (hours)", notice), h("label", {}, "Booking window (days ahead)", ahead),
      h("label", {}, "Max unconfirmed requests per slot", pend), h("label", {}, "Blocked dates", blocked),
      h("div", { class: "g" }, h("button", { class: "p", onclick: async () => { try {
        const list = s => s.value.split(",").map(x => x.trim()).filter(Boolean);
        await api("/availability", { method: "PUT", body: JSON.stringify({ days: days.filter(l => l.firstChild.checked).map(l => +l.firstChild.value),
          slots: list(slots), min_notice_hours: +notice.value, max_days_ahead: +ahead.value, max_pending_per_slot: +pend.value, blocked_dates: list(blocked) }) });
        msg.className = "ok"; msg.textContent = "Saved. The public form uses these rules immediately."; } catch (e) { msg.className = "err"; msg.textContent = e.message; } } }, "Save"), msg)));
}
boot();
})();
