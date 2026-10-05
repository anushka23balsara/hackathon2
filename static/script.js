const inp = document.getElementById("roll"), out = document.getElementById("out");
const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];
let data = null, day = null, reqId = 0, timer = null;

const esc = s => String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const errBox = msg => '<div class="err" role="alert">' + esc(msg) + '</div>';
const mins = t => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
const tm = t => { const [h, m] = t.split(":").map(Number); return (h % 12 || 12) + ":" + String(m).padStart(2, "0") + " " + (h >= 12 ? "PM" : "AM"); };
const todayKey = () => { const k = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"][new Date().getDay()]; return DAYS.includes(k) ? k : "Mon"; };

// Called on every keystroke; waits a moment, then asks the Flask API
function lookup() {
  inp.value = inp.value.replace(/[^a-z0-9]/gi, "").toUpperCase();
  const v = inp.value;
  clearTimeout(timer);
  if (v.length < 3) { out.innerHTML = ""; return; }
  timer = setTimeout(() => fetchStudent(v), 250);
}

async function fetchStudent(v) {
  const id = ++reqId;
  try {
    const res = await fetch("/" + encodeURIComponent(v));
    const json = await res.json();
    if (id !== reqId) return;                                   // ignore outdated responses
    if (res.status === 404 && json.partial) { out.innerHTML = ""; return; }   // still typing
    if (!res.ok) { out.innerHTML = errBox(json.error || "Something went wrong."); return; }
    data = json;
    day = todayKey();
    render();
  } catch (e) {
    if (id === reqId) out.innerHTML = errBox("Could not reach the server. Check your connection and try again.");
  }
}

function render() {
  const s = data.student;
  const ini = s.name.split(" ").map(w => w[0]).slice(0, 2).join("");
  out.innerHTML =
    '<div class="card"><div class="who"><div class="av">' + esc(ini) + '</div><div><h2>' + esc(s.name) + '</h2><p>' + esc(s.branch) + '</p></div></div>' +
    '<div class="facts"><div><small>Roll no.</small><b>' + esc(s.roll_no) + '</b></div><div><small>Batch</small><b>' + esc(s.batch) + '</b></div><div><small>Sr. no.</small><b>' + esc(s.sr_no) + '</b></div></div>' +
    '<p class="mates">' + esc(s.batch_size) + ' students in batch ' + esc(s.batch) + '.</p></div><div id="tt"></div><div id="att"></div>';
  drawTT();
  drawAttendance(new Date().toISOString().slice(0, 7));
}

async function drawAttendance(month) {
  const el = document.getElementById("att");
  const roll = data.student.roll_no;
  let json;
  try {
    const res = await fetch("/api/student/" + roll + "/attendance?month=" + month);
    json = await res.json();
    if (!res.ok) { el.innerHTML = errBox(json.error || "Could not load attendance."); return; }
  } catch (e) { el.innerHTML = errBox("Could not reach the server."); return; }

  const rows = json.report.map(r =>
    '<tr><td>' + esc(r.subject) + '</td><td>' + r.held + '</td><td>' + r.attended + '</td>' +
    '<td>' + (r.percent === null ? '—' : r.percent + '%') + '</td></tr>'
  ).join("");
  const t = json.totals;
  el.innerHTML =
    '<h3 class="att-h">Monthly attendance</h3>' +
    '<div class="row"><label for="month" class="visually-hidden">Month</label><input type="month" id="month" value="' + month + '" max="' + new Date().toISOString().slice(0, 7) + '"></div>' +
    (json.report.length
      ? '<table class="roster att-table"><thead><tr><th>Subject</th><th>Held</th><th>Attended</th><th>%</th></tr></thead><tbody>' + rows +
        '<tr class="att-total"><td>Total</td><td>' + t.held + '</td><td>' + t.attended + '</td><td>' + (t.percent === null ? '—' : t.percent + '%') + '</td></tr></tbody></table>'
      : '<p class="free">No lectures have been recorded for this month yet.</p>');
  document.getElementById("month").onchange = e => drawAttendance(e.target.value);
}

function drawTT() {
  const s = data.student, n = new Date(), nowM = n.getHours() * 60 + n.getMinutes();
  const isToday = day === todayKey() && n.getDay() >= 1 && n.getDay() <= 5;
  const tabs = DAYS.map(d => '<button data-d="' + d + '" aria-pressed="' + (d === day) + '">' + d + '</button>').join("");
  const cards = (data.timetable[day] || []).map(x => {
    const off = x.code === "LIB" || x.code === "SL";
    const live = isToday && nowM >= mins(x.start) && nowM < mins(x.end);
    return '<div class="slot' + (live ? ' now' : off ? ' off' : '') + '"><div class="time">' + tm(x.start) + '<span>to ' + tm(x.end) + '</span></div><div><h3>' + esc(x.subject) + (live ? '<span class="tag">Now</span>' : '') + '</h3><div class="meta">' +
      (x.faculty ? '<span>Faculty <b>' + esc(x.faculty) + '</b></span>' : '') + (x.room ? '<span>Room <b>' + esc(x.room) + '</b></span>' : '') +
      (x.batch_specific ? '<span>Batch <b>' + esc(s.batch) + '</b></span>' : '') + '</div></div></div>';
  }).join("");
  const el = document.getElementById("tt");
  el.innerHTML = '<div class="days" role="group" aria-label="Choose day">' + tabs + '</div>' + (cards || '<p class="free">No classes.</p>');
  el.querySelectorAll("[data-d]").forEach(b => b.onclick = () => { day = b.dataset.d; drawTT(); });
}

inp.addEventListener("input", lookup);
document.querySelectorAll("[data-n]").forEach(b => b.onclick = () => { inp.value = b.dataset.n; fetchStudent(b.dataset.n); });
