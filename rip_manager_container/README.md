# Rip Manager v0.18.8

Release date: 25 August 2026  
Bundled Rip Node API: v0.2.9

Rip Manager controls optical-disc ripping nodes from one mobile-friendly web
interface. This package also includes a built-in simulator, the real Rip Node
software, installation tools, manual GitHub updates and safe Manager rollback.

## What is new in v0.18.8

- Bundled Node API v0.2.9 works around USB optical bridges that incorrectly
  report a physically closed, loaded tray as open or empty.
- Suspicious negative tray states are checked against real block-device media
  capacity. A readable disc is reported as loaded and prepared rips can leave
  **Waiting for disc** after the normal silent delay.
- Rip startup uses the same verified media result, preventing a false raw tray
  status from rejecting a disc immediately after Manager detects it.
- **Supports Close** remains enforced end-to-end. When disabled, the Close
  control is hidden and Manager rejects close requests; waiting intake never
  sends an automatic close command.

## What is new in v0.18.7

- **Hardware → Manage node → Storage location** reads and changes the real
  output directory used by that Node API.
- Node API v0.2.8 persists the path under the node user's configuration,
  requires an existing writable absolute directory, performs a write test and
  refuses changes during active rips.
- The Simulator is no longer included in **Output Shares**, because it does not
  provide a real SMB storage share.

## What is new in v0.18.6

- Opening a drive always presents an empty, focused barcode scanner field. A
  previous job or waiting disc can no longer refill it automatically.
- Loaded media without a readable filesystem label explicitly displays
  **Disc loaded · label unavailable** rather than leaving an unexplained gap.

## What is new in v0.18.5

- The barcode scanner field now has its own large **Clear** button, so kiosk
  users do not need an onscreen keyboard or Delete key.
- Clearing removes the visible value and pending barcode, resets the scanner
  prompt and returns focus ready for the next scan.
- On narrow phones the scanner field remains full width with **Lookup** and
  **Clear** arranged beneath it.

## What is new in v0.18.4

- Dashboard drive assignment now waits for the longer optical-hardware timeout
  instead of retaining a stale partial cache when drive probing exceeds eight
  seconds. All mapped drives are then available in every tile selector.
- Native Chrome/Windows option lists have explicit foreground and background
  colours, preventing pale option text on a white dropdown.
- Every real node card has a **Terminal** button which opens that node's Cockpit
  console on HTTPS port 9090. Keep Cockpit LAN/VPN-only.
- Bundled Node API remains v0.2.7 and installation remains manual-only.

## What is new in v0.18.3

- Drive Mapping forms now remain inside the Settings drawer at every browser
  width. Selectors and buttons stack cleanly without horizontal scrolling.
- Bundled Node API v0.2.7 explicitly refreshes udev and scans both `/dev` and
  sysfs, so newly connected optical drives appear after pressing **Refresh**.
- Node update acknowledgements are parsed defensively. A successful install is
  no longer reported as failed because an older shell updater returned HTTP 422.
- **Settings → System → Restart Rip Manager** provides a confirmed restart.
  Rip jobs continue on their independent Nodes while Manager restarts.
- The Simulator update row is hidden when simulation is disabled.
- Update installation remains manual-only.

## What is new in v0.18.2

- Fixed **Auto-assign detected drives** returning Internal Server Error when
  the API service user could not save `/opt/rip-node/drive-map.json`.
- Bundled Rip Node API v0.2.6 stores mappings under the node user's writable
  `~/.config/rip-node/drive-map.json` path.
- A readable legacy mapping is loaded automatically, so existing assignments
  remain available and are written to the new location on the next change.
- Fresh-node adoption now gives the node user ownership of `/opt/rip-node`
  and installs the API file with the correct user and group.
- Existing adopted nodes can receive v0.2.6 through the normal manual Node
  update; Ubuntu does not need reinstalling and the node must not be deleted.

## What was new in v0.18.1

- Fresh-node adoption waits up to 90 seconds for the local Node API health
  endpoint after systemd launches the service.
- Manager then retries authenticated `/api/info` verification for a further
  30 seconds before reporting a connection failure.
- This prevents fast verification from racing Uvicorn while it is still
  binding port 8000 on a small node.
- Genuine startup failures include recent systemd and journal diagnostics in
  the progress result.
- Retrying with the same Node ID updates the stale API token left by an
  interrupted adoption and clears repeated `401 Unauthorized` polling errors.

## What was new in v0.18.0

- **Install & Adopt Node** now starts a background installation job, so a long
  Ubuntu package operation cannot make the browser request appear to stop.
- The installer displays named stages, percentage, elapsed time, the current
  action and a scrollable stage history.
- Progress resumes when the adoption page is reopened in the same browser tab.
- Failures stay visible with their exact stage and plain-English error. The
  operator can correct the problem and safely retry the idempotent installer.
- Missing bundled API files and SFTP upload errors are reported explicitly.
- SSH, sudo and NAS credentials remain in memory only for the running job and
  are cleared immediately when it finishes.

## What was new in v0.17.9

- A node disconnect now cancels its silent prepared-drive timer. Reconnecting
  cannot start a rip using time accumulated while the node was offline.
- This completes the v0.17.8 prepared-drive, capability and disc-state fixes.

## What was new in v0.17.8

- **Prepare Drive** now saves the details and opens an empty tray automatically
  when that drive supports Open / Eject.
- After the operator inserts the disc and manually closes the tray, Manager
  waits silently for 15 seconds after positive disc detection, then starts the
  rip. There is no visible countdown and Manager never closes the tray.
- Per-drive **Supports Open / Eject** and **Supports Close** capability switches
  are restored under **Settings → Hardware → Drives**. Unsupported controls are
  hidden and the settings persist across restarts and updates.
- Ejecting a failed, cancelled or completed disc detaches its old job from the
  live drive tile. A replacement disc starts cleanly as **Disc detected**.
- Simulator replacement discs receive a fresh disc-cycle identity and cannot
  inherit **Complete** or **Failed** from the previous disc.
- The simulator retains the intended 80% insertion chance, 10% rip-failure
  chance and accelerated demonstration speed.

## What was new in v0.17.7

- Closing a simulator tray now starts a fresh disc cycle and cannot inherit a
  completed or failed job from the previous disc.
- A closed simulator tray contains a disc 80% of the time. Every newly inserted
  disc begins at **Disk detected**.
- Simulator rips now succeed 90% of the time and fail 10% of the time with a
  simulated read error during progress.
- Fake rip progress is approximately five-and-a-half times faster, making a
  full successful demonstration take roughly 100 seconds before verification.
- These probabilities and speed changes apply only to the built-in Simulator;
  real Rip Nodes and real ripping speeds are unchanged.

## What was new in v0.17.6

- Nested Settings pages now use one compact header: **Back** on the left, the
  page title centred, and **Close** on the right. The oversized second Back
  button and the empty space beneath it have been removed.
- Real nodes can be removed from **Settings → Hardware → Manage node**. Removal
  uses two confirmation stages and requires the exact friendly node name.
- Node removal is blocked while a rip is starting, ripping, verifying or being
  cancelled. The built-in Simulator cannot be removed.
- Removing a node clears its live cache, waiting intake, dashboard positions
  and drive preferences, but preserves completed job history and files.
- Simulator drives are now named **BR1**, **BR2**, and **DVD1–DVD4**. Existing
  Simulator dashboard positions and preferences migrate automatically once.

## What was new in v0.17.5

- Fixed a later mobile Settings rule that overrode the information-icon size
  and stretched each **i** into a tall pill.
- Information icons now use the exact compact **ⓘ** glyph with no visible button
  chrome and are isolated from shared application button rules.
- Icons remain 18 px, or 17 px beside a setting label, without changing the
  surrounding heading or card layout.
- Help popups remain above the Settings drawer.
- Browser asset versions match v0.17.5, preventing the broken v0.17.4 CSS from
  remaining in the browser cache.

## What was new in v0.17.4

- Settings help controls are now small circular **i** icons instead of
  full-sized application buttons.
- Heading icons are positioned independently, so they no longer change heading,
  row or card dimensions on mobile.
- Information popups now open above the Settings drawer and all normal interface
  layers.
- Keyboard focus, screen-reader labels and the existing plain-English help are
  preserved.
- Browser asset versions match v0.17.4, preventing an older cached interface.

## What was new in v0.17.3

- On screens up to 600 px wide, the crowded header actions are replaced by one
  hamburger menu in the top-right corner.
- The popup keeps Refresh, Shares, Stats and Settings, with every option aligned
  to the right and large enough to use comfortably on a phone.
- The menu closes after choosing an option, tapping elsewhere, or pressing
  Escape. Desktop and tablet headers above 600 px are unchanged.
- Browser asset versions match v0.17.3, preventing an older cached interface.

## What was new in v0.17.2

- Settings that need explanation now have a circular **i** information button.
- Each panel explains what the setting does, when to use it, the recommended
  choice, what it affects, common problems and how to undo it.
- Help is written in plain English and works on mobile, keyboard and screen
  readers. It does not save or discard unfinished changes.
- This README is now a complete installation and operating guide.
- Browser asset versions match v0.17.2, preventing an older cached interface.

## Implementation notes

The help system uses one reusable accessible control and shared help panel.
Topics are attached only where setup or safety guidance is useful; obvious
controls such as Theme and Volume remain uncluttered. Help covers verification,
auto-eject, video defaults, layout, node connections, drive mapping, simulator,
barcodes, providers, API keys, Ubuntu installation, NAS storage, network
addresses, polling, API tools, updates, rollback and PIN protection.

## Upgrade an existing Manager

1. Publish this ZIP as the newest release asset in the Rip Manager repository.
2. Open **Settings → System → Updates** and press **Check now**.
3. Confirm v0.18.8 is available. Do not update during an active rip.
4. Press **Install update** once. Manager briefly restarts.
5. Refresh after it returns and confirm v0.18.8 in Settings.

The update preserves the database, settings, PIN, nodes, layout and job history.

If Updates says the host updater does not support rollback, run this once on
Byte-Me:

```bash
bash /mnt/user/appdata/rip-manager/rip_manager_container/unraid/install-rip-github-updater.sh
```

## Fresh Manager installation

1. Extract the ZIP into the persistent Rip Manager project directory.
2. Keep `app`, `node`, `unraid`, `docker-compose.yml`, `Dockerfile`,
   `simulator_node.py` and this README together.
3. Start the supplied compose project or Unraid template.
4. Open the published Manager web address.
5. Set a PIN under **Settings → Security** if required.
6. Use the simulator, or add a real node under **Settings → Hardware**.

## Add a real Rip Node

### Install on fresh Ubuntu

Use this for a clean Ubuntu machine or VM. Give it a fixed LAN address first,
then open **Hardware → Add Rip Node → Install on fresh Ubuntu**. Manager uses
SSH once to install the bundled API and services. It can configure an SMB/NAS
mount. SSH and NAS passwords are not stored in Manager.

### Connect an existing Node API

Use this when the Node API is already installed. Enter a unique ID, friendly
name, API URL and optional token. Test the connection before adding it. No SSH
or storage settings are changed.

## Built-in Simulator

The simulator runs inside Manager with two Blu-ray and four DVD drives. It has
tray controls, random discs, moving progress, elapsed time, ETA, verification,
completion, failure handling and realistic statistics.

Closing a tray has an 80% chance of inserting a fake disc. A successful job
rips, verifies for three seconds, completes, waits three seconds and ejects if
auto-eject is enabled.

Barcode handling is real, not faked. Scan a genuine UPC/EAN/ISBN through the
normal intake screen for live lookup, or choose Skip. The simulator never uses
physical drives, a NAS or another computer. Leave it off for normal use; its
controls are at the bottom of **Settings → Hardware**.

## Barcode and metadata

The barcode field accepts a scanner ending with Enter or typed text. Results
remain editable and never start a rip automatically.

- MusicBrainz: music releases and artists.
- Google Books: books and audiobooks; key optional.
- UPCitemdb: retail UPC/EAN data; paid access may require a key.
- OMDb: exact movie/TV title and year enrichment; key required.

Results depend on third-party availability, limits and internet access. Never
include API keys in screenshots or support messages.

## Drive mapping

Open **Hardware → Manage drives**. Auto-assign is recommended and creates one
logical name per detected drive. Stable USB/udev paths are saved where possible
so `/dev/srX` changes should not rearrange tiles. Mapping changes are blocked
while an affected drive is ripping.

## Updates and rollback

Only the Manager repository is required. The real Node Python file is bundled
in this ZIP and served by Manager. The simulator is built in and is never
updated separately.

GitHub checks do not install automatically. **Install update** updates Manager;
**Install node update** sends the bundled Node version to real nodes.

Before Manager updates, the host creates a verified code-only backup and keeps
the five newest. Rollback preserves settings, PIN, nodes, database and history.
It makes a safety backup, restarts, checks health and automatically restores the
newer code if the chosen backup cannot start. Node rollback is not automatic.

## PIN lock

PINs contain 4–8 digits. The lock screen has an onscreen keypad, suppresses the
phone keyboard and accepts a physical keyboard or scanner. Enter submits the
PIN. The current PIN is required before replacing it.

## Settings map

| Path | Purpose |
| --- | --- |
| Settings → Ripping | Verification, eject, video defaults and sounds |
| Settings → Dashboard | Grid, drive placement, spacing and theme |
| Settings → Hardware | Nodes, drive mapping, Add Rip Node and simulator |
| Settings → Metadata | Barcode lookup, providers and API keys |
| Settings → System → Updates | Manager/Node releases and rollback |
| Settings → System → Advanced | Polling and API tools |
| Settings → System → Diagnostics | Manager, node and updater checks |
| Settings → Security | PIN and lock controls |

## Compatibility

- Manager: v0.18.8; bundled Node API: v0.2.9.
- Existing v0.16.x and v0.17.x Manager data is preserved.
- Drive mapping requires Node API v0.2.4 or newer.
- Real nodes should use the Manager-served updater.
- Rollback requires the persistent host updater from v0.17.1 or newer.

## Known limitations

- Metadata depends on third-party services, internet and rate limits.
- The simulator cannot test physical drives, NAS permissions or real engines.
- Manager rollback does not roll back Ubuntu or a real Node API.
- Fresh-node installation needs working SSH and sudo access.
- Service addresses must be reachable from the container or node using them.

## Troubleshooting

### Update remains queued

Check the persistent Unraid watcher and
`/mnt/user/Updater/Logs/github-updater.log`. A successful install ends with the
container being rebuilt, recreated and started.

### Node is offline

Confirm its fixed IP, API port and service. The URL must work from Manager. Run
**System → Diagnostics** and read the Connection information panel.

### Drives are missing or moved

Refresh Drive mapping and use Auto-assign. Check the powered USB connection if
Ubuntu does not detect the drive.

### Barcode lookup returns nothing

Enable barcode lookup and the correct provider, test its key and use manual
title entry if the provider has no match.

### New interface does not appear

Confirm Settings reports v0.18.8, then reload the page. Versioned asset URLs
prevent old interface files being reused.

## Checklist for future releases

1. Update Manager and browser asset versions together.
2. Update release date and bundled Node version here.
3. Add plain-English implementation notes explaining what changed and why.
4. Update upgrade steps, compatibility and limitations.
5. Add or revise circular **i** help for new settings needing instructions.
6. Test help with touch, keyboard, screen reader labels and narrow mobile width.
7. Verify Python/JavaScript syntax, package contents and updater filenames.
