/* Rip Manager 0.20.13 active title metadata fix.
 * Keep Manager-owned disc metadata attached to a live rip when the node
 * reports a different transient job id, without carrying completed metadata
 * onto a later disc.
 */
(function () {
  function historyMatchesLive(history, live) {
    if (!history || !live) return false;

    if (history.node_job_id && live.id && history.node_job_id === live.id) {
      return true;
    }

    if (!isActive(history) || !isActive(live)) {
      return false;
    }

    const historyStarted = Number(history.started_at);
    const liveStarted = Number(live.started_at);
    if (Number.isFinite(historyStarted) && Number.isFinite(liveStarted)) {
      return Math.abs(historyStarted - liveStarted) <= 300;
    }

    return true;
  }

  function mergeHistoryMetadata(history, live) {
    return {
      ...history,
      ...live,
      title: history.title,
      year: history.year,
      season: history.season,
      disc: history.disc,
      barcode: history.barcode,
      media_type: history.media_type,
      creator: history.creator,
      narrator: history.narrator,
      raw: live,
    };
  }

  jobFor = function (drive) {
    const history = State.jobs.find((job) =>
      job.node_id === drive.node_id && job.drive === drive.name);
    const live = drive.active_job;

    if (live && history && historyMatchesLive(history, live)) {
      return mergeHistoryMetadata(history, live);
    }
    if (live) return live;
    return history;
  };

  // Force the next polling pass to repaint current tiles with recovered titles.
  State.gridSignature = "";
})();
