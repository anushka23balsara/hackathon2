const view = document.getElementById("view");
const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const tm = t => { const [h, m] = t.split(":").map(Number); return (h % 12 || 12) + ":" + String(m).padStart(2, "0") + " " + (h >= 12 ? "PM" : "AM"); };
let me = null;          // {code, name}
let schedule = [];      // this faculty's timetable rows
let options = null;     // {subjects, periods, days}

async function api(path, opts) {
  const res = await fetch(path, Object.assign({ headers: { "Content-Type": "application/json" } }, opts));
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || "Something went wrong.");
  return json;
}

async function boot() {
  try {
    const s = await api("/api/faculty/session");
    if (s.logged_in) { me = { code: s.code, name: s.name }; await loadDashboard(); }
    else renderLogin();
  } catch (e) { renderLogin(); }
}

// ---------- login ----------
function renderLogin(msg) {
  view.innerHTML =
    '<p class="sub">Log in with the code and password given to you (by default, both are your faculty code, e.g. code <b>DAP</b>, password <b>DAP</b>).</p>' +
    (msg ? '<div class="msg err">' + esc(msg) + '</div>' : '') +
    '<form class="stack" id="loginForm">' +
    '<div><label for="code">Faculty code</label><input id="code" autocomplete="username" autocapitalize="characters" required></div>' +
    '<div><label for="pw">Password</label><input id="pw" type="password" autocomplete="current-password" required></div>' +
    '<button class="btn" type="submit">Log in</button></form>';
  document.getElementById("loginForm").onsubmit = async e => {
    e.preventDefault();
    const code = document.getElementById("code").value.trim().toUpperCase();
    const password = document.getElementById("pw").value;
    try {
      const r = await api("/api/faculty/login", { method: "POST", body: JSON.stringify({ code, password }) });
      const s = await api("/api/faculty/session");
      me = { code: s.code, name: s.name };
      await loadDashboard();
    } catch (err) { renderLogin(err.message); }
  };
}

// ---------- dashboard ----------
async function loadDashboard() {
  const s = await api("/api/faculty/schedule");
  schedule = s.schedule;
  renderDashboard();
}

function topbar(title) {
  return '<div class="topbar"><div><span class="pill">' + esc(me.code) + '</span> <span class="who">' + esc(me.name) + '</span></div>' +
    '<div class="row"><button class="link" id="pwBtn">Change password</button><button class="link" id="logoutBtn">Log out</button></div></div>' +
    (title ? '<h2>' + esc(title) + '</h2>' : '');
}

function bindTopbar() {
  document.getElementById("logoutBtn").onclick = async () => { await api("/api/faculty/logout", { method: "POST" }); me = null; renderLogin(); };
  document.getElementById("pwBtn").onclick = renderChangePassword;
}

function renderDashboard() {
  const byDay = {};
  schedule.forEach(s => (byDay[s.day] = byDay[s.day] || []).push(s));
  const days = ["Mon", "Tue", "Wed", "Thu", "Fri"];
  const cards = days.filter(d => byDay[d]).map(d =>
    '<h3 style="margin:18px 0 8px">' + d + '</h3>' + byDay[d].map(s =>
      '<div class="class-card"><h3>' + esc(s.subject) + '</h3>' +
      '<div class="meta">' + tm(s.start) + '–' + tm(s.end) + ' &middot; <b>' + esc(s.branch) + '</b>' +
      (s.batch ? ' batch <b>' + esc(s.batch) + '</b>' : '') + ' &middot; Room <b>' + esc(s.room) + '</b></div>' +
      '<div class="row"><button class="btn small" data-att="' + s.id + '">Take attendance</button>' +
      '<button class="btn small secondary" data-hist="' + s.id + '">Records</button>' +
      '<button class="btn small secondary" data-edit="' + s.id + '">Edit slot</button></div></div>'
    ).join("")
  ).join("");
  view.innerHTML = topbar() + (cards || '<p class="free">No classes assigned to your code yet.</p>');
  bindTopbar();
  view.querySelectorAll("[data-att]").forEach(b => b.onclick = () => renderAttendance(Number(b.dataset.att)));
  view.querySelectorAll("[data-hist]").forEach(b => b.onclick = () => renderHistory(Number(b.dataset.hist)));
  view.querySelectorAll("[data-edit]").forEach(b => b.onclick = () => renderEdit(Number(b.dataset.edit)));
}

// ---------- attendance ----------
async function renderAttendance(timetableId, date) {
  const slot = schedule.find(s => s.id === timetableId);
  date = date || new Date().toISOString().slice(0, 10);
  const data = await api("/api/faculty/class/" + timetableId + "?date=" + date);
  const rows = data.roster.map(st =>
    '<tr data-roll="' + esc(st.roll_no) + '"><td>' + st.sr_no + '</td><td>' + esc(st.name) + ' <span class="pill">' + esc(st.roll_no) + '</span></td>' +
    '<td><div class="status-toggle"><button type="button" class="present' + (st.status === "Present" ? " on" : "") + '" data-s="Present">Present</button>' +
    '<button type="button" class="absent' + (st.status === "Absent" ? " on" : "") + '" data-s="Absent">Absent</button></div></td></tr>'
  ).join("");

  view.innerHTML = topbar(slot.subject) +
    '<p class="meta">' + esc(slot.branch) + (slot.batch ? ' &middot; batch ' + esc(slot.batch) : '') + '</p>' +
    '<div class="row"><label for="attDate" style="margin:0">Date</label><input type="date" id="attDate" value="' + date + '" max="' + new Date().toISOString().slice(0, 10) + '"></div>' +
    (data.already_marked ? '<p class="pill" style="margin-top:8px">Already marked for this date — showing saved attendance</p>' : '') +
    '<div class="row" style="margin-top:12px"><button class="btn small secondary" id="allPresent">Mark all present</button><button class="btn small secondary" id="allAbsent">Mark all absent</button></div>' +
    '<table class="roster"><thead><tr><th>#</th><th>Student</th><th>Status</th></tr></thead><tbody>' + rows + '</tbody></table>' +
    '<div class="summary"><span><b id="presentCount"></b> present</span><span><b id="totalCount">' + data.roster.length + '</b> total</span></div>' +
    '<div class="row"><button class="btn" id="saveAtt">Save attendance</button><button class="btn secondary" id="backBtn">Back</button></div>' +
    '<div id="attMsg"></div>';
  bindTopbar();

  const updateCount = () => {
    const n = view.querySelectorAll(".status-toggle button.present.on").length;
    document.getElementById("presentCount").textContent = n;
  };
  view.querySelectorAll(".status-toggle button").forEach(b => b.onclick = () => {
    const grp = b.parentElement;
    grp.querySelectorAll("button").forEach(x => x.classList.remove("on"));
    b.classList.add("on");
    updateCount();
  });
  updateCount();
  document.getElementById("allPresent").onclick = () => { view.querySelectorAll(".status-toggle").forEach(g => { g.querySelectorAll("button").forEach(b => b.classList.remove("on")); g.querySelector(".present").classList.add("on"); }); updateCount(); };
  document.getElementById("allAbsent").onclick = () => { view.querySelectorAll(".status-toggle").forEach(g => { g.querySelectorAll("button").forEach(b => b.classList.remove("on")); g.querySelector(".absent").classList.add("on"); }); updateCount(); };
  document.getElementById("attDate").onchange = e => renderAttendance(timetableId, e.target.value);
  document.getElementById("backBtn").onclick = renderDashboard;
  document.getElementById("saveAtt").onclick = async () => {
    const records = [...view.querySelectorAll("tr[data-roll]")].map(tr => ({
      roll_no: tr.dataset.roll,
      status: tr.querySelector(".status-toggle button.on").dataset.s,
    }));
    const msg = document.getElementById("attMsg");
    try {
      const r = await api("/api/faculty/attendance", { method: "POST", body: JSON.stringify({ timetable_id: timetableId, date, records }) });
      msg.innerHTML = '<div class="msg ok">Saved — ' + r.present + ' of ' + r.total + ' present. Recorded at ' + esc(r.saved_at) + ' as proof.</div>';
    } catch (err) { msg.innerHTML = '<div class="msg err">' + esc(err.message) + '</div>'; }
  };
}

// ---------- history (attendance proof records) ----------
async function renderHistory(timetableId) {
  const slot = schedule.find(s => s.id === timetableId);
  const data = await api("/api/faculty/attendance/history/" + timetableId);
  const items = data.history.map(h =>
    '<li><span>' + esc(h.date) + '</span><span>' + h.present + '/' + h.total + ' present <span class="pill">' + esc(h.marked_at) + '</span></span></li>'
  ).join("");
  view.innerHTML = topbar(slot.subject + " — records") +
    (items ? '<ul class="history-list">' + items + '</ul>' : '<p class="free">No attendance recorded yet for this class.</p>') +
    '<div class="row" style="margin-top:14px"><button class="btn secondary" id="backBtn">Back</button></div>';
  bindTopbar();
  document.getElementById("backBtn").onclick = renderDashboard;
}

// ---------- edit timetable slot ----------
async function renderEdit(timetableId) {
  const slot = schedule.find(s => s.id === timetableId);
  if (!options) options = await api("/api/faculty/options");
  const dayOpts = options.days.map(d => '<option ' + (d === slot.day ? "selected" : "") + '>' + d + '</option>').join("");
  const perOpts = f => options.periods.map(p => '<option value="' + p.no + '" ' + (p.no === slot[f] ? "selected" : "") + '>' + p.no + ' (' + tm(p.start_time) + ')</option>').join("");
  const subOpts = options.subjects.map(s => '<option value="' + s.code + '" ' + (s.code === slot.code ? "selected" : "") + '>' + esc(s.name) + '</option>').join("");

  view.innerHTML = topbar("Edit slot") +
    '<form class="stack" id="editForm">' +
    '<div class="edit-grid"><div><label>Day</label><select id="day">' + dayOpts + '</select></div>' +
    '<div><label>Subject</label><select id="subject">' + subOpts + '</select></div>' +
    '<div><label>Start period</label><select id="startP">' + perOpts("start_period") + '</select></div>' +
    '<div><label>End period</label><select id="endP">' + perOpts("end_period") + '</select></div></div>' +
    '<div><label>Room</label><input id="room" value="' + esc(slot.room || "") + '"></div>' +
    '<div class="row"><button class="btn" type="submit">Save changes</button><button class="btn secondary" type="button" id="backBtn">Cancel</button></div>' +
    '</form><div id="editMsg"></div>';
  bindTopbar();
  document.getElementById("backBtn").onclick = renderDashboard;
  document.getElementById("editForm").onsubmit = async e => {
    e.preventDefault();
    const body = {
      day: document.getElementById("day").value,
      subject_code: document.getElementById("subject").value,
      start_period: Number(document.getElementById("startP").value),
      end_period: Number(document.getElementById("endP").value),
      room: document.getElementById("room").value,
    };
    const msg = document.getElementById("editMsg");
    try {
      await api("/api/faculty/timetable/" + timetableId, { method: "PATCH", body: JSON.stringify(body) });
      await loadDashboard();
    } catch (err) { msg.innerHTML = '<div class="msg err">' + esc(err.message) + '</div>'; }
  };
}

// ---------- change password ----------
function renderChangePassword() {
  view.innerHTML = topbar("Change password") +
    '<form class="stack" id="pwForm">' +
    '<div><label>Current password</label><input type="password" id="cur" required></div>' +
    '<div><label>New password</label><input type="password" id="new" required minlength="4"></div>' +
    '<div class="row"><button class="btn" type="submit">Update password</button><button class="btn secondary" type="button" id="backBtn">Cancel</button></div>' +
    '</form><div id="pwMsg"></div>';
  bindTopbar();
  document.getElementById("backBtn").onclick = renderDashboard;
  document.getElementById("pwForm").onsubmit = async e => {
    e.preventDefault();
    const msg = document.getElementById("pwMsg");
    try {
      await api("/api/faculty/password", { method: "POST", body: JSON.stringify({ current: document.getElementById("cur").value, new: document.getElementById("new").value }) });
      msg.innerHTML = '<div class="msg ok">Password updated.</div>';
    } catch (err) { msg.innerHTML = '<div class="msg err">' + esc(err.message) + '</div>'; }
  };
}

boot();
