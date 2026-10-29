# Local Mac AI organiser

This adds `/local-ai` to Rip Manager. It talks to Ollama at `http://192.168.1.167:11434` using `adam-local:latest`. It uses the existing controller PIN session; unlock the main Manager page before opening the organiser. No additional account or cloud API key is required.

## Setup after installing this branch

1. Use the normal AD53 updater after the change is merged and released. No changes to Rip Nodes are needed.
2. Keep the Mac awake and Ollama running. Reserve the Mac's IP in UniFi so it stays stable.
3. Open **Local AI organiser** from the link above the Manager header, or `/local-ai` on the Manager address.
4. Save the Mac address and model, then press **Test Mac connection**.
5. Select one archived disc, create a preview, inspect the renames, and apply it. **Undo file changes** reverses the recorded renames if the files have not since changed.
6. Enable **Check up to 20 unprocessed disc folders every seven days**. The first pass starts within a minute; subsequent passes start no more often than every seven days. Missed passes wait until the Manager is running. This is separate from Librarium's Sunday schedules.
7. Enable **Automatically apply evidence-backed plans** if desired. Ambiguous plans remain available for review. Failed/unavailable Mac connections are recorded in the last-run status, with the next automatic attempt seven days later.

## What it does

The Mac receives filenames, title, recorded season/disc, sizes and durations. It receives no video bytes. AI proposes feature/episode/extra/unknown roles; application code constructs paths within each existing archived folder. Existing source data and file-change records are retained.

Automatic eligibility is deliberately independent of model confidence:
- Explicit `SxxExx` numbers are preserved in TV names and Season folders.
- Clearly named extras can be moved under `extras` without inventing a content title.
- A single movie video at least 45 minutes long can receive the recorded title and year.

Multiple unknown movie tracks, unidentified TV episodes, conflicts and files with paired sidecars require review or further identification. The organiser does not watch video, infer episode identities from track order, deduplicate, delete media, or move titles between library roots. Music/audiobook tracks remain handled by existing workflows. Plans are limited to 20 videos per disc so they fit the small local model; large sets require smaller disc folders. The weekly pass processes at most 20 previously unprocessed folders, so existing backlog progresses over subsequent weeks.

Renames use hard links followed by unlinking the old name, keeping media bytes unchanged and refusing overwrites. The archive filesystem must support hard links within each disc folder. Unsupported filesystems stop the plan and leave the source available. A durable journal is written before each move under `/data/local-ai`. A crash between link and unlink can leave both names; this is reported for manual recovery rather than deleting an unverified copy. Never clear `/data/local-ai` while a plan needs undo or recovery.

## Deployment status

Source and tests are prepared separately from the live installation. Until the branch is installed on Byte-Me, the live Manager has none of these new organiser controls. The existing verified Mover is unchanged.

## Validation

21 new tests cover byte-preserving apply/undo, stale previews, controller authentication, refusal of overwrite/link escapes, subtitle pairing, ambiguous tracks, explicit episode numbering, cut labels, model path injection and weekly idempotence. The full suite on this Mac reports 163 passes and five failures; the untouched base reports 142 passes with the identical five failures (case-insensitive audiobook directory tests and inability to write container mounts). Linux CI remains the deployment gate. JavaScript syntax and existing confirmation/action tests pass. A synthetic three-file request was exercised on the real Mac model; it misclassified a labelled trailer, which is why explicit filename evidence overrides model role guesses. No live archive files were used.
