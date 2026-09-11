/* Rip Manager metadata lookup presentation enhancements.
 * Loaded after app.js so the existing barcode lookup keeps its API behaviour
 * while results are made easier to verify before a rip starts.
 */

function setRipManagerBrowserTheme() {
  let theme = document.querySelector('meta[name="theme-color"]');
  if (!theme) {
    theme = document.createElement("meta");
    theme.name = "theme-color";
    document.head.appendChild(theme);
  }
  theme.content = "#000000";

  let tile = document.querySelector('meta[name="msapplication-TileColor"]');
  if (!tile) {
    tile = document.createElement("meta");
    tile.name = "msapplication-TileColor";
    document.head.appendChild(tile);
  }
  tile.content = "#000000";

  let apple = document.querySelector('meta[name="apple-mobile-web-app-status-bar-style"]');
  if (!apple) {
    apple = document.createElement("meta");
    apple.name = "apple-mobile-web-app-status-bar-style";
    document.head.appendChild(apple);
  }
  apple.content = "black";
}

function lookupMediaLabel(value) {
  const labels = {
    movie: "MOVIE",
    tv: "TV SERIES",
    music: "MUSIC",
    audiobook: "AUDIOBOOK",
    book: "BOOK",
  };
  const key = String(value || "").trim().toLowerCase();
  return labels[key] || (key ? key.replaceAll("_", " ").toUpperCase() : "MEDIA");
}

function lookupMediaSearchTerm(value) {
  const terms = {
    movie: "movie",
    tv: "TV series",
    music: "album",
    audiobook: "audiobook",
    book: "book",
  };
  const key = String(value || "").trim().toLowerCase();
  return terms[key] || key || "media";
}

function lookupReleaseText(match) {
  const fullDate = match.release_date || match.released || match.published_date || match.first_air_date;
  if (fullDate) {
    const parsed = new Date(fullDate);
    if (!Number.isNaN(parsed.getTime())) {
      return parsed.toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" });
    }
    return String(fullDate);
  }
  return match.year ? String(match.year) : "Release date unknown";
}

function lookupGoogleUrl(match) {
  const title = match.title || match.raw_title || "";
  const bits = [title];
  if (match.year) bits.push(String(match.year));
  bits.push(lookupMediaSearchTerm(match.media_type));
  return `https://www.google.com/search?q=${encodeURIComponent(bits.filter(Boolean).join(" "))}`;
}

function lookupResultHeading(matches) {
  const types = [...new Set(matches.map((match) => String(match.media_type || "").toLowerCase()).filter(Boolean))];
  if (types.length === 1) {
    const label = lookupMediaLabel(types[0]).toLowerCase();
    return `${label.charAt(0).toUpperCase()}${label.slice(1)} matches found`;
  }
  return "Media matches found";
}

function installLookupEnhancementStyles() {
  if (document.getElementById("lookupEnhancementStyles")) return;
  const style = document.createElement("style");
  style.id = "lookupEnhancementStyles";
  style.textContent = `
    .barcode-match-list.lookup-enhanced { display:grid; gap:10px; margin:12px 0; }
    .lookup-result { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:10px; align-items:stretch; }
    .lookup-result-select { width:100%; text-align:left; min-width:0; }
    .lookup-result-main { display:flex; flex-direction:column; gap:7px; min-width:0; }
    .lookup-result-title { font-size:1rem; line-height:1.25; }
    .lookup-result-meta { display:flex; flex-wrap:wrap; align-items:center; gap:7px; }
    .lookup-media-badge { display:inline-flex; align-items:center; padding:4px 8px; border-radius:999px; font-size:.72rem; font-weight:800; letter-spacing:.06em; border:1px solid currentColor; }
    .lookup-release { font-weight:700; }
    .lookup-result-extra { opacity:.75; font-size:.82rem; }
    .lookup-result-actions { display:flex; flex-direction:column; gap:6px; min-width:112px; }
    .lookup-result-google { display:flex; align-items:center; justify-content:center; min-height:42px; padding:8px 10px; border-radius:10px; text-decoration:none; font-weight:750; border:1px solid var(--border, rgba(127,127,127,.35)); background:var(--panel, rgba(127,127,127,.08)); color:inherit; white-space:nowrap; }
    .lookup-result-google:hover { filter:brightness(1.08); }
    .lookup-result-source { text-align:center; font-size:.74rem; opacity:.68; line-height:1.2; }
    .lookup-type-callout { margin:10px 0 4px; padding:10px 12px; border-radius:10px; background:var(--panel, rgba(127,127,127,.08)); font-weight:700; }
    @media (max-width:620px) {
      .lookup-result { grid-template-columns:1fr; }
      .lookup-result-actions { flex-direction:row; align-items:center; min-width:0; }
      .lookup-result-google { flex:1; }
      .lookup-result-source { flex:1; text-align:right; }
    }
  `;
  document.head.appendChild(style);
}

function showBarcodeMatches(matches) {
  installLookupEnhancementStyles();

  const rows = matches.map((match, index) => {
    const title = match.title || match.raw_title || "Untitled";
    const typeLabel = lookupMediaLabel(match.media_type);
    const release = lookupReleaseText(match);
    const extra = [
      match.season ? `Season ${match.season}` : "",
      match.creator || "",
    ].filter(Boolean).join(" · ");
    const confidence = match.confidence != null && match.confidence !== "" ? `${match.confidence}%` : "";
    const source = [match.source || "", confidence].filter(Boolean).join(" · ");

    return `<div class="lookup-result">
      <button class="barcode-match lookup-result-select" type="button" data-barcode-match="${index}">
        <span class="lookup-result-main">
          <strong class="lookup-result-title">${esc(title)}</strong>
          <span class="lookup-result-meta">
            <span class="lookup-media-badge">${esc(typeLabel)}</span>
            <span class="lookup-release">${esc(release)}</span>
          </span>
          ${extra ? `<span class="lookup-result-extra">${esc(extra)}</span>` : ""}
        </span>
      </button>
      <div class="lookup-result-actions">
        <a class="lookup-result-google" href="${esc(lookupGoogleUrl(match))}" target="_blank" rel="noopener noreferrer" aria-label="Google ${esc(title)}">Google this</a>
        ${source ? `<span class="lookup-result-source">${esc(source)}</span>` : ""}
      </div>
    </div>`;
  }).join("");

  $("#genericModalTitle").textContent = lookupResultHeading(matches);
  $("#genericModalBody").innerHTML = `<div class="note">Confirm the media type and release date before choosing a result. Google this opens a separate verification search and does not change any metadata.</div>
    <div class="lookup-type-callout">Media type is shown prominently on every match.</div>
    <div class="barcode-match-list lookup-enhanced">${rows}</div>
    <button class="secondary full-button" type="button" id="barcodeManual">None of these — enter manually</button>`;
  $("#genericModal").classList.remove("hidden");

  $("#genericModalBody").querySelectorAll("[data-barcode-match]").forEach((button) => {
    button.onclick = () => {
      const match = matches[Number(button.dataset.barcodeMatch)];
      fillDetails(match);
      closeModal("genericModal");
      showDetailsStep();
      $("#lookupStatus").textContent = `${lookupMediaLabel(match.media_type)} found${match.year ? ` · ${match.year}` : ""}`;
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

setRipManagerBrowserTheme();
installLookupEnhancementStyles();
