/* Metadata match presentation enhancements.
 * Loaded after app.js so it can replace only the barcode-result renderer
 * without changing lookup, intake, ripping, node, or updater behaviour.
 */
(() => {
  const MEDIA_LABELS = {
    movie: "MOVIE",
    tv: "TV SERIES",
    music: "MUSIC",
    audiobook: "AUDIOBOOK",
  };

  function mediaLabel(match) {
    const type = String(match?.media_type || "").toLowerCase();
    return MEDIA_LABELS[type] || (type ? type.replaceAll("_", " ").toUpperCase() : "MEDIA");
  }

  function releaseLabel(match) {
    const value = match?.release_date || match?.published_date || match?.date;
    if (value) {
      const parsed = new Date(value);
      if (!Number.isNaN(parsed.getTime())) {
        return `Released ${parsed.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" })}`;
      }
    }
    return match?.year ? `Released ${match.year}` : "Release year unknown";
  }

  function googleUrl(match) {
    const title = match?.title || match?.raw_title || "";
    const type = String(match?.media_type || "media").toLowerCase();
    const typeHint = type === "tv" ? "TV series" : type === "music" ? "album" : type === "audiobook" ? "audiobook" : "movie";
    const terms = [`\"${title}\"`, match?.year || "", typeHint].filter(Boolean).join(" ");
    return `https://www.google.com/search?q=${encodeURIComponent(terms)}`;
  }

  const style = document.createElement("style");
  style.textContent = `
    .metadata-match-list{display:grid;gap:12px;margin:14px 0}
    .metadata-match-card{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:12px;align-items:stretch;border:1px solid var(--border,rgba(127,127,127,.28));border-radius:14px;padding:12px;background:var(--panel,rgba(127,127,127,.06))}
    .metadata-match-pick{appearance:none;border:0;background:transparent;color:inherit;text-align:left;padding:2px;cursor:pointer;min-width:0}
    .metadata-match-pick:hover strong{text-decoration:underline}
    .metadata-match-title{display:block;font-size:1.06rem;margin:7px 0 5px}
    .metadata-match-meta{display:flex;flex-wrap:wrap;gap:7px;align-items:center;color:var(--muted,#8b95a5);font-size:.88rem}
    .metadata-type-badge{display:inline-flex;align-items:center;width:max-content;border-radius:999px;padding:4px 8px;font-size:.72rem;font-weight:800;letter-spacing:.055em;background:var(--accent,#5d7cff);color:#fff}
    .metadata-release{font-weight:700;color:inherit}
    .metadata-source{display:block;margin-top:7px;color:var(--muted,#8b95a5);font-size:.78rem}
    .metadata-google{display:inline-flex;align-items:center;justify-content:center;align-self:center;min-height:42px;padding:0 13px;border-radius:10px;border:1px solid var(--border,rgba(127,127,127,.32));color:inherit;text-decoration:none;font-weight:700;white-space:nowrap;background:transparent}
    .metadata-google:hover{background:rgba(127,127,127,.1)}
    @media(max-width:620px){.metadata-match-card{grid-template-columns:1fr}.metadata-google{width:100%}}
  `;
  document.head.appendChild(style);

  showBarcodeMatches = function showEnhancedBarcodeMatches(matches) {
    const rows = matches.map((match, index) => {
      const title = match.title || match.raw_title || "Untitled";
      const extras = [];
      if (match.season) extras.push(`Season ${match.season}`);
      if (match.creator) extras.push(match.creator);
      const confidence = match.confidence != null ? ` · ${match.confidence}%` : "";
      return `<div class="metadata-match-card">
        <button class="metadata-match-pick" type="button" data-barcode-match="${index}" aria-label="Use ${esc(title)}">
          <span class="metadata-type-badge">${esc(mediaLabel(match))}</span>
          <strong class="metadata-match-title">${esc(title)}</strong>
          <span class="metadata-match-meta"><span class="metadata-release">${esc(releaseLabel(match))}</span>${extras.map(x => `<span>· ${esc(x)}</span>`).join("")}</span>
          <small class="metadata-source">${esc(match.source || "Metadata result")}${esc(confidence)}</small>
        </button>
        <a class="metadata-google" href="${esc(googleUrl(match))}" target="_blank" rel="noopener noreferrer" aria-label="Google ${esc(title)}">Google this ↗</a>
      </div>`;
    }).join("");

    $("#genericModalTitle").textContent = "Choose the correct media";
    $("#genericModalBody").innerHTML = `<div class="note"><strong>Check the media type and release year.</strong> Choose the closest result, or use Google this to verify it first. Google does not change any Rip Manager metadata.</div>
      <div class="metadata-match-list">${rows}</div>
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
  };
})();
