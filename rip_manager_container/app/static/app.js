/* Rip Remote 0.17.8 — front end for Rip Manager.
 *
 * Sections, in order:
 *   1. State and small helpers
 *   2. API access
 *   3. Drive model: turning a node's drive payload into a tile
 *   4. The tile grid
 *   5. Intake: barcode, details, and the adaptive tray control
 *   6. Drive actions
 *   7. Shares and stats
 *   8. Settings
 *   9. Updates
 *  10. Events, sounds and the refresh loop
 *  11. Login and bootstrap
 */

/* ------------------------------------------------------------------ *
 * 1. State and small helpers
 * ------------------------------------------------------------------ */

const State = {
  nodes: [],
  drives: [],
  jobs: [],
  stats: [],
  settings: null,
  draft: null,
  refreshedAt: null,
  eventCursor: Number(localStorage.eventCursor || 0),
  gridSignature: "",
  refreshTimer: null,
  intake: { nodeId: null, drive: null, barcode: "" },
};

const ACTIVE_STATES = ["starting", "ripping", "verifying", "cancelling"];
const FAILED_STATES = ["failed", "verification_failed", "interrupted"];

const STATE_LABELS = {
  starting: "Starting",
  ripping: "Ripping",
  verifying: "Checking files",
  cancelling: "Stopping",
  failed: "Failed",
  verification_failed: "File check failed",
  interrupted: "Interrupted",
  cancelled: "Cancelled",
  complete: "Complete",
};

const REASON_LABELS = {
  tray_open: "The tray is open",
  no_readable_media: "No disc in the drive",
  drive_loading: "The drive is reading the disc",
  probe_timeout: "The drive is still reading the disc",
  detected_cached: "The drive is still reading the disc",
  unreadable_filesystem: "A disc is loaded but could not be identified",
  being_ripped: "The disc is currently being ripped",
  device_missing: "The drive is not connected",
  starting: "Preparing the rip",
  ripping: "Reading and copying the disc",
  verifying: "Checking the completed files",
  cancelling: "Stopping the rip",
  verification_failed: "The completed files did not pass verification",
};

const $ = (selector) => document.querySelector(selector);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const isActive = (job) => ACTIVE_STATES.includes(job?.state);
const percent = (value) => (value == null ? "—" : `${Math.round(value)}%`);

function formatDuration(seconds) {
  if (seconds == null || !Number.isFinite(Number(seconds)) || Number(seconds) < 0) return "—";
  let value = Math.max(0, Math.round(Number(seconds)));
  const hours = Math.floor(value / 3600); value %= 3600;
  const minutes = Math.floor(value / 60);
  const secs = value % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  if (minutes) return `${minutes}m ${String(secs).padStart(2, "0")}s`;
  return `${secs}s`;
}

function jobElapsedSeconds(job) {
  // Active timers should visibly count between API refreshes. The node's
  // elapsed_seconds value is only a snapshot taken at its last poll.
  if (isActive(job) && job?.started_at) {
    return Math.max(0, Date.now() / 1000 - Number(job.started_at));
  }
  if (Number.isFinite(Number(job?.elapsed_seconds))) return Math.max(0, Number(job.elapsed_seconds));
  if (!job?.started_at) return null;
  return Math.max(0, Date.now() / 1000 - Number(job.started_at));
}

function etaForJob(job) {
  const progress = progressOf(job);
  const elapsed = jobElapsedSeconds(job);
  if (!isActive(job) || job?.state !== "ripping" || !elapsed || progress < 2 || progress >= 100) {
    return { elapsed, remaining: null, total: null, calculating: job?.state === "ripping" };
  }
  const total = elapsed / (progress / 100);
  const remaining = Math.max(0, total - elapsed);
  return { elapsed, remaining, total, calculating: false };
}

function currentStageProgress(job) {
  const raw = job?.progress_raw || job?.raw?.progress_raw || {};
  const current = Number(raw.current);
  const max = Number(raw.max);
  if (!Number.isFinite(current) || !Number.isFinite(max) || max <= 0 || current < 0) return null;
  return Math.max(0, Math.min(100, current / max * 100));
}

function healthSummary(job) {
  const health = job?.health || job?.raw?.health;
  if (!health) return "";
  if (health.state === "stalled") return `Possibly stalled · no progress ${formatDuration(health.seconds_since_progress)}`;
  if (health.state === "slow") return `Slow / retrying · no progress ${formatDuration(health.seconds_since_progress)}`;
  return health.label || "";
}

function ripProgressMarkup(job, compact = false) {
  if (!job) return "";
  const overall = progressOf(job);
  const stage = currentStageProgress(job);
  const eta = etaForJob(job);
  const health = healthSummary(job);
  const operation = plainMessage(job.current_operation || "Current title / file");

  const fileBar = isActive(job) && job.state === "ripping" && stage != null
    ? `<div class="rip-progress-block current-file">
         <div class="progress-caption"><span>${compact ? "Current" : esc(operation)}</span><b>${percent(stage)}</b></div>
         <div class="progress-track"><div class="progress-fill current-file-fill" style="width:${stage}%"></div></div>
       </div>`
    : "";

  let timing = "";
  if (job.state === "ripping") {
    if (eta.total != null) timing = `${formatDuration(eta.remaining)} left · ~${formatDuration(eta.total)} total`;
    else timing = "Calculating time…";
  } else if (job.state === "verifying") timing = "Checking completed files";
  else if (job.state === "complete") timing = `Finished in ${formatDuration(eta.elapsed)}`;

  const meta = [health, timing].filter(Boolean).join(" · ");
  return `<div class="rip-progress-stack ${compact ? "compact" : ""}">
    ${fileBar}
    <div class="rip-progress-block overall">
      <div class="progress-caption"><span>${compact ? "Total" : "Entire disc"}${meta ? ` · ${esc(meta)}` : ""}</span><b>${percent(overall)}</b></div>
      <div class="progress-track"><div class="progress-fill" style="width:${overall}%"></div></div>
    </div>
  </div>`;
}

function formatBytes(value) {
  if (value == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let n = value;
  while (n >= 1024 && index < units.length - 1) { n /= 1024; index += 1; }
  return `${n.toFixed(index ? 1 : 0)} ${units[index]}`;
}

const stamp = (ts) => (ts
  ? new Date(ts * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "medium" })
  : "Never");

function ago(ts) {
  if (!ts) return "No successful refresh";
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - ts));
  if (seconds < 60) return `${seconds} second${seconds === 1 ? "" : "s"} ago`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} minute${minutes === 1 ? "" : "s"} ago`;
  const hours = Math.round(minutes / 60);
  return `${hours} hour${hours === 1 ? "" : "s"} ago`;
}

function plainMessage(value) {
  if (!value) return "No additional information";
  if (REASON_LABELS[value]) return REASON_LABELS[value];
  if (/^PRG[CV]:|^MSG:/.test(value)) return "Reading and copying the disc";
  return String(value).replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(element.timer);
  element.timer = setTimeout(() => element.classList.remove("show"), 2600);
}

/* ------------------------------------------------------------------ *
 * 2. API access
 * ------------------------------------------------------------------ */

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });

  let body;
  try { body = await response.json(); } catch { body = await response.text(); }

  if (response.status === 401 && path !== "/auth/login") showLogin();
  if (!response.ok) {
    const detail = body?.detail;
    throw new Error(typeof detail === "string" ? detail
      : detail ? JSON.stringify(detail)
      : String(body || response.status));
  }
  return body;
}

/* ------------------------------------------------------------------ *
 * 3. Drive model
 * ------------------------------------------------------------------ */

/** Merge the node's live job with the metadata the manager stored for it. */
function jobFor(drive) {
  const history = State.jobs.find((job) =>
    job.node_id === drive.node_id && job.drive === drive.name);
  const live = drive.active_job;

  if (live && history && history.node_job_id === live.id) {
    return { ...history, ...live, title: history.title, year: history.year,
      season: history.season, disc: history.disc, barcode: history.barcode,
      media_type: history.media_type, creator: history.creator,
      narrator: history.narrator, raw: live };
  }
  if (live) return live;
  return history;
}

/**
 * Return the job that should control the tile.
 *
 * When the manager loses a job that was active before a node restart it marks
 * the history row as "interrupted".  The raw node payload still contains the
 * last active state, which lets us distinguish that synthetic history marker
 * from an interruption the node itself is currently reporting.  A synthetic
 * interruption must not mask the drive's new live tray/media state forever.
 * The history is deliberately retained by jobFor(), so opening intake can
 * still pre-fill the previous title and Retry remains available for genuine
 * node-reported failures.
 */
function tileJobFor(drive) {
  const job = jobFor(drive);
  const staleManagerInterruption = job?.state === "interrupted"
    && job?.raw?.state
    && job.raw.state !== "interrupted";
  if (staleManagerInterruption && !drive.active_job) return null;

  // Completion belongs to the disc that was just ripped, not permanently to
  // the physical drive. Once that disc is ejected, let the live tray/media
  // state control the tile while retaining the job in History and jobFor().
  const tray = trayOf(drive);
  const finishedJobDetached = job && !drive.active_job
    && (job.state === "complete" || job.state === "cancelled"
      || FAILED_STATES.includes(job.state));
  if (finishedJobDetached) return null;

  const completedDiscRemoved = job?.state === "complete" && !drive.active_job
    && (["open", "empty"].includes(tray)
      || (tray === "unknown" && drive.media?.present === false));
  return completedDiscRemoved ? null : job;
}

/** Tray state, tolerating an older node that cannot report one. */
function trayOf(drive) {
  return drive.tray || drive.media?.tray || (drive.media?.present ? "disc" : "unknown");
}

/**
 * Decide the tile class and label.
 * Order matters: a live job beats everything, a queued disc beats a stale
 * finished job, and the tray only decides the tile when nothing else does.
 */
function tileState(drive, job) {
  if (!drive.node_online) return ["error", "Offline"];
  if (isActive(job)) return ["ripping", STATE_LABELS[job.state] || "Ripping"];
  if (drive.pending_intake) {
    if (discIsReady(drive)) return ["disc-detected", "Disc ready — start rip"];
    return drive.pending_intake.exhausted
      ? ["error", "Could not auto-start"]
      : ["waiting", "Waiting for disc"];
  }
  if (job?.state === "complete") return ["complete", "Complete"];
  if (FAILED_STATES.includes(job?.state)) return ["error", STATE_LABELS[job.state]];

  const tray = trayOf(drive);
  if (tray === "open") return ["tray-open", "Tray open"];
  if (tray === "loading") return ["disc-detected", "Reading disc"];
  if (tray === "disc" || drive.media?.present) return ["disc-detected", "Disc detected"];
  if (drive.exists === false) return ["error", "Not connected"];
  return ["empty", "Empty"];
}

function tileTitle(drive, job) {
  if (drive.pending_intake) return drive.pending_intake.title;
  if (job?.title) return job.title;
  if (job?.raw?.output_dir) return job.raw.output_dir.split("/").pop();
  if (job?.output_dir) return job.output_dir.split("/").pop();
  if (drive.media?.present) return drive.media.label || "Unassigned disc";
  return "";
}

function describeMedia(item) {
  const bits = [];
  if (item.creator) bits.push(item.creator);
  if (item.year) bits.push(item.year);
  if (item.season != null) bits.push(`Season ${item.season}`);
  if (item.disc != null) bits.push(`Disc ${item.disc}`);
  return bits.join(" · ");
}

function tileSubtitle(drive, job) {
  if (drive.pending_intake) {
    return describeMedia(drive.pending_intake) || "Insert the disc to begin";
  }
  if (job) {
    return describeMedia(job)
      || plainMessage(job.last_message || job.current_operation)
      || drive.node_name
      || drive.node_id;
  }
  return drive.node_name || drive.node_id;
}

function progressOf(job) {
  if (job?.state === "complete") return 100;
  return Math.max(0, Math.min(100, Number(job?.progress) || 0));
}

/* ------------------------------------------------------------------ *
 * 4. The tile grid
 * ------------------------------------------------------------------ */

function tileMarkup(drive, index) {
  const job = tileJobFor(drive);
  const [className, label] = tileState(drive, job);
  const showBar = isActive(job) || job?.state === "complete";
  const value = progressOf(job);

  const footer = showBar ? ripProgressMarkup(job, true) : "";

  return `<button class="drive ${className}${showBar ? " has-rip-progress" : ""}" data-index="${index}">
    <div class="drive-id">${esc(drive.name)}</div>
    <div class="drive-state-rail"><div class="drive-state">${esc(label)}</div></div>
    <div class="drive-main">
      <div class="drive-title">${esc(tileTitle(drive, job))}</div>
      <div class="drive-sub">${esc(tileSubtitle(drive, job))}</div>
    </div>
    <div class="drive-footer">${footer}</div>
  </button>`;
}

function dashboardDriveKey(drive) {
  return drive.manager_drive_id || `${drive.node_id}:${String(drive.name || "").toUpperCase()}`;
}
function configuredDashboardCells() {
  const settings = State.settings || {};
  const columns = Math.max(1, Math.min(6, Number(settings.dashboard_columns || 3)));
  const rows = Math.max(1, Math.min(8, Number(settings.dashboard_rows || 2)));
  const count = columns * rows;
  const saved = Array.isArray(settings.dashboard_tiles) ? settings.dashboard_tiles.slice(0,count) : [];
  if (!saved.some(Boolean)) {
    State.drives.slice(0,count).forEach((drive,index)=>{ saved[index]=dashboardDriveKey(drive); });
  }
  while (saved.length<count) saved.push(null);
  return {columns,rows,cells:saved};
}
function missingDriveTile(key,index) {
  const bare=String(key||"").split(":").pop()||"Drive";
  return `<div class="drive missing-layout-drive" data-layout-index="${index}">
    <div class="drive-id">${esc(bare)}</div>
    <div class="drive-state-rail"><div class="drive-state">MISSING</div></div>
    <div class="drive-main"><div class="drive-title">Drive unavailable</div><div class="drive-sub">This configured drive is not currently reported by its Rip Node.</div></div>
    <div class="drive-footer"></div></div>`;
}
const emptyDashboardTile=(index)=>`<div class="drive dashboard-placeholder" data-layout-index="${index}" aria-hidden="true"></div>`;
function renderGrid() {
  const grid=$("#driveGrid");
  const layout=configuredDashboardCells();
  grid.style.setProperty("--dashboard-cols",layout.columns);
  grid.style.setProperty("--dashboard-rows",layout.rows);
  const spacingPercent=Math.max(50,Math.min(300,Number(State.settings?.dashboard_spacing_percent || 100)));
  grid.style.setProperty("--dashboard-gap-scale",String(spacingPercent/100));
  const byKey=new Map(State.drives.map(d=>[dashboardDriveKey(d),d]));
  const markup=layout.cells.map((key,cellIndex)=>{
    if(!key)return emptyDashboardTile(cellIndex);
    const drive=byKey.get(key);
    if(!drive)return missingDriveTile(key,cellIndex);
    return tileMarkup(drive,State.drives.indexOf(drive));
  }).join("");
  const signature=`${layout.columns}x${layout.rows}:${markup}`;
  if(signature===State.gridSignature)return;
  State.gridSignature=signature;
  grid.innerHTML=markup;
  grid.querySelectorAll("button.drive[data-index]").forEach(button=>{
    button.onclick=()=>openDrive(State.drives[Number(button.dataset.index)]);
  });
}

/* ------------------------------------------------------------------ *
 * 5. Intake
 * ------------------------------------------------------------------ */

function openModal(title, html) {
  $("#genericModalTitle").textContent = title;
  $("#genericModalBody").innerHTML = html;
  $("#genericModal").classList.remove("hidden");
}

const closeModal = (id) => $("#" + id).classList.add("hidden");

const detailRow = (key, value) =>
  `<div class="detail"><div class="k">${esc(key)}</div><div class="v">${esc(value ?? "—")}</div></div>`;

function currentDrive() {
  return State.drives.find((d) =>
    d.node_id === State.intake.nodeId && d.name === State.intake.drive);
}

function driveCapabilityAllowed(drive, capability) {
  const id = drive.manager_drive_id || `${drive.node_id}:${String(drive.name).toUpperCase()}`;
  return State.settings?.drive_preferences?.[id]?.[capability] !== false;
}

const openTrayAllowed = (drive) => driveCapabilityAllowed(drive, "open_tray");
const closeTrayAllowed = (drive) => driveCapabilityAllowed(drive, "close_tray");

/** The fixed bottom-left tray action, matching whatever the tray is doing. */
function trayButtonMarkup(drive, disabled = false) {
  const tray = trayOf(drive);
  const disabledAttr = disabled ? " disabled" : "";
  if (tray === "open") {
    if (!closeTrayAllowed(drive)) return "";
    return `<button class="drive-popup-btn warning-action" type="button" data-tray="close"${disabledAttr}>Close tray</button>`;
  }
  if (!openTrayAllowed(drive)) return "";
  if (tray === "disc" || tray === "loading" || drive.media?.present) {
    return `<button class="drive-popup-btn warning-action" type="button" data-tray="eject"${disabledAttr}>${disabled ? "Eject unavailable" : "Eject disc"}</button>`;
  }
  return `<button class="drive-popup-btn warning-action" type="button" data-tray="eject"${disabledAttr}>Open tray</button>`;
}

function discIsReady(drive) {
  const tray = trayOf(drive);
  if (tray === "unknown") return Boolean(drive.media?.present);
  return tray === "disc";
}

function renderIntakeActions() {
  const drive = currentDrive();
  if (!drive) return;

  const ready = discIsReady(drive);
  const primary = ready
    ? `<button class="drive-popup-btn primary-action popup-primary" type="submit">Start rip</button>`
    : `<button class="drive-popup-btn primary-action popup-primary" type="submit">${drive.pending_intake ? "Save details & continue waiting" : "Wait for disc"}</button>`;
  const third = drive.pending_intake
    ? `<button class="drive-popup-btn danger-action" type="button" data-action="stop-waiting">Stop waiting</button>`
    : `<button class="drive-popup-btn dismiss-action" type="button" data-action="close-intake">Close</button>`;

  $("#intakeActions").innerHTML = `${primary}<div class="popup-secondary-actions">${trayButtonMarkup(drive)}${third}</div>`;

  $("#intakeNote").textContent = ready
    ? "A disc is loaded. Enter the details and start the rip."
    : drive.pending_intake
      ? "This drive is already waiting for a disc. Saving replaces the stored details."
      : "No disc yet. Enter the details and the rip starts on its own once one is loaded.";
}

function renderBarcodeActions() {
  const drive = currentDrive();
  if (!drive) return;
  const close = `<button class="drive-popup-btn dismiss-action" type="button" data-action="close-intake">Close</button>`;
  $("#barcodeActions").innerHTML = `<div class="popup-secondary-actions">${trayButtonMarkup(drive)}${close}</div>`;
}

function showBarcodeStep() {
  $("#upcArea").classList.remove("hidden");
  $("#ripForm").classList.add("hidden");
  $("#lookupStatus").textContent = "Ready to scan";
  $("#upcInput").value = State.intake.barcode || "";
  renderBarcodeActions();
  setTimeout(focusScanner, 80);
}

function showDetailsStep() {
  $("#upcArea").classList.add("hidden");
  $("#ripForm").classList.remove("hidden");
  renderIntakeActions();
}

/** Keep the barcode field focused for a USB/Bluetooth scanner without opening
 *  the on-screen keyboard on a phone. */
function focusScanner() {
  const input = $("#upcInput");
  if (!input) return;
  input.setAttribute("readonly", "readonly");
  try { input.focus({ preventScroll: true }); } catch { input.focus(); }
  setTimeout(() => input.removeAttribute("readonly"), 60);
}

function fillDetails(media) {
  $("#mediaTitle").value = media.title || "";
  $("#mediaYear").value = media.year || "";
  $("#mediaType").value = media.media_type || (media.season != null ? "tv" : "movie");
  $("#mediaSeason").value = media.season ?? 1;
  $("#mediaDisc").value = media.disc ?? 1;
  $("#audioDisc").value = media.disc ?? 1;
  $("#movieDisc").value = media.media_type === "movie" ? (media.disc ?? "") : "";
  $("#mediaCreator").value = media.creator || "";
  $("#mediaNarrator").value = media.narrator || "";
  toggleTvFields();
  updateFolderPreview();
}

function openIntake(drive) {
  if (!drive) return;
  State.intake = { nodeId: drive.node_id, drive: drive.name, barcode: "" };
  $("#nodeId").value = drive.node_id;
  $("#driveName").value = drive.name;
  $("#intakeHeading").textContent = `${drive.name} · ${drive.node_name || drive.node_id}`;

  const pending = drive.pending_intake;
  const previous = pending || jobFor(drive);
  State.intake.barcode = pending?.barcode || previous?.barcode || "";

  // The filesystem volume label belongs to the physical disc.  It is useful
  // diagnostic information, but it is often machine-formatted and must never
  // silently become the movie/series title or output folder name.
  const discLabel = String(drive.media?.label || "").trim();
  const discLabelInfo = $("#discLabelInfo");
  discLabelInfo.textContent = discLabel ? `Disc label: ${discLabel}` : "";
  discLabelInfo.classList.toggle("hidden", !discLabel);

  if (pending) {
    fillDetails(pending);
  } else {
    fillDetails({ title: "", media_type: "movie" });
  }

  $("#intakeModal").classList.remove("hidden");

  // Straight to the details when we already know what the disc is.
  if (pending || !State.settings?.upc_lookup) {
    showDetailsStep();
    setTimeout(() => $("#mediaTitle").focus({ preventScroll: true }), 50);
  } else {
    showBarcodeStep();
  }
}

function toggleTvFields() {
  const type = $("#mediaType").value;
  const isAudio = type === "music" || type === "audiobook";
  $("#tvFields").classList.toggle("hidden", type !== "tv");
  $("#movieDiscField").classList.toggle("hidden", type !== "movie");
  $("#audioFields").classList.toggle("hidden", !isAudio);
  $("#narratorField").classList.toggle("hidden", type !== "audiobook");
  $("#mediaTitleLabel").textContent = type === "music" ? "Album" : type === "audiobook" ? "Book title" : "Title";
  $("#mediaCreatorLabel").textContent = type === "music" ? "Album artist" : "Author";
  $("#mediaTitle").placeholder = type === "music" ? "Album title" : type === "audiobook" ? "Book title" : "The Italian Job";
}

function updateFolderPreview() {
  const type = $("#mediaType").value;
  let title = $("#mediaTitle").value || (type === "music" ? "Album" : type === "audiobook" ? "Book" : "Title");
  const year = $("#mediaYear").value;
  if (year) title += ` (${year})`;
  const movieDisc = $("#movieDisc").value;
  let preview = type === "tv" ? `TV / ${title}` : type === "movie" ? `Movies / ${title}${movieDisc ? ` / Disk ${movieDisc}` : ""}` : "";
  if (type === "tv") {
    preview += ` / Season ${$("#mediaSeason").value} / Disk ${$("#mediaDisc").value}`;
  } else if (type === "music" || type === "audiobook") {
    const root = type === "music" ? "Music" : "AudioBooks";
    const creator = $("#mediaCreator").value || (type === "music" ? "Album Artist" : "Author");
    const narrator = type === "audiobook" && $("#mediaNarrator").value
      ? ` {${$("#mediaNarrator").value}}` : "";
    preview = `${root} / ${creator} / ${title}${narrator} / Disc ${$("#audioDisc").value}`;
  }
  $("#folderPreview").textContent = preview;
}

function intakeBody() {
  const body = { title: $("#mediaTitle").value.trim() };
  const year = $("#mediaYear").value;
  if (year) body.year = Number(year);
  body.media_type = $("#mediaType").value;
  if (body.media_type === "tv") {
    body.season = Number($("#mediaSeason").value);
    body.disc = Number($("#mediaDisc").value);
  }
  if (body.media_type === "movie" && $("#movieDisc").value) body.disc = Number($("#movieDisc").value);
  if (body.media_type === "music" || body.media_type === "audiobook") {
    body.creator = $("#mediaCreator").value.trim();
    body.disc = Number($("#audioDisc").value);
    if (body.media_type === "audiobook" && $("#mediaNarrator").value.trim()) {
      body.narrator = $("#mediaNarrator").value.trim();
    }
  }
  if (State.intake.barcode) body.barcode = State.intake.barcode;
  return body;
}


function showBarcodeMatches(matches) {
  const rows = matches.map((match, index) => `<button class="barcode-match" type="button" data-barcode-match="${index}">
    <span><strong>${esc(match.title || match.raw_title || "Untitled")}</strong>
      <small>${esc(match.media_type || "")}${match.year ? ` · ${esc(match.year)}` : ""}${match.season ? ` · Season ${esc(match.season)}` : ""}${match.creator ? ` · ${esc(match.creator)}` : ""}</small>
    </span>
    <span class="barcode-match-source">${esc(match.source || "")}<small>${esc(match.confidence || "")}%</small></span>
  </button>`).join("");
  $("#genericModalTitle").textContent = "Choose the closest match";
  $("#genericModalBody").innerHTML = `<div class="note">Choose the closest result. Nothing starts automatically — every field remains editable before Start Rip.</div>
    <div class="barcode-match-list">${rows}</div>
    <button class="secondary full-button" type="button" id="barcodeManual">None of these — enter manually</button>`;
  $("#genericModal").classList.remove("hidden");
  $("#genericModalBody").querySelectorAll("[data-barcode-match]").forEach((button) => {
    button.onclick = () => {
      const match = matches[Number(button.dataset.barcodeMatch)];
      fillDetails(match);
      closeModal("genericModal");
      showDetailsStep();
      setTimeout(() => $("#mediaTitle").focus({ preventScroll: true }), 60);
    };
  });
  $("#barcodeManual").onclick = () => {
    closeModal("genericModal");
    fillDetails({ title: "", media_type: "movie" });
    showDetailsStep();
    setTimeout(() => $("#mediaTitle").focus({ preventScroll: true }), 60);
  };
}

async function lookupBarcode() {
  const code = $("#upcInput").value.trim();
  if (!code) return;
  State.intake.barcode = code;
  $("#upcInput").value = "";
  $("#lookupStatus").textContent = "Searching the barcode database…";

  try {
    const result = await api(`/lookup/upc/${encodeURIComponent(code)}`);
    if (result.found && Array.isArray(result.matches) && result.matches.length) {
      showBarcodeMatches(result.matches);
      $("#lookupStatus").textContent = `${result.matches.length} possible match${result.matches.length === 1 ? "" : "es"} found`;
      return;
    }
    if (result.found) {
      fillDetails(result);
      $("#lookupStatus").textContent = "Found";
      showDetailsStep();
      return;
    }
    $("#lookupStatus").textContent = result.error || "No match. Enter the details by hand.";
  } catch (error) {
    $("#lookupStatus").textContent = error.message;
  }

  fillDetails({ title: "", media_type: "movie" });
  showDetailsStep();
  setTimeout(() => $("#mediaTitle").focus({ preventScroll: true }), 60);
}

/* ------------------------------------------------------------------ *
 * 6. Drive actions
 * ------------------------------------------------------------------ */

const drivePath = (nodeId, drive, action) =>
  `/nodes/${encodeURIComponent(nodeId)}/drives/${encodeURIComponent(drive)}/${action}`;

async function runAction(nodeId, drive, action, options = {}) {
  try {
    const result = await api(drivePath(nodeId, drive, action), {
      method: options.method || "POST",
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
    toast(result?.message || options.success || `${drive} ${action}`);
    closeModal("genericModal");
    if (options.closeIntake) closeModal("intakeModal");
    await refresh();
    return result;
  } catch (error) {
    toast(error.message);
    return null;
  }
}

async function ejectDrive(nodeId, drive) {
  if (State.settings?.confirm_eject && !confirm(`Eject ${drive}?`)) return;
  await runAction(nodeId, drive, "eject", { success: `${drive} ejected`, closeIntake: true });
}

async function closeTray(nodeId, drive) {
  await runAction(nodeId, drive, "close", { success: `${drive} tray closing` });
}

async function cancelRip(nodeId, drive) {
  if (!confirm(`Cancel the rip in ${drive}?`)) return;
  await runAction(nodeId, drive, "cancel", { success: `${drive} cancelling` });
}

async function retryRip(nodeId, drive) {
  await runAction(nodeId, drive, "retry", { success: `${drive} retrying` });
}

async function stopWaiting(nodeId, drive) {
  await runAction(nodeId, drive, "intake", {
    method: "DELETE", success: `${drive} is no longer waiting`, closeIntake: true,
  });
}

async function clearDrive(nodeId, drive) {
  if (!confirm(`Clear the failed rip from ${drive}?`)) return;
  await runAction(nodeId, drive, "clear", { success: `${drive} cleared` });
}

async function submitIntake(event) {
  event.preventDefault();
  const { nodeId, drive } = State.intake;
  const body = intakeBody();
  if (!body.title) { $("#mediaTitle").focus(); return; }
  if (["music", "audiobook"].includes(body.media_type) && !body.creator) {
    $("#mediaCreator").focus();
    toast(body.media_type === "music" ? "Enter the album artist" : "Enter the author");
    return;
  }

  try {
    const result = await api(drivePath(nodeId, drive, "intake"), {
      method: "POST", body: JSON.stringify(body),
    });
    closeModal("intakeModal");
    toast(result.message || `${drive} queued`);
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}

/* ------------------------------------------------------------------ *
 * Drive details popup
 * ------------------------------------------------------------------ */

function openDrive(drive) {
  if (!drive) return;
  State.openDriveKey = { nodeId: drive.node_id, name: drive.name };
  const job = tileJobFor(drive);
  const [className, label] = tileState(drive, job);

  // An idle drive goes straight to intake; that is the common case.
  if (["empty", "disc-detected", "tray-open"].includes(className) && !drive.pending_intake) {
    openIntake(drive);
    return;
  }

  const media = drive.media || {};
  const details =
    detailRow("State", label) +
    detailRow("Type", job?.media_type || job?.media?.media_type || "—") +
    detailRow("Node", drive.node_name || drive.node_id) +
    detailRow("Tray", plainMessage(trayOf(drive))) +
    detailRow("Device", media.device || drive.device) +
    detailRow("Disc", media.label) +
    detailRow("Title", tileTitle(drive, job) || "—") +
    detailRow("Artist / Author", job?.creator || job?.media?.creator) +
    detailRow("Narrator", job?.narrator || job?.media?.narrator) +
    detailRow("Barcode", drive.pending_intake?.barcode || job?.barcode) +
    detailRow("Rip health", isActive(job) ? (healthSummary(job) || "Active") : "—") +
    detailRow("Elapsed", isActive(job) ? formatDuration(jobElapsedSeconds(job)) : "—") +
    detailRow("Estimated remaining", isActive(job) && etaForJob(job).remaining != null ? `~${formatDuration(etaForJob(job).remaining)}` : isActive(job) && job?.state === "ripping" ? "Calculating…" : "—") +
    detailRow("Estimated total", isActive(job) && etaForJob(job).total != null ? `~${formatDuration(etaForJob(job).total)}` : isActive(job) && job?.state === "ripping" ? "Calculating…" : "—") +
    detailRow("Latest update",
      plainMessage(drive.pending_intake?.last_error || job?.last_message
        || job?.current_operation || media.reason));

  const button = (action, text, style = "", disabled = false) =>
    `<button class="drive-popup-btn ${style}" type="button" data-drive-action="${action}"${disabled ? " disabled" : ""}>${text}</button>`;
  const trayButton = (disabled = false) => button(
    "tray",
    disabled ? "Eject unavailable" : trayOf(drive) === "open" ? "Close tray" : "Eject disc",
    "warning-action",
    disabled,
  );
  const visibleTrayButton = (disabled = false) =>
    trayOf(drive) === "open" && !closeTrayAllowed(drive) ? "" : trayButton(disabled);
  const closeButton = () => button("close", "Close", "dismiss-action");

  let primary = "";
  let secondary = "";
  if (isActive(job)) {
    primary = button("cancel", "Cancel rip", "danger-action popup-primary");
    secondary = visibleTrayButton(true) + closeButton();
  } else if (drive.pending_intake) {
    primary = button("intake", "Edit details", "primary-action popup-primary");
    secondary = visibleTrayButton() + button("stop-waiting", "Stop waiting", "danger-action");
  } else if (FAILED_STATES.includes(job?.state) || job?.state === "cancelled") {
    primary = button("retry", "Retry rip", "primary-action popup-primary");
    secondary = visibleTrayButton() + button("clear", "Clear drive", "danger-action");
  } else if (job?.state === "complete") {
    primary = button("intake", "Rip another disc", "primary-action popup-primary");
    secondary = visibleTrayButton() + closeButton();
  } else {
    primary = button("intake", "Enter details & start rip", "primary-action popup-primary");
    secondary = visibleTrayButton() + closeButton();
  }

  const progressPanel = (isActive(job) || job?.state === "complete")
    ? `<div class="drive-rip-progress">${ripProgressMarkup(job, false)}</div>`
    : "";

  openModal(`${drive.name} · ${label}`,
    `${progressPanel}<div class="drive-detail-grid">${details}</div>
     <div class="drive-popup-actions">${primary}<div class="popup-secondary-actions">${secondary}</div></div>`);

  $("#genericModalBody").querySelectorAll("[data-drive-action]").forEach((element) => {
    element.onclick = () => {
      const action = element.dataset.driveAction;
      const { node_id: nodeId, name } = drive;
      if (action === "cancel") return cancelRip(nodeId, name);
      if (action === "retry") return retryRip(nodeId, name);
      if (action === "clear") return clearDrive(nodeId, name);
      if (action === "eject") return ejectDrive(nodeId, name);
      if (action === "stop-waiting") return stopWaiting(nodeId, name);
      if (action === "close") { closeModal("genericModal"); return undefined; }
      if (action === "tray") {
        return trayOf(drive) === "open" ? closeTray(nodeId, name) : ejectDrive(nodeId, name);
      }
      closeModal("genericModal");
      openIntake(drive);
      return undefined;
    };
  });
}

/* ------------------------------------------------------------------ *
 * 7. Shares and stats
 * ------------------------------------------------------------------ */

function nodeShare(node) {
  try {
    const host = new URL(node.url).hostname;
    return { unc: `\\\\${host}\\Rips`, link: `smb://${host}/Rips` };
  } catch {
    return { unc: node.url, link: node.url };
  }
}

async function copyShare(value) {
  try {
    await navigator.clipboard.writeText(value);
    toast("Share path copied");
  } catch {
    prompt("Copy this share path:", value);
  }
}

function showShares() {
  const cards = State.nodes.length
    ? State.nodes.map((node) => {
      const share = nodeShare(node);
      return `<div class="share-card">
        <div class="node-top">
          <strong>${esc(node.name)}</strong>
          <span class="status ${node.online ? "" : "offline"}">● ${node.online ? "ONLINE" : "OFFLINE"}</span>
        </div>
        <div class="share-path">${esc(share.unc)}</div>
        <div class="share-actions">
          <a class="primary" href="${esc(share.link)}">Open share</a>
          <button class="secondary" type="button" data-copy="${esc(share.unc)}">Copy path</button>
        </div>
      </div>`;
    }).join("")
    : `<div class="note">No nodes configured.</div>`;

  openModal("Output Shares",
    `<div class="shares">${cards}</div>
     <div class="note share-note">If Chrome blocks SMB links, use Copy path and paste it into File Explorer.</div>`);

  $("#genericModalBody").querySelectorAll("[data-copy]").forEach((button) => {
    button.onclick = () => copyShare(button.dataset.copy);
  });
}

const relativeSpan = (ts) => `<span class="live-ago" data-ts="${ts || 0}">${esc(ago(ts))}</span>`;

function updateRelativeTimes() {
  document.querySelectorAll(".live-ago").forEach((element) => {
    element.textContent = ago(Number(element.dataset.ts));
  });
}

const kpi = (key, value) => `<div class="kpi"><div class="k">${key}</div><div class="v">${value}</div></div>`;

function opticalDriveRows(drives) {
  if (!drives?.length) return "";
  const rows = drives.map((drive) => {
    let value = "Idle";
    if (["starting", "ripping"].includes(drive.state)) {
      const mbps = Number(drive.megabits_per_second);
      const megabytes = Number.isFinite(mbps) ? mbps / 8 : null;
      value = drive.speed_x == null
        ? "Measuring…"
        : `${Number(drive.speed_x).toFixed(1)}× · ${megabytes == null ? "—" : megabytes.toFixed(1)} MB/s · ${Number(drive.megabits_per_second).toFixed(1)} Mbps`;
    } else if (drive.state === "verifying") {
      value = "Verifying";
    } else if (drive.state === "cancelling") {
      value = "Stopping";
    }
    const detail = drive.engine === "makemkv" ? "MakeMKV · DVD equivalent"
      : drive.engine ? "abcde · CD equivalent" : "Optical drive";
    return `<div class="drive-stat">
      <strong>${esc(drive.name)}</strong><span>${esc(detail)}</span><b>${esc(value)}</b>
    </div>`;
  }).join("");
  return `<div class="stats-subtitle">Optical drive read speeds</div><div class="drive-list">${rows}</div>`;
}

function showStats() {
  const manager = `<div class="manager-refresh">
    <div><span>RIP MANAGER LAST REFRESH</span><strong>${esc(stamp(State.refreshedAt))}</strong></div>
    <b>${relativeSpan(State.refreshedAt)}</b>
  </div>`;

  const cards = State.stats.length
    ? State.stats.map((entry) => {
      const stats = entry.stats || {};
      const cpu = stats.cpu || {};
      const memory = stats.memory || {};
      const disk = stats.disks?.ripping || {};
      return `<div class="node-card">
        <div class="node-top">
          <div class="node-name">${esc(entry.node_name)}</div>
          <div class="status ${entry.online ? "" : "offline"}">● ${entry.online ? "ONLINE" : "OFFLINE"}</div>
        </div>
        <div class="node-refresh">
          <span>Last refreshed</span><strong>${esc(stamp(entry.fetched_at))}</strong>
          <small>${relativeSpan(entry.fetched_at)}</small>
        </div>
        <div class="kpis">
          ${kpi("CPU", percent(cpu.usage_percent))}
          ${kpi("RAM", percent(memory.percent))}
          ${kpi("RIPS DISK", percent(disk.percent))}
          ${kpi("FREE SPACE", formatBytes(disk.free))}
          ${kpi("CLOCK", cpu.frequency_mhz?.current ? `${Math.round(cpu.frequency_mhz.current)} MHz` : "—")}
          ${kpi("PROCESSES", stats.process_count ?? "—")}
        </div>
        ${opticalDriveRows(stats.optical_drives)}
      </div>`;
    }).join("")
    : `<div class="note">No statistics available.</div>`;

  openModal("System Stats", `${manager}<div class="stats-grid">${cards}</div>`);
  updateRelativeTimes();
}

/* ------------------------------------------------------------------ *
 * 8. Settings
 * ------------------------------------------------------------------ */

const saveBar = () => `<div class="savebar sticky-savebar">
  <button class="secondary" type="button" data-settings-action="cancel">Cancel</button>
  <button class="primary" type="button" data-settings-action="save" ${settingsDirty() ? "" : "disabled"}>Save changes</button>
</div>`;

const backBar = () => "";

const SETTINGS_HELP = {
  "verify-rips": {title:"Verify completed rips",does:"Checks the files made by the node before the job is marked as successful.",use:"Keep this on for normal use. It catches an incomplete or unreadable result before the disc is ejected.",recommended:"On.",effect:"A completed rip briefly shows as verifying. A failed check leaves the disc available so the job can be investigated or tried again.",undo:"Turn it off and save if you need the quickest possible test workflow."},
  "auto-eject": {title:"Auto-eject after a rip",does:"Opens the optical-drive tray after a rip and its verification finish successfully.",use:"Use this when you want the drive ready for the next disc without pressing Eject.",recommended:"On for unattended or multi-drive ripping.",effect:"The disc stays in the drive if the rip fails. The simulator follows the same behaviour and waits three seconds before ejecting.",undo:"Turn it off and save; completed discs will remain in their drives."},
  "video-defaults": {title:"Video defaults",does:"Controls which video titles and language tracks the node prefers when reading a DVD or Blu-ray.",use:"Raise the minimum length to ignore trailers and short extras. English preferences guide track selection but do not rename the disc.",recommended:"Keep the minimum low until you know your discs; 2 minutes is the safe default.",effect:"These defaults are sent with new rips. Existing or running jobs are not changed.",undo:"Restore the previous values and save."},
  "dashboard-layout": {title:"Dashboard layout",does:"Sets the number of drive tiles, their positions and the gaps between them.",use:"Assign each real or simulated drive to the position where you want it to stay.",recommended:"Use only as many cells as you need. Leave a cell Empty when you deliberately want a gap.",effect:"A drive can appear in one cell only. Missing drives keep their saved position so the layout does not shuffle.",undo:"Change the grid or assignments again, or press Cancel before saving."},
  "node-connection": {title:"Node connection",does:"Tells Rip Manager where a real Rip Node API is and whether it should be contacted.",use:"The URL must be reachable from the Manager container, not only from your phone or laptop.",recommended:"Use the node's fixed LAN address, for example http://192.168.1.50:8000.",effect:"Disabling a node stops polling and control but does not uninstall or erase that node.",problems:"If it stays offline, check the IP, port, Node API service and firewall.",undo:"Re-enable it or restore the previous URL and save."},
  "drive-mapping": {title:"Drive mapping",does:"Links stable names such as DVD1 to the physical optical drives detected by a node.",use:"Use Auto-assign for the cleanest normal setup. Use manual mapping only when a drive must keep a particular name or position.",recommended:"Auto-assign detected drives, then check the names before starting a rip.",effect:"Persistent USB or udev paths are saved where available, so Linux device-number changes should not rearrange the dashboard.",problems:"Mapping changes are blocked while an affected drive is ripping.",undo:"Assign the drive again, remove the logical slot, or run Auto-assign."},
  "simulator": {title:"Built-in Simulator",does:"Adds a safe fake node with two Blu-ray drives and four DVD drives for demonstrations and training.",use:"Enable it when showing the Manager without real node hardware. Disable it for normal operation if you do not want fake drives on the dashboard.",recommended:"Off for normal use; on only for demonstrations, training or testing.",effect:"It never touches physical drives, a NAS or another computer. Reset returns every fake drive and job to its starting state.",undo:"Disable it. Any simulator dashboard positions are cleared automatically."},
  "barcode": {title:"Barcode lookup",does:"Uses a real scanned UPC, EAN or ISBN to find editable title information before a rip starts.",use:"Scan into the barcode box from the main screen. You may also type a code. Choose the closest result or skip lookup.",recommended:"On when an internet connection is available.",effect:"The lookup does not start a rip automatically and the returned fields can always be corrected.",problems:"Results depend on the enabled providers and their limits or API keys.",undo:"Turn lookup off and save; manual title entry still works."},
  "metadata-providers": {title:"Metadata sources",does:"Chooses which online catalogues are searched after a barcode is scanned.",use:"MusicBrainz is for music, Google Books for books, UPCitemdb for retail UPC data, and OMDb improves movie or TV titles and years.",recommended:"Enable only the sources you use. Test a provider after adding a key.",effect:"More sources can improve matches but may make lookup slightly slower.",problems:"Some providers require an account or API key and may have daily limits.",undo:"Disable the provider and save; stored rip titles are not changed."},
  "api-keys": {title:"API keys",does:"Allows Rip Manager to use an online metadata provider that requires authentication.",use:"Copy the key from your own provider account and paste it here. The key is not your Rip Manager PIN.",recommended:"Leave an optional key blank unless you have one. Never share keys in screenshots.",effect:"The key is stored with Manager settings and used only for that provider's lookup requests.",problems:"An expired, incorrect or rate-limited key causes provider tests or lookups to fail.",undo:"Clear the field and save, or disable the provider."},
  "existing-node": {title:"Connect an existing Node API",does:"Adds a node where the Rip Node software is already installed and running.",use:"Use this instead of the Ubuntu installer when the API already answers on its port.",recommended:"Test the connection before adding it. Give each node a unique ID.",effect:"No SSH changes are made. Manager stores the API address and optional API token.",problems:"The address must work from inside the Manager container.",undo:"Remove or disable the node under Hardware."},
  "fresh-install": {title:"Install on fresh Ubuntu",does:"Uses SSH once to install the bundled Rip Node software, its services and optional NAS storage, then adds it to Manager.",use:"Use this on a clean supported Ubuntu machine or VM intended to become a Rip Node.",recommended:"Give the machine a fixed LAN address first. Test SSH and sudo before installing.",effect:"System packages and Rip Node services are installed on the selected Ubuntu host. SSH and NAS passwords are not saved by Manager.",problems:"Do not point this at Byte-Me or an unrelated computer. The account must be allowed to use sudo.",undo:"Remove the node from Manager and uninstall its services on Ubuntu if you no longer need it."},
  "nas-mount": {title:"NAS storage",does:"Mounts an SMB share on the new node so completed rips can be written directly to your NAS.",use:"Enter the share as //SERVER/SHARE and choose the local output path, normally /mnt/ripping.",recommended:"Use a dedicated NAS account with access only to the ripping share.",effect:"The installer creates the mount and stores the NAS credentials on the node, not in Rip Manager.",problems:"The node must be able to reach the NAS and the share name, username and password must be correct.",undo:"Remove or change the mount on the node."},
  "network-addresses": {title:"Manager and node addresses",does:"Sets the address Manager uses to control the node and the address the node uses to download future bundled updates from Manager.",use:"Use fixed LAN addresses when the machines are separate. Addresses must be reachable from the service using them.",recommended:"Node URL: http://NODE-IP:8000. Manager URL: your Manager LAN URL and published port.",effect:"A wrong address can make control or updates appear offline even when both computers are running.",undo:"Correct the saved node URL under Hardware; rerun the node updater installer if its Manager address is wrong."},
  "polling": {title:"Dashboard polling",does:"Controls how often Manager asks nodes for fresh drive state, progress and statistics.",use:"Active polling is used while a rip is running; idle polling is used at other times.",recommended:"Idle 5 seconds and active 2 seconds.",effect:"Lower numbers feel faster but create more network, node and database activity.",problems:"Very low values can make slow nodes less reliable and do not make the optical drive rip faster.",undo:"Restore 5 seconds idle and 2 seconds active."},
  "api-tools": {title:"API tools",does:"Opens the technical API documentation and lets developers test Manager endpoints directly.",use:"Use this only for diagnostics, integration work or support instructions.",recommended:"Most users do not need it.",effect:"Some actions can control drives or change data, just like the normal interface.",problems:"Do not run an endpoint unless you understand what it changes.",undo:"Close the API page; opening it alone changes nothing."},
  "pin-lock": {title:"PIN protection",does:"Requires a 4–8 digit PIN before people can use Manager controls or its protected API.",use:"Enable it when the Manager page is accessible to people who should not operate drives or change settings.",recommended:"Choose a PIN that is not easy to guess and keep it somewhere safe.",effect:"Changing the PIN signs out existing sessions. The lock screen accepts its keypad, a physical keyboard or direct scanner text.",problems:"You must enter the current PIN before replacing an existing PIN.",undo:"Turn Lock Rip Manager off and save while you are signed in."},
  "updates": {title:"Manager and node updates",does:"Checks the configured GitHub release and queues a Manager or bundled Node update only when you press Install.",use:"Use Check now first, then install the Manager update before pushing a newer bundled Node version.",recommended:"Do not update during an active rip. Read the included README implementation notes first.",effect:"Manager briefly restarts. Node updates are delivered by Manager; the simulator is built in and is not updated separately.",problems:"The persistent host updater must be running for a queued Manager request to install.",undo:"Use Rollback for Manager code. Node rollback is not automatic."},
  "rollback": {title:"Rollback",does:"Restores an earlier backed-up version of Manager application code.",use:"Use it if a Manager update starts but the new interface or service does not work correctly.",recommended:"Choose the immediately previous version first.",effect:"Settings, PIN, nodes, database and job history are preserved. Manager briefly goes offline and checks its own health after restoration.",problems:"Rollback needs the current persistent Unraid updater and at least one verified backup.",undo:"Install the newer release again from Updates."}
};

const helpButton = (topic, label="More information") => `<button type="button" class="info-button" data-help-topic="${esc(topic)}" aria-label="${esc(label)}" title="${esc(label)}"><span aria-hidden="true">ⓘ</span></button>`;
const groupHeading = (title, topic=null) => `<div class="settings-group-heading"><h3>${esc(title)}</h3>${topic?helpButton(topic,`More information about ${title}`):""}</div>`;

function openSettingsHelp(topic) {
  const help=SETTINGS_HELP[topic]; if(!help)return;
  const rows=[["What it does",help.does],["When to use it",help.use],["Recommended setting",help.recommended],["What it affects",help.effect],["If something goes wrong",help.problems],["How to undo it",help.undo]].filter(([,value])=>value);
  openModal(help.title,`<div class="settings-help-panel">${rows.map(([name,value])=>`<section><h4>${esc(name)}</h4><p>${esc(value)}</p></section>`).join("")}</div>`);
}


function settingsSnapshot(){ return JSON.stringify(State.draft || {}); }
function markSettingsClean(){ State.settingsBaseline = settingsSnapshot(); }
function settingsDirty(){ return Boolean(State.draft) && settingsSnapshot() !== (State.settingsBaseline || ""); }
function updateDirtySaveButtons(){ document.querySelectorAll('[data-settings-action="save"]').forEach(b=>b.disabled=!settingsDirty()); }

function ensureSettingsNav() {
  if (!Array.isArray(State.settingsNav)) State.settingsNav = [];
}
function navigateSettings(page, options={}) {
  ensureSettingsNav();
  if (options.reset) State.settingsNav = [];
  if (options.push !== false) {
    const current = State.settingsNav[State.settingsNav.length-1];
    if (!current || current.page !== page || JSON.stringify(current.args||[]) !== JSON.stringify(options.args||[])) {
      State.settingsNav.push({page,args:options.args||[]});
    }
  }
  renderSettingsRoute(page, ...(options.args||[]));
}
function settingsBack() {
  ensureSettingsNav();
  if (State.settingsNav.length > 1) {
    State.settingsNav.pop();
    const previous = State.settingsNav[State.settingsNav.length-1];
    renderSettingsRoute(previous.page, ...(previous.args||[]));
    return;
  }
  renderSettingsHome();
}
function renderSettingsRoute(page, ...args) {
  const routes = {
    ripping:rippingPage,
    dashboard:dashboardPage,
    hardware:hardwarePage,
    "hardware-node":hardwareNodePage,
    "hardware-drives":hardwareDrivesPage,
    metadata:metadataPage,
    provider:providerPage,
    system:systemPage,
    advanced:advancedPage,
    updates:updatesPage,
    security:securityPage,
    diagnostics:diagnosticsPage,
    "add-node":addNodePage,
    "install-node-page":adoptionPage,
    "connect-node":existingNodePage,
  };
  (routes[page] || renderSettingsHome)(...args);
}


const toggleRow = (name, description, on, attributes) => `<div class="setting-row">
  <div class="setting-copy">
    <div class="setting-name">${esc(name)}</div>
    <div class="setting-desc">${esc(description)}</div>
  </div>
  <button type="button" class="toggle${on ? " on" : ""}" ${attributes} aria-pressed="${Boolean(on)}"></button>
</div>`;

const toggleRowHelp = (name, description, on, attributes, topic) => `<div class="setting-row">
  <div class="setting-copy">
    <div class="setting-name setting-name-with-help"><span>${esc(name)}</span>${helpButton(topic,`More information about ${name}`)}</div>
    <div class="setting-desc">${esc(description)}</div>
  </div>
  <button type="button" class="toggle${on ? " on" : ""}" ${attributes} aria-pressed="${Boolean(on)}"></button>
</div>`;

const navRow = (name, description, page) => `<button type="button" class="settings-nav-row" data-settings-page="${page}">
  <div class="setting-copy">
    <div class="setting-name">${esc(name)}</div>
    <div class="setting-desc">${esc(description)}</div>
  </div>
  <span class="settings-chevron">›</span>
</button>`;

function drawer(title, html) {
  $("#drawerTitle").textContent = title;
  $("#settingsBackHeader").classList.toggle("hidden", !Array.isArray(State.settingsNav) || State.settingsNav.length <= 1);
  $("#settingsContent").innerHTML = `<div class="settings-page active">${html}</div>`;
  $("#overlay").classList.remove("hidden");
}

async function openSettings() {
  try {
    State.settings = await api("/settings");
    State.draft = structuredClone(State.settings);
    State.settingsNav = [{page:"home",args:[]}];
    markSettingsClean();
    renderSettingsHome();
  } catch (error) { toast(error.message); }
}

function renderSettingsHome() {
  ensureSettingsNav();
  if (!State.settingsNav.length || State.settingsNav[State.settingsNav.length-1]?.page !== "home") {
    State.settingsNav = [{page:"home",args:[]}];
  }
  const nodes=(State.draft?.nodes||[]).filter(node=>!isSimulatorNode(node));
  const driveCount=(State.drives||[]).filter(drive=>drive.node_id!=="simulator").length;
  const providerCount=["metadata_musicbrainz","metadata_google_books","metadata_upcitemdb","metadata_omdb"].filter(k=>State.draft?.[k]).length;
  const cards=[
    ["ripping","Ripping","Disc handling, video defaults and notifications","▶"],
    ["dashboard","Dashboard","Grid layout, tile placement and theme","▦"],
    ["hardware","Hardware","Rip Nodes, drive mapping and connections","▣",`${nodes.length} node${nodes.length===1?"":"s"} · ${driveCount} drive${driveCount===1?"":"s"}`],
    ["metadata","Metadata","Barcode lookup and metadata sources","⌗",`${providerCount}/4 enabled`],
    ["system","System","Updates, advanced options and diagnostics","↻"],
    ["security","Security","PIN protection and access","◇"],
  ];
  drawer("Settings", `<div class="settings-hero">
    <div><span class="settings-eyebrow">RIP MANAGER</span><h2>Control centre</h2><p>Everything needed to run the ripping system, grouped by what you are trying to do.</p></div>
    <div class="settings-version-pill">${esc($("#managerVersion")?.textContent||"")}</div>
  </div>
  <div class="settings-dashboard">${cards.map(([page,title,desc,icon,badge])=>`<button class="settings-dashboard-card" type="button" data-settings-page="${page}">
    <span class="settings-card-icon">${icon}</span><span class="settings-card-copy"><strong>${title}</strong><small>${desc}</small></span>
    ${badge?`<span class="settings-card-badge">${esc(badge)}</span>`:""}<span class="settings-card-arrow">›</span></button>`).join("")}</div>
  <div class="settings-footer-note"><span class="settings-online-dot"></span>Changes apply only after Save changes.</div>`);
}

const isSimulatorNode = (node) => node?.id === "simulator"
  || String(node?.url || "").includes("/simulator-node");

function settingsPage(page) {
  navigateSettings(page);
}

function setToggle(button, value) {
  button.classList.toggle("on", value);
  button.setAttribute("aria-pressed", String(value));
}

function toggleDraft(key, button) {
  State.draft[key] = !State.draft[key];
  setToggle(button, State.draft[key]);
}

function drivePreference(id) {
  if (!State.draft.drive_preferences[id]) {
    State.draft.drive_preferences[id] = {
      detect: true,
      open_tray: true,
      close_tray: true,
    };
  }
  const preference = State.draft.drive_preferences[id];
  if (preference.open_tray === undefined) preference.open_tray = true;
  if (preference.close_tray === undefined) preference.close_tray = true;
  return preference;
}


function availableLayoutDrives() {
  return State.drives.map(drive=>({
    key:dashboardDriveKey(drive), name:drive.name,
    node:drive.node_name||drive.node_id, exists:drive.exists!==false
  }));
}
function normaliseDraftLayout() {
  const cols=Math.max(1,Math.min(6,Number(State.draft.dashboard_columns||3)));
  const rows=Math.max(1,Math.min(8,Number(State.draft.dashboard_rows||2)));
  const count=cols*rows;
  const tiles=Array.isArray(State.draft.dashboard_tiles)?State.draft.dashboard_tiles.slice(0,count):[];
  while(tiles.length<count)tiles.push(null);
  State.draft.dashboard_columns=cols; State.draft.dashboard_rows=rows; State.draft.dashboard_tiles=tiles;
}
function dashboardPage() {
  normaliseDraftLayout();
  const drives=availableLayoutDrives(),cols=State.draft.dashboard_columns,rows=State.draft.dashboard_rows;
  const spacing=Math.max(50,Math.min(300,Number(State.draft.dashboard_spacing_percent || 100)));
  const opts=sel=>`<option value="">Empty</option>`+drives.map(d=>`<option value="${esc(d.key)}"${d.key===sel?" selected":""}>${esc(d.name)} · ${esc(d.node)}${d.exists?"":" · missing"}</option>`).join("");
  const cells=State.draft.dashboard_tiles.map((sel,i)=>`<div class="layout-preview-cell ${sel?"assigned":"empty"}"><select data-layout-cell="${i}">${opts(sel)}</select></div>`).join("");
  const total=cols*rows;
  drawer("Dashboard",`${backBar()}
    <div class="settings-page-intro"><span class="settings-page-icon">▦</span><div><strong>Dashboard</strong><small>Arrange the drive grid exactly as it appears on the main screen.</small></div></div>
    <div class="settings-group">${groupHeading("Grid size","dashboard-layout")}
      <div class="grid-stepper-list">
        <div class="grid-stepper-row"><span>Columns</span><div class="grid-stepper">
          <button type="button" class="stepper-btn" data-layout-step="columns:-1" ${cols<=1?"disabled":""}>−</button><strong>${cols}</strong><button type="button" class="stepper-btn" data-layout-step="columns:1" ${cols>=6?"disabled":""}>+</button>
        </div></div>
        <div class="grid-stepper-row"><span>Rows</span><div class="grid-stepper">
          <button type="button" class="stepper-btn" data-layout-step="rows:-1" ${rows<=1?"disabled":""}>−</button><strong>${rows}</strong><button type="button" class="stepper-btn" data-layout-step="rows:1" ${rows>=8?"disabled":""}>+</button>
        </div></div>
      </div>
      <div class="note compact-note">${total} dashboard cell${total===1?"":"s"}</div>
    </div>
    <div class="settings-group">${groupHeading("Tile placement","dashboard-layout")}
      <div class="layout-preview" style="--layout-preview-cols:${cols};--layout-preview-gap:${Math.round(7*spacing/100)}px">${cells}</div>
      <div class="note compact-note">Each cell is a selector. Empty cells stay visible as plain grey tiles. A drive can only be assigned once.</div>
    </div>
    <div class="settings-group"><h3>Spacing</h3>
      <div class="spacing-control">
        <div class="spacing-label"><span>Tile spacing</span><strong id="spacingValue">${spacing}%</strong></div>
        <input type="range" min="50" max="300" step="10" value="${spacing}" data-setting-input="dashboard_spacing_percent">
        <div class="spacing-scale"><span>0.5×</span><span>Current</span><span>3×</span></div>
      </div>
      <div class="note compact-note">50% is half the original spacing. 100% is the original spacing. 300% is three times the original spacing.</div>
    </div>
    <div class="settings-group"><h3>Appearance</h3>
      <div class="field"><label>Theme</label><select data-setting-input="theme">
        <option value="system"${State.draft.theme==="system"?" selected":""}>System</option>
        <option value="dark"${State.draft.theme==="dark"?" selected":""}>Dark</option>
        <option value="light"${State.draft.theme==="light"?" selected":""}>Light</option>
      </select></div>
      <div class="note compact-note">Theme changes only after Save changes is pressed.</div>
    </div>${saveBar()}`);
}

function stepDraftLayout(dimension,delta) {
  normaliseDraftLayout();
  const current = dimension==="columns" ? State.draft.dashboard_columns : State.draft.dashboard_rows;
  const min=1,max=dimension==="columns"?6:8;
  const next=Math.max(min,Math.min(max,current+Number(delta)));
  if(next===current)return;
  resizeDraftLayout(dimension,next);
}

function resizeDraftLayout(dimension,value) {
  normaliseDraftLayout();
  const oldCols=State.draft.dashboard_columns,oldRows=State.draft.dashboard_rows,old=[...State.draft.dashboard_tiles];
  const newCols=dimension==="columns"?Number(value):oldCols,newRows=dimension==="rows"?Number(value):oldRows;
  const next=new Array(newCols*newRows).fill(null);
  for(let r=0;r<Math.min(oldRows,newRows);r++)for(let c=0;c<Math.min(oldCols,newCols);c++)next[r*newCols+c]=old[r*oldCols+c]||null;
  State.draft.dashboard_columns=newCols;State.draft.dashboard_rows=newRows;State.draft.dashboard_tiles=next;updateDirtySaveButtons();dashboardPage();
}
function assignDraftLayoutCell(index,key) {
  normaliseDraftLayout(); const target=Number(index);
  if(key)State.draft.dashboard_tiles=State.draft.dashboard_tiles.map((x,i)=>x===key&&i!==target?null:x);
  State.draft.dashboard_tiles[target]=key||null; updateDirtySaveButtons();dashboardPage();
}

function hardwarePage() {
  const allNodes=State.draft.nodes||[];
  const nodes=allNodes.filter(node=>!isSimulatorNode(node));
  const simulator=allNodes.find(isSimulatorNode);
  const cards=nodes.map((node,index)=>{
    const realIndex=allNodes.indexOf(node);
    const live=State.nodes.find(n=>n.id===node.id)||{};
    const drives=State.drives.filter(d=>d.node_id===node.id);
    return `<div class="hardware-node-card">
      <div class="hardware-node-head"><div><strong>${esc(node.name)}</strong><small>${esc(node.url)}</small></div><span class="hardware-state ${live.online?"ok":"bad"}">${live.online?"ONLINE":"OFFLINE"}</span></div>
      <div class="hardware-summary"><span>${drives.length} drive${drives.length===1?"":"s"}</span><span>${live.version?`Node API v${esc(live.version)}`:"Node API version unknown"}</span></div>
      <div class="hardware-actions"><button class="secondary" type="button" data-hardware-node="${realIndex}">Manage node</button><button class="primary" type="button" data-settings-page="hardware-drives">Manage drives</button></div>
    </div>`;
  }).join("")||`<div class="note">No Rip Nodes are configured.</div>`;
  drawer("Hardware",`${backBar()}<div class="settings-page-intro"><span class="settings-page-icon">▣</span><div><strong>Nodes and drives</strong><small>Manage each node and the drives connected to it.</small></div></div>
    <div class="settings-group"><h3>Rip Nodes</h3>${cards}</div>
    <div class="settings-group"><h3>Add hardware</h3><button class="primary full-button" type="button" data-settings-page="add-node">Add Rip Node</button></div>
    <div class="settings-group simulator-settings"><div class="simulator-settings-head"><div>${groupHeading("Built-in Simulator","simulator")}<small>Demonstration and training only</small></div><span class="update-badge simulator">BUILT-IN</span></div>
      ${simulator ? `<div class="simulator-status-row"><span>Status</span><strong class="${simulator.enabled?"ok-text":"muted-inline"}">${simulator.enabled?"RUNNING":"OFF"}</strong></div>
        <div class="note compact-note">Two simulated Blu-ray drives and four simulated DVD drives. It never accesses real hardware or storage.</div>
        <button class="secondary full-button" type="button" data-settings-action="reset-simulator" ${simulator.enabled?"":"disabled"}>Reset simulator</button>
        <button class="${simulator.enabled?"danger":"primary"} full-button" type="button" data-settings-action="toggle-simulator">${simulator.enabled?"Disable simulator":"Enable simulator"}</button>`
      : `<div class="note compact-note">The simulator is not configured and does not affect normal operation.</div><button class="primary full-button" type="button" data-settings-action="add-simulator">Add built-in simulator</button>`}
    </div>${saveBar()}`);
}
function hardwareNodePage(index) {
  const node=State.draft.nodes[Number(index)]; if(!node){hardwarePage();return;}
  const live=State.nodes.find(n=>n.id===node.id)||{};
  drawer(node.name,`${backBar()}<div class="settings-page-intro"><span class="settings-page-icon">⌁</span><div><strong>${esc(node.name)}</strong><small>${live.online?"Online":"Offline"}${live.version?` · Node API v${esc(live.version)}`:""}</small></div></div>
    <div class="settings-group">${groupHeading("Connection","node-connection")}
      <div class="field"><label>Friendly name</label><input value="${esc(node.name)}" data-node-index="${index}" data-node-field="name"></div>
      <div class="field"><label>Node URL</label><input value="${esc(node.url)}" data-node-index="${index}" data-node-field="url"></div>
      ${toggleRow("Enabled","Include this node in polling and control",node.enabled,`data-node-toggle="${index}"`)}
    </div>
    <div class="settings-group"><h3>Drives</h3><button class="primary full-button" type="button" data-settings-page="hardware-drives">Manage drive mapping</button></div>
    <div class="settings-group danger-zone"><h3>Danger zone</h3>
      <div class="note compact-note">Remove this node from Rip Manager. This does not uninstall the Node API or delete completed rip history.</div>
      <button class="danger full-button" type="button" data-remove-node="${index}">Remove node</button>
    </div>${saveBar()}`);
}

function beginRemoveNode(index) {
  const node = State.draft?.nodes?.[Number(index)];
  if (!node || isSimulatorNode(node)) return;
  openModal("Remove node", `<div class="intake-section">
    <div class="note">This removes <strong>${esc(node.name)}</strong> from Rip Manager. It will not uninstall the Node API, delete files, or erase completed job history.</div>
    <div class="warning-box">Removal is blocked while this node is ripping, starting, verifying or cancelling.</div>
    <button class="danger full-button" type="button" id="continueNodeRemoval">Continue</button>
  </div>`);
  $("#continueNodeRemoval").onclick = () => confirmRemoveNode(index);
}

function confirmRemoveNode(index) {
  const node = State.draft?.nodes?.[Number(index)];
  if (!node) return;
  $("#genericModalTitle").textContent = "Confirm node removal";
  $("#genericModalBody").innerHTML = `<div class="intake-section">
    <div class="note">Type <strong>${esc(node.name)}</strong> exactly to confirm.</div>
    <div class="field"><label>Friendly node name</label><input id="removeNodeName" autocomplete="off"></div>
    <button class="danger full-button" type="button" id="removeNodeFinal" disabled>Permanently remove</button>
  </div>`;
  const input = $("#removeNodeName"), button = $("#removeNodeFinal");
  input.oninput = () => { button.disabled = input.value !== node.name; };
  button.onclick = async () => {
    button.disabled = true;
    try {
      await api(`/nodes/${encodeURIComponent(node.id)}`, {method:"DELETE", body:JSON.stringify({confirm_name:input.value})});
      closeModal("genericModal");
      State.settings = await api("/settings");
      State.draft = structuredClone(State.settings);
      markSettingsClean();
      toast(`${node.name} removed. Completed history was preserved.`);
      State.settingsNav = [{page:"home",args:[]},{page:"hardware",args:[]}];
      await refresh(true);
      hardwarePage();
    } catch (error) { toast(error.message); button.disabled = input.value !== node.name; }
  };
  input.focus();
}
async function hardwareDrivesPage() {
  drawer("Drives", `${backBar()}
    <div class="settings-page-intro"><span class="settings-page-icon">▣</span>
      <div><strong>Drive mapping</strong><small>Add, remove, replace or swap optical drives without SSH.</small></div>
      ${helpButton("drive-mapping","More information about drive mapping")}
    </div>
    <div id="driveMappingContent"><div class="note">Loading drive mappings…</div></div>
    `);
  await loadDriveMapping();
}

function physicalDriveLabel(drive) {
  return [drive.device, drive.vendor, drive.model].filter(Boolean).join(" · ");
}

async function loadDriveMapping() {
  const box = $("#driveMappingContent");
  if (!box) return;
  const nodes = (State.draft.nodes || []).filter(node=>!isSimulatorNode(node));
  if (!nodes.length) { box.innerHTML = `<div class="note">No Rip Nodes are configured.</div>`; return; }

  const sections = [];
  for (const node of nodes) {
    if (!node.enabled) continue;
    try {
      const data = await api(`/nodes/${encodeURIComponent(node.id)}/drive-mapping?refresh=1`);
      const available = data.available || [];
      const mappings = data.mappings || [];

      const ownerByDevice = new Map();
      mappings.forEach(m => { if (m.device && m.exists) ownerByDevice.set(m.device,m.name); });

      const optionsFor = (currentName,currentDevice,includeBlank=true) => {
        const blank = includeBlank ? `<option value=""${currentDevice?"":" selected"}>Choose detected drive…</option>` : "";
        return blank + available.map(d => {
          const owner=ownerByDevice.get(d.device);
          const selected=d.device===currentDevice ? " selected" : "";
          const suffix=owner && owner!==currentName ? ` · assigned to ${owner}` : owner===currentName ? " · current" : "";
          return `<option value="${esc(d.device)}"${selected}>${esc(physicalDriveLabel(d))}${esc(suffix)}</option>`;
        }).join("");
      };

      const rows = mappings.map(m => {
        const driveId = `${node.id}:${String(m.name).toUpperCase()}`;
        const capability = drivePreference(driveId);
        return `<div class="drive-map-card ${m.exists ? "" : "missing"}">
        <div class="drive-map-head">
          <div><strong>${esc(m.name)}</strong><small>${m.exists ? esc([m.vendor,m.model].filter(Boolean).join(" ") || m.device) : "Mapped drive is missing"}</small></div>
          <span class="drive-map-state ${m.exists ? "ok" : "bad"}">${m.exists ? "CONNECTED" : "MISSING"}</span>
        </div>
        <div class="drive-map-detail"><span>Current device</span><code>${esc(m.device || m.configured_device || "—")}</code></div>
        ${m.id_path ? `<div class="drive-map-detail"><span>USB / udev path</span><code>${esc(m.id_path)}</code></div>` : ""}
        <div class="drive-capability-settings">
          ${toggleRow("Supports Open / Eject","Show Open or Eject and allow Prepare Drive to open this tray",capability.open_tray,`data-drive-id="${esc(driveId)}" data-drive-key="open_tray"`)}
          ${toggleRow("Supports Close","Show the Close tray button for this drive",capability.close_tray,`data-drive-id="${esc(driveId)}" data-drive-key="close_tray"`)}
        </div>
        <div class="drive-map-actions">
          <select data-map-select="${esc(node.id)}:${esc(m.name)}">${optionsFor(m.name,m.exists?m.device:"",true)}</select>
          <button type="button" class="primary" data-map-assign="${esc(node.id)}:${esc(m.name)}">Assign / Swap</button>
          <button type="button" class="secondary danger-button" data-map-remove="${esc(node.id)}:${esc(m.name)}" ${m.active ? "disabled" : ""}>Remove</button>
        </div>
      </div>`;
      }).join("") || `<div class="note">No drive slots are configured on this node.</div>`;

      sections.push(`<div class="settings-group drive-node-group">
        <div class="drive-node-title"><div><h3>${esc(node.name)}</h3><small>${esc(node.url)}</small></div><button type="button" class="secondary" data-map-refresh="${esc(node.id)}">Refresh</button></div>
        <div class="drive-reconcile-panel">
          <button type="button" class="primary full-button" data-map-reconcile="${esc(node.id)}">Auto-assign detected drives</button>
          <div class="note compact-note">Rebuilds the mapping so every detected optical drive has exactly one logical name: DVD1, DVD2, DVD3… Surplus logical slots are removed and extra detected drives automatically create new DVD numbers.</div>
        </div>
        ${rows}
        <div class="drive-map-add">
          <input data-new-slot="${esc(node.id)}" placeholder="New slot name, e.g. DVD4" maxlength="20" autocomplete="off">
          <select data-new-device="${esc(node.id)}">${optionsFor("", "", true)}</select>
          <button type="button" class="primary" data-map-add="${esc(node.id)}">Add drive manually</button>
        </div>
        <div class="note compact-note">Mappings save the physical drive's persistent USB/udev path where possible, so later /dev/srX renumbering should not move the logical DVD slot.</div>
      </div>`);
    } catch (error) {
      sections.push(`<div class="settings-group"><h3>${esc(node.name)}</h3><div class="note bad">Drive mapping requires Rip Node API v0.2.4 or newer. ${esc(error.message)}</div></div>`);
    }
  }
  box.innerHTML = sections.join("");

  box.querySelectorAll("[data-map-reconcile]").forEach(btn => btn.onclick = async () => {
    const nodeId=btn.dataset.mapReconcile;
    if (!confirm("Rebuild drive mappings from the optical drives currently detected by this node? Existing logical slots will be replaced with DVD1, DVD2, DVD3… and surplus slots will be deleted.")) return;
    btn.disabled=true;
    const original=btn.textContent;
    btn.textContent="Rebuilding mappings…";
    try {
      const result=await api(`/nodes/${encodeURIComponent(nodeId)}/drive-mapping/reconcile`,{method:"POST"});
      toast(result.message || "Drive mappings rebuilt");
      await loadDriveMapping();
      await refresh(true);
    } catch(error) {
      toast(error.message);
      btn.disabled=false;
      btn.textContent=original;
    }
  });

  box.querySelectorAll("[data-map-assign]").forEach(btn => btn.onclick = async () => {
    const [nodeId,name] = btn.dataset.mapAssign.split(":");
    const select = box.querySelector(`[data-map-select="${CSS.escape(nodeId+":"+name)}"]`);
    if (!select?.value) { toast("Choose a detected optical drive"); return; }
    btn.disabled = true;
    try {
      await api(`/nodes/${encodeURIComponent(nodeId)}/drive-mapping/${encodeURIComponent(name)}`, {
        method:"PUT", body:JSON.stringify({device:select.value,swap:true})
      });
      toast(`${name} mapping updated`);
      await loadDriveMapping();
      await refresh(true);
    } catch (error) { toast(error.message); btn.disabled=false; }
  });

  box.querySelectorAll("[data-map-remove]").forEach(btn => btn.onclick = async () => {
    const [nodeId,name] = btn.dataset.mapRemove.split(":");
    if (!confirm(`Remove ${name} from this Rip Node? This does not eject or delete the physical drive.`)) return;
    try {
      await api(`/nodes/${encodeURIComponent(nodeId)}/drive-mapping/${encodeURIComponent(name)}`, {method:"DELETE"});
      toast(`${name} removed`);
      await loadDriveMapping();
      await refresh(true);
    } catch (error) { toast(error.message); }
  });

  box.querySelectorAll("[data-map-add]").forEach(btn => btn.onclick = async () => {
    const nodeId=btn.dataset.mapAdd;
    const name=(box.querySelector(`[data-new-slot="${CSS.escape(nodeId)}"]`)?.value || "").trim().toUpperCase();
    const device=box.querySelector(`[data-new-device="${CSS.escape(nodeId)}"]`)?.value || "";
    if (!/^[A-Z][A-Z0-9_-]{0,19}$/.test(name)) { toast("Use a drive name such as DVD4 or BR1"); return; }
    if (!device) { toast("Choose a detected optical drive"); return; }
    try {
      await api(`/nodes/${encodeURIComponent(nodeId)}/drive-mapping/${encodeURIComponent(name)}`, {
        method:"PUT",body:JSON.stringify({device,swap:true})
      });
      toast(`${name} added`);
      await loadDriveMapping();
      await refresh(true);
    } catch (error) { toast(error.message); }
  });

  box.querySelectorAll("[data-map-refresh]").forEach(btn => btn.onclick = loadDriveMapping);
}

function rippingPage() {
  const soundsOn=Boolean(State.draft.sounds);
  drawer("Ripping",`${backBar()}
    <div class="settings-page-intro"><span class="settings-page-icon">▶</span><div><strong>Ripping behaviour</strong><small>What happens before, during and after a rip.</small></div></div>
    <div class="settings-group">${groupHeading("Disc handling","verify-rips")}
      ${toggleRow("Verify completed rips","Check produced files before considering a rip complete",State.draft.verify_before_eject,`data-toggle-key="verify_before_eject"`)}
      ${toggleRowHelp("Auto-eject after successful rip","Open the tray after a successful rip",State.draft.auto_eject,`data-toggle-key="auto_eject"`,"auto-eject")}
      ${toggleRow("Confirm manual eject","Ask before ejecting a disc by hand",State.draft.confirm_eject,`data-toggle-key="confirm_eject"`)}
    </div>
    <div class="settings-group">${groupHeading("Video defaults","video-defaults")}
      <div class="field"><label>Minimum title length (minutes)</label><input type="number" min="0" max="120" value="${State.draft.minimum_video_minutes??2}" data-setting-input="minimum_video_minutes"></div>
      ${toggleRow("Prefer English audio","Prefer English-language audio tracks",State.draft.prefer_english_audio,`data-toggle-key="prefer_english_audio"`)}
      ${toggleRow("Prefer English subtitles","Prefer English subtitle tracks",State.draft.prefer_english_subtitles,`data-toggle-key="prefer_english_subtitles"`)}
    </div>
    <div class="settings-group"><h3>Notifications</h3>
      ${toggleRow("Browser sounds","Play completion and failure alerts",State.draft.sounds,`data-toggle-key="sounds"`)}
      <div class="field ${soundsOn?"":"disabled-setting"}"><label>Volume</label><input type="range" min="0" max="100" step="5" value="${State.draft.volume}" data-setting-input="volume" ${soundsOn?"":"disabled"}></div>
    </div>${saveBar()}`);
}

function metadataPage() {
  const providers=[
    ["musicbrainz","Music","MusicBrainz","Music CD release and artist metadata",State.draft.metadata_musicbrainz],
    ["google_books","Books / Audiobooks","Google Books","Book title and author metadata",State.draft.metadata_google_books],
    ["upcitemdb","DVD / Blu-ray / TV","UPCitemdb","General UPC/EAN metadata",State.draft.metadata_upcitemdb],
    ["omdb","Movie / TV enrichment","OMDb","Structured title and release year enrichment",State.draft.metadata_omdb],
  ];
  drawer("Metadata",`${backBar()}<div class="settings-page-intro"><span class="settings-page-icon">⌗</span><div><strong>Barcode and metadata</strong><small>Choose the sources used when a disc is scanned.</small></div></div>
    <div class="settings-group">${groupHeading("Barcode lookup","barcode")}${toggleRow("Enable barcode lookup","Scan UPC, EAN or ISBN and choose from metadata matches",State.draft.upc_lookup,`data-toggle-key="upc_lookup"`)}</div>
    <div class="settings-group">${groupHeading("Sources","metadata-providers")}<div class="provider-list">${providers.map(([id,type,name,desc,on])=>`<button class="provider-card" type="button" data-provider-page="${id}"><span><strong>${esc(type)}</strong><small>${esc(name)} · ${esc(desc)}</small></span><span class="provider-status ${on?"ok":"off"}">${on?"ENABLED":"OFF"}</span><span>›</span></button>`).join("")}</div></div>${saveBar()}`);
}
function providerPage(provider) {
  if(provider==="musicbrainz"){
    drawer("MusicBrainz",`${backBar()}<div class="settings-group"><h3>MusicBrainz</h3>${toggleRow("Enabled","Use MusicBrainz for music metadata",State.draft.metadata_musicbrainz,`data-toggle-key="metadata_musicbrainz"`)}<button class="secondary full-button" type="button" data-settings-action="test-metadata" data-provider="musicbrainz">Test connection</button></div>${saveBar()}`);return;
  }
  if(provider==="google_books"){
    const on=Boolean(State.draft.metadata_google_books);
    drawer("Google Books",`${backBar()}<div class="settings-group">${groupHeading("Google Books","api-keys")}${toggleRow("Enabled","Use Google Books for book metadata",on,`data-toggle-key="metadata_google_books"`)}<div class="${on?"":"disabled-setting"}"><div class="field"><label>API key <span class="muted-inline">(optional)</span></label><input type="password" value="${esc(State.draft.metadata_google_books_key||"")}" data-setting-input="metadata_google_books_key" ${on?"":"disabled"}></div><button class="secondary full-button" type="button" data-settings-action="test-metadata" data-provider="google_books" ${on?"":"disabled"}>Test connection</button></div></div>${saveBar()}`);return;
  }
  if(provider==="omdb"){
    const on=Boolean(State.draft.metadata_omdb);
    drawer("OMDb",`${backBar()}<div class="settings-group">${groupHeading("OMDb movie / TV enrichment","api-keys")}
      ${toggleRow("Enabled","Use OMDb to confirm movie/series titles and fill the release year",on,`data-toggle-key="metadata_omdb"`)}
      <div class="${on?"":"disabled-setting"}">
        <div class="field"><label>API key</label><input type="password" value="${esc(State.draft.metadata_omdb_key||"")}" data-setting-input="metadata_omdb_key" ${on?"":"disabled"}></div>
        <div class="note compact-note">UPC identifies the product first. OMDb is then queried using the cleaned movie/series title; only an exact title match is allowed to replace the Year field.</div>
      </div>
    </div>${saveBar()}`);return;
  }
  const on=Boolean(State.draft.metadata_upcitemdb),paid=State.draft.metadata_upcitemdb_mode==="paid";
  drawer("UPCitemdb",`${backBar()}<div class="settings-group">${groupHeading("UPCitemdb","api-keys")}${toggleRow("Enabled","Use UPCitemdb for general product metadata",on,`data-toggle-key="metadata_upcitemdb"`)}<div class="${on?"":"disabled-setting"}"><div class="field"><label>Access</label><select data-setting-input="metadata_upcitemdb_mode" ${on?"":"disabled"}><option value="free"${paid?"":" selected"}>Free / trial</option><option value="paid"${paid?" selected":""}>Paid API key</option></select></div>${paid?`<div class="field"><label>User key</label><input type="password" value="${esc(State.draft.metadata_upcitemdb_key||"")}" data-setting-input="metadata_upcitemdb_key"></div>`:""}<button class="secondary full-button" type="button" data-settings-action="test-metadata" data-provider="upcitemdb" ${on?"":"disabled"}>Test connection</button></div></div>${saveBar()}`);
}

async function diagnosticsPage() {
  drawer("Diagnostics", `${backBar()}<div class="settings-group"><h3>System Check</h3><div id="diagnosticsResult" class="note">Running checks…</div><button class="secondary full-button" type="button" data-settings-action="run-diagnostics">Run again</button></div>`);
  runDiagnostics();
}

async function runDiagnostics() {
  const box = $("#diagnosticsResult"); if (!box) return;
  box.textContent = "Checking Rip Manager and nodes…";
  try {
    const [health, updates, fleet] = await Promise.all([api("/health"), api("/updates/status"), api("/fleet")]);
    const nodes = updates.nodes || [];
    box.innerHTML = `<div class="diagnostic-line ok"><strong>Rip Manager</strong><span>✓ Online · v${esc(updates.manager.installed)}</span></div>
      <div class="diagnostic-line ${updates.source ? "ok" : "bad"}"><strong>Update source</strong><span>${esc(updates.source || "Unavailable")}</span></div>
      ${nodes.map(n=>`<div class="diagnostic-line ${n.simulator||n.version&&!n.error?"ok":"bad"}"><strong>${esc(n.name)}${n.simulator?` · BUILT-IN`:""}</strong><span>${n.simulator&&!n.version?"Off":n.version?`✓ v${esc(n.version)}`:`✕ ${esc(n.error||"Offline")}`}</span></div>`).join("")}`;
  } catch (error) { box.textContent = error.message; }
}

function addNodePage() {
  drawer("Add Rip Node",`${backBar()}<div class="settings-page-intro"><span class="settings-page-icon">＋</span><div><strong>Choose how to add the node</strong><small>The next screen only asks for details required by that method.</small></div></div>
    <div class="settings-group">${groupHeading("Fresh Ubuntu installer","fresh-install")}<button class="settings-nav-row" type="button" data-settings-page="install-node-page"><div class="setting-copy"><div class="setting-name">Install on fresh Ubuntu</div><div class="setting-desc">Connect over SSH, install the bundled Node API, configure storage and adopt it.</div></div><span class="settings-chevron">›</span></button></div>
    <div class="settings-group">${groupHeading("Existing Node API","existing-node")}<button class="settings-nav-row" type="button" data-settings-page="connect-node"><div class="setting-copy"><div class="setting-name">Connect existing Node API</div><div class="setting-desc">Add an API that is already installed. No SSH or storage credentials required.</div></div><span class="settings-chevron">›</span></button></div>`);
}

function existingNodePage() {
  drawer("Connect Existing Node",`${backBar()}<div class="settings-page-intro"><span class="settings-page-icon">⌁</span><div><strong>Existing Node API</strong><small>Test and add a node without SSH.</small></div></div>
    <div class="settings-group"><h3>Identity</h3>
      <div class="field"><label>Friendly name</label><input id="adoptName" value="Rip Node 1" autocomplete="off"></div>
      <div class="field"><label>Node ID</label><input id="adoptId" value="rip-node-1" autocomplete="off"></div>
    </div>
    <div class="settings-group">${groupHeading("Connection","existing-node")}
      <div class="field"><label>Node API URL</label><input id="adoptNodeUrl" placeholder="http://192.168.1.50:8000" autocomplete="off"></div>
      <div class="field"><label>API token <span class="muted-inline">(optional)</span></label><input id="adoptExistingToken" type="password" autocomplete="off"></div>
      <button class="secondary full-button" type="button" data-settings-action="test-existing-node">Test connection</button>
      <div id="adoptTestResult" class="note compact-note">No SSH connection is used.</div>
    </div>
    <button class="primary full-button" type="button" data-settings-action="pair-existing-node">Add existing node</button>`);
}

function adoptionPage() {
  const managerDefault = window.location.origin;
  drawer("Add / Adopt Rip Node", `${backBar()}
    <div class="settings-group">${groupHeading("1 · Node Identity","fresh-install")}
      <div class="field"><label>Friendly name</label><input id="adoptName" value="Rip Node 2" autocomplete="off"></div>
      <div class="field"><label>Node ID</label><input id="adoptId" value="rip-node-2" autocomplete="off"></div>
      <div class="field"><label>Rip output path on node</label><input id="adoptOutput" value="/mnt/ripping" autocomplete="off"></div>
      <div class="field"><label class="label-with-help">NAS share <span class="muted-inline">(optional)</span>${helpButton("nas-mount","More information about NAS storage")}</label><input id="adoptNasShare" value="//192.168.1.187/Rips" autocomplete="off"></div>
      <div class="field"><label>NAS username</label><input id="adoptNasUser" autocomplete="username"></div>
      <div class="field"><label>NAS password</label><input id="adoptNasPassword" type="password" autocomplete="off"></div>
      <div class="field"><label>Node API port</label><input id="adoptApiPort" type="number" min="1" max="65535" value="8000"></div>
    </div>
    <div class="settings-group">${groupHeading("2 · One-time SSH Installer","fresh-install")}
      <div class="field"><label>Ubuntu host / IP</label><input id="adoptHost" placeholder="192.168.1.50" autocomplete="off"></div>
      <div class="field"><label>SSH port</label><input id="adoptSshPort" type="number" min="1" max="65535" value="22"></div>
      <div class="field"><label>SSH username</label><input id="adoptUser" placeholder="adam" autocomplete="username"></div>
      <div class="field"><label>SSH password</label><input id="adoptPassword" type="password" autocomplete="current-password"></div>
      <div class="field"><label>Sudo password <span class="muted-inline">(blank = same as SSH)</span></label><input id="adoptSudo" type="password" autocomplete="off"></div>
      <button class="secondary full-button" type="button" data-settings-action="test-node-ssh">Test SSH &amp; Ubuntu</button>
      <div id="adoptTestResult" class="note compact-note">No credentials are stored by Rip Manager.</div>
    </div>
    <div class="settings-group">${groupHeading("3 · Network Addresses","network-addresses")}
      <div class="field"><label>Address Rip Manager uses to reach this node</label>
        <input id="adoptNodeUrl" placeholder="Blank = http://HOST:8000" autocomplete="off"></div>
      <div class="note compact-note">For a node on this same physical server, use <code>http://host.docker.internal:8000</code>. For another machine use its LAN IP. If both are Docker services on the same network, a service name also works.</div>
      <div class="field"><label>Rip Manager address the node uses for automatic updates</label>
        <input id="adoptManagerUrl" value="${esc(managerDefault)}" autocomplete="off"></div>
      <div class="note compact-note">This address is saved on the new node's updater. Same machine: <code>http://127.0.0.1:8088</code> works when the manager port is published. Another LAN machine should normally use Rip Manager's LAN address.</div>
    </div>
    <div class="settings-group"><h3>4 · Review and install</h3>
      <button id="adoptInstall" class="primary full-button" type="button" data-settings-action="install-node">Install &amp; Adopt Node</button>
      <div id="adoptInstallResult" class="note compact-note">The clean installer adds Ubuntu dependencies, the API, NAS mount, persistent drive mappings and manual-only GitHub updates, then verifies and adopts the node. Credentials are never saved by Manager.</div>
      <section id="adoptProgress" class="adopt-progress hidden" aria-live="polite" aria-label="Node installation progress">
        <div class="adopt-progress-head"><strong id="adoptProgressStage">Preparing installer</strong><span id="adoptProgressElapsed">0s</span></div>
        <div class="adopt-progress-track" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"><span id="adoptProgressFill"></span></div>
        <div class="adopt-progress-meta"><span id="adoptProgressMessage">Waiting to start…</span><strong id="adoptProgressPercent">0%</strong></div>
        <ol id="adoptProgressEvents" class="adopt-progress-events"></ol>
      </section>
    </div>`);
  setTimeout(resumeAdoptionProgress, 0);
}

function adoptionFields() {
  return {
    node_id: $("#adoptId")?.value.trim(),
    name: $("#adoptName")?.value.trim(),
    host: $("#adoptHost")?.value.trim(),
    ssh_port: Number($("#adoptSshPort")?.value || 22),
    username: $("#adoptUser")?.value.trim(),
    password: $("#adoptPassword")?.value || "",
    sudo_password: $("#adoptSudo")?.value || null,
    api_port: Number($("#adoptApiPort")?.value || 8000),
    node_url: $("#adoptNodeUrl")?.value.trim() || null,
    manager_url_for_node: $("#adoptManagerUrl")?.value.trim(),
    output_path: $("#adoptOutput")?.value.trim() || "/mnt/ripping",
    nas_share: $("#adoptNasShare")?.value.trim() || null,
    nas_username: $("#adoptNasUser")?.value || null,
    nas_password: $("#adoptNasPassword")?.value || null,
  };
}

async function testNodeSsh() {
  const fields = adoptionFields();
  const result = $("#adoptTestResult");
  if (!fields.host || !fields.username || !fields.password) { toast("Enter host, username and SSH password"); return; }
  result.textContent = "Testing SSH and sudo…";
  try {
    const info = await api("/adoption/test-ssh", { method: "POST", body: JSON.stringify({
      host: fields.host, ssh_port: fields.ssh_port, username: fields.username,
      password: fields.password, sudo_password: fields.sudo_password,
    }) });
    result.innerHTML = `<strong>${esc(info.hostname || fields.host)}</strong> · ${esc(info.os || "Linux")} · sudo ${info.sudo ? "OK" : "FAILED"}<br>
      Python: ${esc(info.python3 || "missing")} · MakeMKV: ${info.makemkv ? "installed" : "not detected"} · Drives: ${esc((info.optical_devices || []).join(", ") || "none detected")}`;
  } catch (error) {
    result.textContent = error.message;
  }
}


async function pairExistingNode() {
  const fields = adoptionFields();
  if (!fields.node_id || !fields.name || !fields.node_url) {
    toast("Enter Node ID, friendly name and the Manager connection URL"); return;
  }
  const token = $("#adoptExistingToken")?.value || null;
  try {
    await api("/nodes", { method: "POST", body: JSON.stringify({
      id: fields.node_id, name: fields.name, url: fields.node_url, enabled: true, token,
    }) });
    toast(`${fields.name} adopted`);
    State.settings = await api("/settings");
    State.draft = structuredClone(State.settings);
    hardwarePage();
  } catch (error) { toast(error.message); }
}

async function testExistingNode() {
  const url=$("#adoptNodeUrl")?.value.trim(), token=$("#adoptExistingToken")?.value||null;
  const result=$("#adoptTestResult");
  if(!url){result.textContent="Enter the Node API URL";return;}
  result.textContent="Testing Node API…";
  try{
    const info=await api("/adoption/test-existing",{method:"POST",body:JSON.stringify({url,token})});
    result.textContent=`✓ ${info.node||"Rip Node"} · API v${info.version||"unknown"}`;
  }catch(error){result.textContent=`✕ ${error.message}`;}
}

async function reloadSettingsPage(page="hardware") {
  State.settings=await api("/settings"); State.draft=structuredClone(State.settings);
  await refresh(); navigateSettings(page,{replace:true});
}

async function addSimulator() {
  try{
    await api("/nodes",{method:"POST",body:JSON.stringify({id:"simulator",name:"Simulator Node",url:"http://127.0.0.1:8080/simulator-node",enabled:true,token:null})});
    toast("Built-in simulator enabled"); await reloadSettingsPage();
  }catch(error){toast(error.message);}
}

async function toggleSimulator() {
  const simulator=(State.draft.nodes||[]).find(isSimulatorNode); if(!simulator)return;
  const enabled=!simulator.enabled;
  try{
    await api(`/nodes/${encodeURIComponent(simulator.id)}`,{method:"PUT",body:JSON.stringify({enabled})});
    if(!enabled){
      const settings=await api("/settings");
      settings.dashboard_tiles=(settings.dashboard_tiles||[]).map(key=>String(key||"").startsWith(`${simulator.id}:`)?null:key);
      await api("/settings",{method:"PUT",body:JSON.stringify(settings)});
    }
    toast(enabled?"Simulator enabled":"Simulator disabled"); await reloadSettingsPage();
  }catch(error){toast(error.message);}
}

async function resetSimulator() {
  if(!confirm("Reset every simulated drive and job to the starting demonstration scene?"))return;
  try{const result=await api("/simulator-node/reset",{method:"POST"});toast(result.message||"Simulator reset");await refresh(true);hardwarePage();}
  catch(error){toast(error.message);}
}

async function installNode() {
  const fields = adoptionFields();
  if (!fields.node_id || !fields.name || !fields.host || !fields.username || !fields.password || !fields.manager_url_for_node) {
    toast("Complete the node, SSH and manager address fields"); return;
  }
  if (!confirm(`Install Rip Node on ${fields.host} and adopt it as ${fields.name}?`)) return;
  const button = $("#adoptInstall");
  const result = $("#adoptInstallResult");
  button.disabled = true;
  button.textContent = "Installation running…";
  result.textContent = "The installer is running in the background. You can follow every stage below.";
  try {
    const started = await api("/adoption/install/start", { method: "POST", body: JSON.stringify(fields) });
    sessionStorage.setItem("ripManagerAdoptionJob", started.job_id);
    await watchAdoptionProgress(started.job_id);
  } catch (error) {
    result.textContent = error.message;
    button.disabled = false;
    button.textContent = "Install & Adopt Node";
  }
}

function renderAdoptionProgress(job) {
  const panel = $("#adoptProgress");
  if (!panel) return;
  panel.classList.remove("hidden", "complete", "failed");
  panel.classList.toggle("complete", job.state === "complete");
  panel.classList.toggle("failed", job.state === "failed");
  const value = Math.max(0, Math.min(100, Number(job.percent) || 0));
  $("#adoptProgressStage").textContent = job.state === "failed" ? "Installation stopped" : job.state === "complete" ? "Installation complete" : String(job.stage || "Installing").replaceAll("_", " ");
  $("#adoptProgressElapsed").textContent = formatDuration(Date.now() / 1000 - Number(job.started_at || Date.now() / 1000));
  $("#adoptProgressMessage").textContent = job.error || job.message || "Working…";
  $("#adoptProgressPercent").textContent = `${Math.round(value)}%`;
  $("#adoptProgressFill").style.width = `${value}%`;
  panel.querySelector('[role="progressbar"]').setAttribute("aria-valuenow", String(Math.round(value)));
  $("#adoptProgressEvents").innerHTML = (job.events || []).map((event, index, events) =>
    `<li class="${index === events.length - 1 ? "current" : "done"}"><span>${index < events.length - 1 || job.state === "complete" ? "✓" : job.state === "failed" ? "!" : "•"}</span><div><strong>${esc(String(event.stage || "step").replaceAll("_", " "))}</strong><small>${esc(event.message || "")}</small></div><b>${Math.round(Number(event.percent) || 0)}%</b></li>`
  ).join("");
}

async function watchAdoptionProgress(jobId) {
  const button = $("#adoptInstall");
  const result = $("#adoptInstallResult");
  while (true) {
    let job;
    try {
      job = await api(`/adoption/install/${encodeURIComponent(jobId)}`);
    } catch (error) {
      if (result) result.textContent = `Unable to read installer progress: ${error.message}`;
      if (button) { button.disabled = false; button.textContent = "Resume installer status"; }
      return;
    }
    renderAdoptionProgress(job);
    if (job.state === "complete") {
      sessionStorage.removeItem("ripManagerAdoptionJob");
      const installed = job.result || {};
      if (result) result.innerHTML = `<strong>Adopted successfully.</strong> ${esc(installed.name || "Rip Node")} · v${esc(installed.version || "unknown")} · ${esc(installed.url || "")}${installed.warning ? `<br>${esc(installed.warning)}` : ""}`;
      if (button) { button.disabled = true; button.textContent = "Installed successfully"; }
      toast(`${installed.name || "Rip Node"} adopted`);
      State.settings = await api("/settings"); State.draft = structuredClone(State.settings);
      return;
    }
    if (job.state === "failed") {
      sessionStorage.removeItem("ripManagerAdoptionJob");
      if (result) result.textContent = job.error || "Installation failed";
      if (button) { button.disabled = false; button.textContent = "Retry Install & Adopt"; }
      return;
    }
    await new Promise(resolve => setTimeout(resolve, 1000));
  }
}

function resumeAdoptionProgress() {
  const jobId = sessionStorage.getItem("ripManagerAdoptionJob");
  if (!jobId || !$("#adoptProgress")) return;
  const button = $("#adoptInstall");
  if (button) { button.disabled = true; button.textContent = "Installation running…"; }
  watchAdoptionProgress(jobId);
}

function systemPage() {
  drawer("System",`${backBar()}<div class="settings-page-intro"><span class="settings-page-icon">↻</span><div><strong>System</strong><small>Updates, advanced behaviour and troubleshooting.</small></div></div>
    <div class="settings-group"><h3>Updates</h3>${navRow("Updates","Manager, Rip Nodes and Update System","updates")}</div>
    <div class="settings-group"><h3>Advanced</h3>${navRow("Advanced settings","Polling, API tools and developer options","advanced")}</div>
    <div class="settings-group"><h3>Tools</h3>${navRow("Run system diagnostics","Check Manager, nodes and update share","diagnostics")}</div>
    <div class="settings-group"><h3>Restart</h3><div class="note compact-note">Restarts Rip Manager only. Active rips continue on their Rip Nodes.</div><button class="secondary system-restart-button" type="button" data-settings-action="restart-manager">Restart Rip Manager</button></div>`);
}

async function restartManager() {
  if (!confirm("Restart Rip Manager now? The page will be unavailable briefly. Active node rips will continue.")) return;
  const button = document.querySelector('[data-settings-action="restart-manager"]');
  if (button) { button.disabled = true; button.textContent = "Restarting…"; }
  try {
    await api("/system/restart", {method:"POST"});
    toast("Rip Manager is restarting");
    setTimeout(() => location.reload(), 5000);
  } catch (error) {
    if (button) { button.disabled = false; button.textContent = "Restart Rip Manager"; }
    toast(error.message || "Restart request failed");
  }
}
function advancedPage() {
  drawer("Advanced",`${backBar()}<div class="settings-group">${groupHeading("Dashboard polling","polling")}
    <div class="field"><label>Idle polling interval (seconds)</label><input type="number" min="2" max="300" value="${State.draft.idle_poll_seconds}" data-setting-input="idle_poll_seconds"></div>
    <div class="field"><label>Active rip polling interval (seconds)</label><input type="number" min="1" max="60" value="${State.draft.active_poll_seconds}" data-setting-input="active_poll_seconds"></div>
    </div><div class="settings-group">${groupHeading("Developer tools","api-tools")}<a class="primary api-link full-button" href="/docs" target="_blank" rel="noreferrer">Open API tools</a></div>${saveBar()}`);
}
function securityPage() {
  const hasPin=Boolean(State.draft.pin_set),lockOn=Boolean(State.draft.lock_enabled);
  drawer("Security",`${backBar()}<div class="settings-page-intro"><span class="settings-page-icon">◇</span><div><strong>PIN protection</strong><small>${hasPin?"A 4–8 digit PIN is configured.":"No PIN is configured."}</small></div></div>
    <div class="settings-group">${groupHeading("Protection","pin-lock")}${toggleRow("Lock Rip Manager","Require the PIN before controls and API access",lockOn,`data-toggle-key="lock_enabled"`)}<div class="security-status-row"><span>Status</span><strong class="${lockOn&&hasPin?"ok-text":"muted-inline"}">${lockOn&&hasPin?"ENABLED":hasPin?"PIN SET · LOCK OFF":"NOT CONFIGURED"}</strong></div></div>
    <div class="settings-group"><h3>${hasPin?"Change PIN":"Set PIN"}</h3>
      ${hasPin?`<div class="field"><label>Current PIN</label><input id="currentPin" type="password" inputmode="numeric" pattern="[0-9]{4,8}" minlength="4" maxlength="8" autocomplete="current-password" placeholder="4–8 digits"></div>`:""}
      <div class="field"><label>${hasPin?"New PIN":"PIN"}</label><input id="newPin" type="password" inputmode="numeric" pattern="[0-9]{4,8}" minlength="4" maxlength="8" autocomplete="new-password" placeholder="4–8 digits"></div>
      <div class="field"><label>Confirm ${hasPin?"new ":""}PIN</label><input id="confirmPin" type="password" inputmode="numeric" pattern="[0-9]{4,8}" minlength="4" maxlength="8" autocomplete="new-password" placeholder="4–8 digits"></div>
      <div class="note compact-note">PINs must contain numbers only and can be between 4 and 8 digits long.</div>
    </div>${lockOn&&hasPin?`<button class="secondary full-button" type="button" data-settings-action="lock">Lock now</button>`:""}${saveBar()}`);
}

function updatesPage() {
  drawer("Updates", `${backBar()}
    <div class="settings-group"><div class="update-share-heading">${groupHeading("Manager and Node Updates","updates")}</div>
      <div id="updateStatus" class="update-status"><span>Checking GitHub…</span></div>
      <div class="update-actions">
        <button id="checkUpdates" class="secondary" type="button">Check now</button>
        <button id="installManager" class="primary" type="button" disabled>Install update</button>
        <button id="pushNodes" class="primary node-update" type="button" disabled>Install node update</button>
      </div>
    </div>
    <div class="settings-group">${groupHeading("Rollback","rollback")}<div class="note compact-note">Restore Manager application code while preserving settings, PIN, nodes and history.</div><div id="rollbackStatus"><span>Loading verified backups…</span></div></div>
    `);

  $("#checkUpdates").onclick = () => loadUpdates(true);
  $("#installManager").onclick = installManagerUpdate;
  $("#pushNodes").onclick = pushNodeUpdates;
  loadUpdates();
}

async function saveSettings() {
  const pin = $("#newPin")?.value || "";
  const confirmPin = $("#confirmPin")?.value || "";
  const currentPin = $("#currentPin")?.value || "";

  if (pin !== confirmPin) { toast("PINs do not match"); return; }
  if (pin && !/^\d{4,8}$/.test(pin)) { toast("PIN must be 4–8 digits"); return; }
  if (State.draft.pin_set && pin && !/^\d{4,8}$/.test(currentPin)) {
    toast("Enter your current 4–8 digit PIN");
    return;
  }
  if (State.draft.lock_enabled && !State.draft.pin_set && !pin) {
    toast("Set a 4–8 digit PIN before enabling the lock");
    return;
  }

  const body = { ...State.draft };
  delete body.pin_set;
  if (pin) {
    body.new_pin = pin;
    if (State.draft.pin_set) body.current_pin = currentPin;
  }

  try {
    State.settings = await api("/settings", { method: "PUT", body: JSON.stringify(body) });
    State.draft = structuredClone(State.settings); markSettingsClean(); State.draft = null;
    applyTheme();
    $("#overlay").classList.add("hidden");
    toast("Settings saved");
    await refresh();
  } catch (error) {
    toast(error.message);
  }
}

function cancelSettings() {
  if(State.draft&&settingsDirty()&&!confirm("Discard unsaved settings changes?"))return;
  if(State.settings)document.documentElement.dataset.theme=State.settings.theme;
  State.draft=null;State.settingsBaseline="";$("#overlay").classList.add("hidden");
}

function applyTheme() {
  if (!State.settings) return;
  document.documentElement.dataset.theme = State.settings.theme;
  localStorage.theme = State.settings.theme;
}

async function lockNow() {
  try {
    await api("/auth/logout", { method: "POST" });
    cancelSettings();
    showLogin();
  } catch (error) {
    toast(error.message);
  }
}

/* ------------------------------------------------------------------ *
 * 9. Updates
 * ------------------------------------------------------------------ */

function updateLine(label, installed, available, state = "") {
  const badge = state === "simulator" ? "BUILT-IN" : state === "new" ? "UPDATE AVAILABLE" : state === "ok" ? "CURRENT" : "CHECK";
  const detail = state === "simulator" ? " · Managed with Rip Manager" : available ? ` · Available: ${esc(available)}` : " · No update file found";
  return `<div class="update-line">
    <div><strong>${esc(label)}</strong><small>Installed: ${esc(installed || "Unknown")}${detail}</small></div>
    <span class="update-badge ${state}">${badge}</span>
  </div>`;
}

async function loadUpdates(announce = false) {
  const box = $("#updateStatus");
  const install = $("#installManager");
  const pushNodes = $("#pushNodes");
  if (!box) return;

  box.innerHTML = "<span>Checking GitHub…</span>";
  install.disabled = true;
  pushNodes.disabled = true;

  try {
    const status = await api("/updates/status");
    const manager = status.manager.available;
    const nodeVersion = status.node_update?.version;

    let html = updateLine("Rip Manager", status.manager.installed, manager?.version,
      manager?.newer ? "new" : "ok");
    const visibleNodes = status.nodes.filter((node) => !(node.simulator && node.enabled === false));
    html += visibleNodes.map((node) => updateLine(node.name, node.version || (node.simulator ? "Included" : null), nodeVersion,
      node.simulator ? "simulator" : node.update_available ? "new" : node.version ? "ok" : "")).join("");
    if (status.host_updater?.message) {
      html += `<div class="update-result ${esc(status.host_updater.state || "")}">${esc(status.host_updater.message)}</div>`;
    }
    if (status.node_update_request?.pending) {
      html += `<div class="update-result">Node update v${esc(status.node_update_request.version || "unknown")} is queued. <button class="inline-cancel" id="cancelNodeUpdate" type="button">Cancel</button></div>`;
    }

    box.innerHTML = html;
    renderRollbackStatus(status);
    const cancel=$("#cancelNodeUpdate"); if(cancel)cancel.onclick=cancelNodeUpdate;
    install.disabled = !manager?.newer;
    const nodeUpdateAvailable = Boolean(status.node_update && status.nodes.some((node) => node.update_available));
    pushNodes.disabled = !nodeUpdateAvailable;
    if (announce) toast("GitHub releases checked");
  } catch (error) {
    box.innerHTML = `<div class="update-result error">${esc(error.message)}</div>`;
    if (announce) toast("Update check failed");
  }
}

function rollbackCard(backup,primary=false) {
  return `<div class="rollback-backup"><div><strong>v${esc(backup.version||"unknown")}</strong><small>${esc(stamp(backup.created_at))} · ${esc(formatBytes(backup.bytes))} · Code only</small></div><button class="${primary?"danger":"secondary"}" type="button" data-rollback-file="${esc(backup.filename)}" data-rollback-version="${esc(backup.version||"unknown")}">${primary?"Roll back":"Select"}</button></div>`;
}

function renderRollbackStatus(status) {
  const box=$("#rollbackStatus"); if(!box)return;
  const capabilities=status.host_capabilities||{};
  if(!capabilities.rollback){
    box.innerHTML=`<div class="note bad"><strong>Host updater refresh required</strong><br>Run this once on Byte-Me before rollback can be used:<br><code>bash /mnt/user/appdata/rip-manager/rip_manager_container/unraid/install-rip-github-updater.sh</code></div>`;
    return;
  }
  if(status.rollback_request?.pending){
    box.innerHTML=`<div class="update-result">Rollback to v${esc(status.rollback_request.version||"unknown")} is queued. The Manager will briefly go offline.</div>`;
    return;
  }
  const backups=(status.rollback_backups||[]).filter(item=>item.version!==status.manager.installed);
  if(!backups.length){
    box.innerHTML=`<div class="note compact-note">No earlier verified backup is available yet. A code-only backup is created before each future Manager update.</div>`;
    return;
  }
  const [latest,...older]=backups;
  box.innerHTML=`${rollbackCard(latest,true)}${older.length?`<details class="rollback-older"><summary>Show ${older.length} older backup${older.length===1?"":"s"}</summary>${older.map(item=>rollbackCard(item)).join("")}</details>`:""}`;
  box.querySelectorAll("[data-rollback-file]").forEach(button=>button.onclick=()=>requestRollback(button.dataset.rollbackFile,button.dataset.rollbackVersion));
}

async function requestRollback(filename,version) {
  if(!confirm(`Roll back Rip Manager to v${version}?\n\nSettings, PIN, node configuration and job history will be preserved.`))return;
  try{
    const result=await api("/updates/rollback",{method:"POST",body:JSON.stringify({filename})});
    toast(result.message||"Rollback queued"); await loadUpdates();
  }catch(error){toast(error.message);}
}

async function installManagerUpdate() {
  if (!confirm("Install the available Rip Manager update? The web page will briefly go offline.")) return;
  try {
    const result = await api("/updates/install-manager", { method: "POST" });
    toast(result.message || "Update requested");
    $("#installManager").disabled = true;
  } catch (error) {
    toast(error.message);
  }
}

async function pushNodeUpdates() {
  if (!confirm("Install the newest Rip Node release on configured nodes? Busy nodes will wait until their rips finish.")) return;
  try {
    const result = await api("/updates/push-nodes", { method: "POST" });
    toast(result.message || "Node update queued");
    await loadUpdates();
  } catch (error) {
    toast(error.message);
  }
}

async function cancelNodeUpdate() {
  try{const result=await api("/updates/node-install-request",{method:"DELETE"});toast(result.message||"Queued update cancelled");await loadUpdates();}
  catch(error){toast(error.message);}
}

/* ------------------------------------------------------------------ *
 * 10. Events, sounds and refresh
 * ------------------------------------------------------------------ */

function playTone(type) {
  if (!State.settings?.sounds) return;
  try {
    const context = new (window.AudioContext || window.webkitAudioContext)();
    const gain = context.createGain();
    gain.gain.value = (Number(State.settings.volume ?? 75) / 100) * 0.16;
    gain.connect(context.destination);

    const sequence = ["complete", "auto_started"].includes(type)
      ? [[900, 0.08], [1350, 0.12]]
      : [[180, 0.15], [140, 0.3]];

    let at = context.currentTime;
    sequence.forEach(([frequency, duration]) => {
      const oscillator = context.createOscillator();
      oscillator.frequency.value = frequency;
      oscillator.connect(gain);
      oscillator.start(at);
      oscillator.stop(at + duration);
      at += duration + 0.05;
    });
  } catch { /* audio is a nicety, never a failure */ }
}

async function drainEvents() {
  try {
    const events = await api(`/events?since_id=${State.eventCursor}&limit=100`);
    for (const event of events) {
      State.eventCursor = Math.max(State.eventCursor, event.id);
      localStorage.eventCursor = State.eventCursor;
      playTone(event.event_type);
      const label = STATE_LABELS[event.event_type] || event.event_type.replaceAll("_", " ");
      toast(`${event.drive || ""} ${label}`.trim());
    }
  } catch { /* the next refresh will retry */ }
}

function anythingBusy() {
  return State.drives.some((drive) =>
    isActive(jobFor(drive)) || Boolean(drive.pending_intake));
}

/** Follow the same cadence the server polls its nodes with. */
function scheduleRefresh() {
  clearTimeout(State.refreshTimer);
  const settings = State.settings || {};
  const seconds = anythingBusy()
    ? Number(settings.active_poll_seconds || 2)
    : Number(settings.idle_poll_seconds || 5);
  State.refreshTimer = setTimeout(refresh, Math.max(1, seconds) * 1000);
}

async function refresh(announce = false) {
  try {
    const [overview, drives, jobs, stats] = await Promise.all([
      api("/overview"), api("/drives"), api("/jobs?limit=100"), api("/system/stats"),
    ]);

    State.nodes = overview.nodes || [];
    State.settings = overview.settings || State.settings;
    State.drives = drives || [];
    State.jobs = jobs || [];
    State.stats = stats || [];
    State.refreshedAt = Date.now() / 1000;

    const connectionNodes = State.nodes.filter((node) => node.enabled && node.id !== "simulator");
    const online = connectionNodes.filter((node) => node.online).length;
    const pill = $("#connection");
    pill.textContent = connectionNodes.length ? `${online}/${connectionNodes.length} online` : "No real nodes";
    pill.classList.toggle("good", online === connectionNodes.length && online > 0);
    pill.classList.toggle("bad", connectionNodes.length > 0 && online === 0);

    renderGrid();

    // Keep an open popup honest instead of showing a frozen snapshot.
    if (!$("#genericModal").classList.contains("hidden")
        && $("#genericModalTitle").textContent === "System Stats") {
      showStats();
    }
    if (!$("#genericModal").classList.contains("hidden")
        && $("#genericModalTitle").textContent !== "System Stats"
        && State.openDriveKey) {
      const liveDrive = State.drives.find((drive) =>
        drive.node_id === State.openDriveKey.nodeId && drive.name === State.openDriveKey.name);
      if (liveDrive && isActive(tileJobFor(liveDrive))) openDrive(liveDrive);
    }
    if (!$("#intakeModal").classList.contains("hidden")
        && !$("#ripForm").classList.contains("hidden")) {
      renderIntakeActions();
    }

    await drainEvents();
    if (announce) toast("Updated");
  } catch (error) {
    const pill = $("#connection");
    pill.textContent = "Manager error";
    pill.classList.add("bad");
    if (announce) toast(error.message);
  } finally {
    scheduleRefresh();
  }
}

/* ------------------------------------------------------------------ *
 * 11. Login and bootstrap
 * ------------------------------------------------------------------ */

function showLogin() {
  clearTimeout(State.refreshTimer);
  $("#loginModal").classList.remove("hidden");
  $("#loginPin").value = "";
  setTimeout(() => $("#loginPin").focus(), 30);
}

async function afterUnlock() {
  try {
    State.settings = await api("/settings");
    applyTheme();
    $("#loginModal").classList.add("hidden");
    await refresh();
  } catch {
    showLogin();
  }
}


async function refreshManagerVersion() {
  const el = $("#managerVersion");
  if (!el) return;
  try {
    const info = await api("/api/info");
    el.textContent = info?.version ? `v${info.version}` : "Version unknown";
  } catch {
    el.textContent = "Version unknown";
  }
}

async function initialise() {
  document.documentElement.dataset.theme = localStorage.theme || "dark";
  await refreshManagerVersion();
  try {
    const status = await api("/auth/status");
    if (status.authenticated) await afterUnlock();
    else showLogin();
  } catch {
    showLogin();
  }
}

/* ------------------------------------------------------------------ *
 * Wiring
 * ------------------------------------------------------------------ */

$("#ripForm").onsubmit = submitIntake;
$("#mediaType").onchange = () => { toggleTvFields(); updateFolderPreview(); };
["mediaTitle", "mediaYear", "mediaSeason", "mediaDisc", "movieDisc", "mediaCreator", "mediaNarrator", "audioDisc"].forEach((id) => {
  $("#" + id).oninput = updateFolderPreview;
});


async function testMetadataProvider(provider) {
  const box = $("#metadataTestResult"); if (box) box.textContent = `Testing ${provider}…`;
  // Save the draft first so the backend test/lookup uses the entered credentials.
  try {
    await api("/settings", { method: "PUT", body: JSON.stringify(State.draft) });
    let code = provider === "google_books" ? "9780140328721" : "0885909950805";
    const result = await api(`/lookup/upc/${code}`);
    const used = (result.matches || []).some(x => String(x.source || "").toLowerCase().replace(" ","_").includes(provider.replace("_books","")));
    if (box) box.textContent = (result.matches || []).length ? `✓ Provider responded; ${result.matches.length} metadata result(s) returned.` : `Provider responded, but the sample had no match. ${esc((result.errors || []).join(" · "))}`;
  } catch (error) { if (box) box.textContent = `✕ ${error.message}`; }
}

$("#lookupButton").onclick = lookupBarcode;
$("#skipUpc").onclick = () => { showDetailsStep(); $("#mediaTitle").focus(); };
$("#rescanUpc").onclick = showBarcodeStep;

$("#upcInput").onkeydown = (event) => {
  // Barcode scanners finish with Enter.
  if (event.key === "Enter") { event.preventDefault(); lookupBarcode(); }
};

$("#intakeModal").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button) return;
  const { nodeId, drive } = State.intake;
  if (button.dataset.tray === "close") { closeTray(nodeId, drive); return; }
  if (button.dataset.tray === "eject") { ejectDrive(nodeId, drive); return; }
  if (button.dataset.action === "stop-waiting") { stopWaiting(nodeId, drive); return; }
  if (button.dataset.action === "close-intake") closeModal("intakeModal");
});

$("#sharesButton").onclick = showShares;
$("#statsButton").onclick = showStats;
$("#settingsButton").onclick = openSettings;
$("#refreshButton").onclick = () => refresh(true);

const mobileNavToggle = $("#mobileNavToggle");
const mobileNavMenu = $("#mobileNavMenu");
const mobileNavBackdrop = $("#mobileNavBackdrop");

function setMobileNav(open) {
  mobileNavMenu.classList.toggle("hidden", !open);
  mobileNavBackdrop.classList.toggle("hidden", !open);
  mobileNavToggle.setAttribute("aria-expanded", String(open));
  mobileNavToggle.setAttribute("aria-label", open ? "Close menu" : "Open menu");
  mobileNavToggle.textContent = open ? "×" : "☰";
}

mobileNavToggle.onclick = (event) => {
  event.stopPropagation();
  setMobileNav(mobileNavToggle.getAttribute("aria-expanded") !== "true");
};

mobileNavMenu.onclick = (event) => {
  const button = event.target.closest("[data-mobile-action]");
  if (!button) return;
  setMobileNav(false);
  const actions = {
    refresh: () => refresh(true),
    shares: showShares,
    stats: showStats,
    settings: openSettings,
  };
  actions[button.dataset.mobileAction]?.();
};

document.addEventListener("click", (event) => {
  if (!event.target.closest(".mobile-nav-wrap")) setMobileNav(false);
});

document.querySelectorAll("[data-close]").forEach((button) => {
  button.onclick = () => closeModal(button.dataset.close);
});
document.querySelectorAll(".modal-backdrop:not(#loginModal)").forEach((backdrop) => {
  backdrop.onclick = (event) => { if (event.target === backdrop) closeModal(backdrop.id); };
});

$("#overlay").onclick = (event) => { if (event.target === $("#overlay")) cancelSettings(); };
$("#settingsClose").onclick = cancelSettings;
$("#settingsBackHeader").onclick = settingsBack;

$("#settingsContent").addEventListener("click", (event) => {
  const button = event.target.closest("button");
  if (!button || !State.draft) return;

  if (button.dataset.helpTopic) { openSettingsHelp(button.dataset.helpTopic); return; }
  if (button.dataset.settingsPage) { navigateSettings(button.dataset.settingsPage); return; }
  if (button.dataset.hardwareNode !== undefined) { navigateSettings("hardware-node",{args:[Number(button.dataset.hardwareNode)]}); return; }
  if (button.dataset.removeNode !== undefined) { beginRemoveNode(Number(button.dataset.removeNode)); return; }
  if (button.dataset.providerPage) { navigateSettings("provider",{args:[button.dataset.providerPage]}); return; }

  switch (button.dataset.settingsAction) {
    case "home": renderSettingsHome(); return;
    case "back": settingsBack(); return;
    case "cancel": cancelSettings(); return;
    case "save": saveSettings(); return;
    case "lock": lockNow(); return;
    case "test-node-ssh": testNodeSsh(); return;
    case "test-existing-node": testExistingNode(); return;
    case "install-node": installNode(); return;
    case "pair-existing-node": pairExistingNode(); return;
    case "add-simulator": addSimulator(); return;
    case "toggle-simulator": toggleSimulator(); return;
    case "reset-simulator": resetSimulator(); return;
    case "run-diagnostics": runDiagnostics(); return;
    case "restart-manager": restartManager(); return;
    case "test-metadata": testMetadataProvider(button.dataset.provider); return;
    default: break;
  }

  if (button.dataset.layoutStep) {
    const [dimension,delta]=button.dataset.layoutStep.split(":");
    stepDraftLayout(dimension,Number(delta));
    return;
  }

  switch ("__continue__") {
    default: break;
  }

  if (button.dataset.toggleKey) {
    toggleDraft(button.dataset.toggleKey, button); updateDirtySaveButtons();
    const title=$("#drawerTitle")?.textContent||"";
    if(title==="Ripping")rippingPage();
    if(title==="Google Books")providerPage("google_books");
    if(title==="UPCitemdb")providerPage("upcitemdb");
    if(title==="OMDb")providerPage("omdb");
    return;
  }
  if (button.dataset.driveId) {
    const preference = drivePreference(button.dataset.driveId);
    const key = button.dataset.driveKey;
    preference[key] = !preference[key];
    setToggle(button, preference[key]);
    updateDirtySaveButtons();
    return;
  }
  if (button.dataset.nodeToggle !== undefined) {
    const index = Number(button.dataset.nodeToggle);
    State.draft.nodes[index].enabled = !State.draft.nodes[index].enabled;
    setToggle(button, State.draft.nodes[index].enabled); updateDirtySaveButtons();
  }
});

$("#settingsContent").addEventListener("change", (event) => {
  const input=event.target;
  if(!State.draft)return;
  if(input.dataset.layoutSize){resizeDraftLayout(input.dataset.layoutSize,input.value);return;}
  if(input.dataset.layoutCell!==undefined){assignDraftLayoutCell(input.dataset.layoutCell,input.value);}
});

$("#settingsContent").addEventListener("input", (event) => {
  const input = event.target;
  if (!State.draft) return;

  if (input.dataset.settingInput) {
    const key = input.dataset.settingInput;
    State.draft[key] = ["number", "range"].includes(input.type) ? Number(input.value) : input.value;
    updateDirtySaveButtons();
    if(key==="dashboard_spacing_percent"){
      const label=$("#spacingValue"); if(label) label.textContent=`${input.value}%`;
      const preview=document.querySelector(".layout-preview");
      if(preview) preview.style.setProperty("--layout-preview-gap",`${Math.round(7*Number(input.value)/100)}px`);
    }
    if(key==="metadata_upcitemdb_mode")providerPage("upcitemdb");
  }
  if (input.dataset.nodeField !== undefined) {
    State.draft.nodes[Number(input.dataset.nodeIndex)][input.dataset.nodeField] = input.value; updateDirtySaveButtons();
  }
});

$("#loginForm").onsubmit = async (event) => {
  event.preventDefault();
  const error = $("#loginError");
  const pin = $("#loginPin").value;
  error.textContent = "";

  if (!/^\d{4,8}$/.test(pin)) { error.textContent = "Enter your 4–8 digit PIN"; return; }
  try {
    await api("/auth/login", { method: "POST", body: JSON.stringify({ pin }) });
    await afterUnlock();
  } catch (failure) {
    error.textContent = failure.message;
    $("#loginPin").select();
  }
};

function enterPinKey(key) {
  const input=$("#loginPin"); if(!input)return;
  if(/^\d$/.test(key) && input.value.length<8) input.value+=key;
  else if(key==="backspace") input.value=input.value.slice(0,-1);
  else if(key==="clear") input.value="";
  input.focus({preventScroll:true});
}

$("#loginModal").addEventListener("click",event=>{
  const button=event.target.closest("[data-pin-key]"); if(!button)return;
  enterPinKey(button.dataset.pinKey);
});

document.addEventListener("keydown",event=>{
  if($("#loginModal").classList.contains("hidden"))return;
  if(/^\d$/.test(event.key)){event.preventDefault();enterPinKey(event.key);return;}
  if(event.key==="Backspace"){event.preventDefault();enterPinKey("backspace");return;}
  if(event.key==="Delete"){event.preventDefault();enterPinKey("clear");return;}
  if(event.key==="Enter"){event.preventDefault();$("#loginForm").requestSubmit();}
});

$("#loginPin").addEventListener("pointerdown",event=>{
  event.preventDefault(); $("#loginPin").focus({preventScroll:true});
});

document.addEventListener("keydown", (event) => {
  if (event.key !== "Escape") return;
  if (mobileNavToggle.getAttribute("aria-expanded") === "true") { setMobileNav(false); mobileNavToggle.focus(); return; }
  if (!$("#overlay").classList.contains("hidden")) { cancelSettings(); return; }
  if (!$("#genericModal").classList.contains("hidden")) { closeModal("genericModal"); return; }
  if (!$("#intakeModal").classList.contains("hidden")) closeModal("intakeModal");
});

// The barcode field must not summon the on-screen keyboard on a phone.
document.addEventListener("pointerdown", (event) => {
  if (event.target?.id === "upcInput") { event.preventDefault(); focusScanner(); }
}, { passive: false });

// Polling a phone that is in someone's pocket helps nobody. Stop while the tab
// is hidden and catch up the moment it comes back.
document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    clearTimeout(State.refreshTimer);
    return;
  }
  const locked = !$("#loginModal").classList.contains("hidden");
  if (!locked) refresh();
});

initialise();
setInterval(updateRelativeTimes, 1000);
