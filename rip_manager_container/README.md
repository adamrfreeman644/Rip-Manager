# Rip Manager v0.17.3

Release date: 20 August 2026  
Bundled Rip Node API: v0.2.5

Rip Manager controls optical-disc ripping nodes from one mobile-friendly web
interface. This package also includes a built-in simulator, the real Rip Node
software, installation tools, manual GitHub updates and safe Manager rollback.

## What is new in v0.17.3

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
3. Confirm v0.17.3 is available. Do not update during an active rip.
4. Press **Install update** once. Manager briefly restarts.
5. Refresh after it returns and confirm v0.17.3 in Settings.

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

Closing a tray has a 50/50 chance of inserting a fake disc. A successful job
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

- Manager: v0.17.3; bundled Node API: v0.2.5.
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

Confirm Settings reports v0.17.3, then reload the page. Versioned asset URLs
prevent old interface files being reused.

## Checklist for future releases

1. Update Manager and browser asset versions together.
2. Update release date and bundled Node version here.
3. Add plain-English implementation notes explaining what changed and why.
4. Update upgrade steps, compatibility and limitations.
5. Add or revise circular **i** help for new settings needing instructions.
6. Test help with touch, keyboard, screen reader labels and narrow mobile width.
7. Verify Python/JavaScript syntax, package contents and updater filenames.
