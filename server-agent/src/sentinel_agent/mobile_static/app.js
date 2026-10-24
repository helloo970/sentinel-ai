"use strict";
// All texts are set with textContent (alert titles and previews may contain third-party
// data). Commands are signed here; they are only verified on the PC.

const $ = (id) => document.getElementById(id);
const S = window.SentinelSign;
const PAIR_KEY = "sentinel_pair_secret";
let last = null;
let busy = false;

// ------------------------------------------------------------------ Helpers

function storage(action, value) {
  try {
    if (action === "get") return window.localStorage.getItem(PAIR_KEY);
    if (action === "set") window.localStorage.setItem(PAIR_KEY, value);
    if (action === "del") window.localStorage.removeItem(PAIR_KEY);
  } catch (e) {
    return null;
  }
  return null;
}

async function call(path, body) {
  const options = body
    ? {
        method: "POST",
        headers: { "Content-Type": "application/json", "X-Sentinel": "1" },
        body: JSON.stringify(body),
        credentials: "same-origin",
      }
    : { credentials: "same-origin" };
  const response = await fetch(path, options);
  let data = {};
  try {
    data = await response.json();
  } catch (e) {
    data = {};
  }
  return { status: response.status, data };
}

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(text, className, onClick) {
  const node = el("button", className, text);
  node.type = "button";
  node.addEventListener("click", onClick);
  return node;
}

function toast(text, tone) {
  const box = $("toast");
  box.textContent = text;
  box.className = "panel toast " + (tone || "");
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (box.hidden = true), 6000);
}

function ago(seconds) {
  if (seconds === null || seconds === undefined) return "never";
  if (seconds < 90) return Math.round(seconds) + " s";
  return Math.round(seconds / 60) + " min";
}

function bytes(value) {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024;
    i += 1;
  }
  return value.toFixed(i ? 1 : 0) + " " + units[i];
}

// ------------------------------------------------------------------ Fire alarm

let audioCtx = null;
let seenAlerts = null;
let sirenUntil = 0;

function unlockAudio() {
  try {
    audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") audioCtx.resume();
  } catch (e) {
    audioCtx = null;
  }
  $("sound").textContent = audioCtx ? "🔔 Alarm sound on" : "Sound not available";
  $("sound").className = "ghost small " + (audioCtx ? "on" : "");
}

/** Like a smoke detector: 3x short, pause; critical 2 rounds, warning 1 round. */
function siren(severity) {
  if (!audioCtx) return;
  const now = audioCtx.currentTime;
  if (now < sirenUntil) return;
  const gain = audioCtx.createGain();
  gain.gain.value = 0.4;
  gain.connect(audioCtx.destination);
  let t = now + 0.05;
  const rounds = severity === "critical" ? 2 : 1;
  for (let r = 0; r < rounds; r++) {
    for (let i = 0; i < 3; i++) {
      const osc = audioCtx.createOscillator();
      osc.type = "square";
      osc.frequency.setValueAtTime(950, t);
      osc.frequency.linearRampToValueAtTime(1250, t + 0.4);
      osc.connect(gain);
      osc.start(t);
      osc.stop(t + 0.4);
      t += 0.6;
    }
    t += 0.4;
  }
  sirenUntil = t;
  if (navigator.vibrate) navigator.vibrate([400, 200, 400, 200, 400]);
}

function checkNewAlerts(recent) {
  const ids = new Set(recent.map((a) => a.id + "|" + a.title));
  if (seenAlerts) {
    const fresh = recent.filter((a) => !seenAlerts.has(a.id + "|" + a.title));
    const worst = fresh.some((a) => a.severity === "critical")
      ? "critical"
      : fresh.some((a) => a.severity === "warn")
        ? "warn"
        : null;
    if (worst) siren(worst);
  }
  seenAlerts = ids;
}

// ------------------------------------------------------------------ Signed commands

function secret() {
  const code = storage("get");
  return code ? S.base32(code) : null;
}

function randomHex(n) {
  const buf = new Uint8Array(n);
  window.crypto.getRandomValues(buf);
  return S.hex(buf);
}

async function send(command, needPin) {
  const key = secret();
  if (!key) {
    toast("Phone is not paired.", "crit");
    return;
  }
  let pin = null;
  if (needPin) {
    pin = window.prompt("Owner PIN (from the PC)");
    if (!pin) return;
  }
  const payload = JSON.stringify(command);
  const nonce = randomHex(16);
  const ts = Math.floor(Date.now() / 1000);
  const envelope = { payload, nonce, ts, sig: S.signEnvelope(S.derive(key, "pair"), nonce, ts, payload) };
  if (pin) envelope.pin_sig = S.signEnvelope(S.derive(key, "pin:" + pin), nonce, ts, payload);
  const result = await call("/m/api/command", { envelope });
  if (result.status === 200) {
    toast("Sent - the PC will answer shortly ...");
    setTimeout(refresh, 1500);
  } else {
    toast("Could not send: " + (result.data.error || result.status), "crit");
  }
}

// ------------------------------------------------------------------ Display

function show(loggedIn) {
  $("login").hidden = loggedIn;
  $("app").hidden = !loggedIn;
}

function renderList(id, items, build, empty) {
  const list = $(id);
  list.replaceChildren();
  if (!items.length && empty) list.appendChild(el("li", "dim", empty));
  items.forEach((item) => list.appendChild(build(item)));
}

function render(data) {
  // Only redraw when something changed - otherwise taps on buttons get lost
  const { pc_age_s: _age, ...stable } = data;
  const signature = JSON.stringify(stable) + "|" + Boolean(secret());
  if (signature === render.signature) {
    $("pc").textContent = data.pc_online ? "PC online" : "PC offline - " + ago(data.pc_age_s);
    return;
  }
  render.signature = signature;
  last = data;
  const pc = data.pc || {};
  const controlling = Boolean(secret()) && Boolean(pc.paired);
  $("pc").textContent = data.pc_online ? "PC online" : "PC offline - " + ago(data.pc_age_s);
  $("pc").className = "chip " + (data.pc_online ? "ok" : "crit");

  const state = $("state");
  if (pc.halted) {
    state.textContent = "EMERGENCY STOP ACTIVE";
    state.className = "value crit";
  } else if (data.halt_pending) {
    state.textContent = "Emergency stop requested ...";
    state.className = "value warn";
  } else {
    state.textContent = data.pc ? "Active" : "-";
    state.className = "value ok";
  }
  const autonomy = $("autonomy");
  if (document.activeElement !== autonomy && pc.autonomy !== null && pc.autonomy !== undefined) {
    autonomy.value = String(pc.autonomy);
  }
  autonomy.disabled = !controlling;
  $("mode").textContent = pc.mode ? "Mode: " + pc.mode + (pc.quiet ? " (quiet)" : "") : "";

  $("pairing").hidden = Boolean(secret());
  $("control").hidden = !controlling;
  $("unpair").hidden = !secret();
  $("ack-all").hidden = !controlling;
  if (secret() && pc && pc.paired === false && data.pc) {
    $("pairing").hidden = false;
  }

  checkNewAlerts(pc.recent || []);
  const alerts = pc.alerts || {};
  $("crit").textContent = alerts.critical || 0;
  $("warn").textContent = alerts.warn || 0;
  $("info").textContent = alerts.info || 0;
  renderList("recent", pc.recent || [], (item) => {
    const li = el("li", item.severity, item.title);
    if (controlling && Number.isInteger(item.id)) {
      li.appendChild(button("OK", "ghost small", () => send({ cmd: "ack", id: item.id })));
    }
    return li;
  });

  if (controlling) {
    renderList(
      "approvals",
      pc.approvals || [],
      (a) => {
        const li = el("li", a.risk === "final" || a.risk === "high" ? "warn" : "");
        li.appendChild(el("div", "", a.title));
        const row = el("div", "buttons");
        row.appendChild(
          button(a.requires_pin ? "Confirm (PIN)" : "Confirm", "small", () =>
            send({ cmd: "approve", id: a.id }, a.requires_pin),
          ),
        );
        row.appendChild(button("Ablehnen", "ghost small", () => send({ cmd: "reject", id: a.id })));
        li.appendChild(row);
        return li;
      },
      "No open approvals",
    );
    const modes = $("modes");
    modes.replaceChildren();
    (pc.modes || []).forEach((m) =>
      modes.appendChild(
        button(m.active ? "End " + m.title : m.title, m.active ? "small" : "ghost small", () =>
          send(
            m.active
              ? { cmd: "action", name: "stop_mode", params: {} }
              : { cmd: "action", name: "start_mode", params: { name: m.name } },
          ),
        ),
      ),
    );
    const routines = $("routines");
    routines.replaceChildren();
    (pc.routines || []).forEach((r) =>
      routines.appendChild(
        button("▶ " + r.title, "ghost small", () =>
          send({ cmd: "action", name: "run_routine", params: { name: r.name } }),
        ),
      ),
    );
    const tests = $("tests");
    tests.replaceChildren();
    const groups = {};
    (pc.tests || []).forEach((t) => (groups[t.group] = groups[t.group] || []).push(t));
    Object.keys(groups).forEach((group) => {
      tests.appendChild(el("div", "label spaced", group));
      const row = el("div", "buttons");
      groups[group].forEach((t) =>
        row.appendChild(button(t.title, "ghost small", () => send({ cmd: "test_alert", rule: t.rule }))),
      );
      tests.appendChild(row);
    });
    const programs = $("programs");
    programs.replaceChildren();
    (pc.programs || []).forEach((p) =>
      programs.appendChild(
        button(p.title, "ghost small", () =>
          send({ cmd: "action", name: "launch_program", params: { name: p.name } }),
        ),
      ),
    );
    renderList(
      "undo",
      pc.undo || [],
      (u) => {
        const li = el("li", "", u.title);
        li.appendChild(button("Undo", "ghost small", () => send({ cmd: "undo", id: u.id })));
        return li;
      },
      "Nothing to undo",
    );
    renderList(
      "results",
      (pc.results || []).slice().reverse().slice(0, 5),
      (r) => el("li", r.status === "executed" ? "" : "warn", r.message || r.status),
    );
  }

  const server = data.server || {};
  $("server").textContent = data.server
    ? "Load " + (server.load || [0])[0] + " · RAM " + server.memory_percent + " % · Disk " +
      server.disk_percent + " % · " + server.uptime_h + " h"
    : "–";
  const backups = data.backups || {};
  renderList(
    "backups",
    backups.paths || [],
    (entry) => {
      const age = entry.newest ? (Date.now() / 1000 - entry.newest) / 3600 : null;
      const bad = !entry.exists || age === null || age > 26;
      const text =
        entry.path + ": " +
        (entry.exists
          ? age === null
            ? "empty"
            : Math.round(age) + " h ago · " + entry.files + " files · " + bytes(entry.bytes)
          : "missing");
      return el("li", bad ? "critical" : "", text);
    },
    backups.available ? "" : "No backup folder set up",
  );

  const stop = $("stop");
  if (pc.halted && controlling) {
    stop.textContent = "RELEASE EMERGENCY STOP (PIN)";
    stop.className = "primary big-button";
    stop.disabled = false;
    $("stop-hint").textContent = "Afterwards actions and agents run again.";
  } else {
    stop.textContent = "EMERGENCY STOP";
    stop.className = "danger";
    stop.disabled = Boolean(pc.halted || data.halt_pending);
    $("stop-hint").textContent = controlling ? "" : "It can only be released on the PC or from a paired phone.";
  }
}

async function refresh() {
  if (busy) return;
  busy = true;
  try {
    const result = await call("/m/api/status");
    if (result.status === 401) {
      show(false);
      return;
    }
    show(true);
    if (result.status === 200) render(result.data);
  } finally {
    busy = false;
  }
}

// ------------------------------------------------------------------ Events

$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = $("login-error");
  error.hidden = true;
  const result = await call("/m/api/login", { pin: $("pin").value });
  $("pin").value = "";
  if (result.status === 200) {
    await refresh();
    return;
  }
  error.textContent =
    result.status === 429 ? "Too many failed attempts. Please wait 5 minutes." : "Wrong PIN.";
  error.hidden = false;
});

$("pair-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const code = $("pair-code").value.toUpperCase().replace(/[^A-Z2-7]/g, "");
  if (code.length < 26) {
    toast("The code is too short.", "crit");
    return;
  }
  storage("set", code);
  $("pair-code").value = "";
  toast("Paired.", "ok");
  refresh();
});

$("chat-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const text = $("chat-text").value.trim();
  if (!text) return;
  $("chat-text").value = "";
  send({ cmd: "chat", text });
});

$("autonomy").addEventListener("change", (event) => {
  send({ cmd: "autonomy", level: Number(event.target.value) }, true);
});

$("ack-all").addEventListener("click", () => send({ cmd: "ack_all" }));

$("sound").addEventListener("click", () => {
  unlockAudio();
  siren("warn");
});
document.addEventListener("pointerdown", () => audioCtx || unlockAudio(), { once: true });

$("stop").addEventListener("click", async () => {
  const pc = (last && last.pc) || {};
  if (pc.halted && secret() && pc.paired) {
    if (window.confirm("Release the emergency stop? Actions and agents will run again.")) {
      await send({ cmd: "resume" }, true);
    }
    return;
  }
  if (!window.confirm("Trigger the emergency stop? All actions and agents on the PC will be locked.")) {
    return;
  }
  await call("/m/api/stop", { confirm: true });
  await refresh();
});

$("unpair").addEventListener("click", () => {
  if (window.confirm("Remove pairing from this phone?")) {
    storage("del");
    refresh();
  }
});

$("logout").addEventListener("click", async () => {
  await call("/m/api/logout", {});
  show(false);
});

// Take the pairing code from the QR link (#pair=...); the part after # never goes to the server
if (window.location.hash.startsWith("#pair=")) {
  const code = window.location.hash.slice(6).toUpperCase().replace(/[^A-Z2-7]/g, "");
  if (code.length >= 26) storage("set", code);
  window.history.replaceState(null, "", "/m");
}

refresh();
setInterval(refresh, 4000);
