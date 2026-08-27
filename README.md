# Rip Manager v0.20.0

Remote controller and update system for Rip Manager and its Rip Nodes.

## Security model

Rip Manager is deliberately **not** a conventional user/account service. It does not use Authentik, usernames, email login, registration, user profiles or email password recovery. Its normal interface remains a lightweight appliance/controller protected by the existing local PIN when controller protection is enabled.

The controller PIN is never stored in plaintext. It is salted and hashed server-side with PBKDF2-HMAC-SHA256. Successful unlocks use random application session tokens; only a SHA-256 hash of each session token is stored in SQLite. Repeated incorrect PIN attempts are throttled. Changing/resetting the PIN revokes existing controller sessions.

Settings → security/status can report only:

- controller protection enabled/disabled
- whether a PIN is configured
- whether configured Rip Nodes have API authentication

It never displays PINs, PIN hashes or node API tokens.

## Changing the controller PIN

Use the normal Settings interface. Once a PIN exists, the current PIN is required before a replacement is accepted. PINs are 4–8 digits. Disabling the controller lock does not convert the application into an account system.

## Emergency console PIN recovery

There is intentionally **no unauthenticated web reset endpoint**. The server owner can reset the PIN from the Docker/Unraid console without deleting any configuration or history:

```bash
docker exec -it rip-manager python /app/controller_admin.py reset-pin
```

Enter the new 4–8 digit PIN twice when prompted. The PIN is read with hidden console input and is never placed in the shell command line or logs. This changes only the PIN hash, lock state and controller sessions. Nodes, ripping history, storage paths, drive mappings, settings and updater state are preserved.

To reset the PIN but leave controller protection disabled:

```bash
docker exec -it rip-manager python /app/controller_admin.py reset-pin --disable-lock
```

To view non-secret security status from the console:

```bash
docker exec rip-manager python /app/controller_admin.py status
```

## Rip Node / API authentication

Human controller security and machine-to-machine security are separate. During Install & Adopt, Rip Manager generates a cryptographically random bearer token for the node. The Manager stores the node token in its persistent database and sends it as `Authorization: Bearer ...`; the node receives the same token through its service environment. Human PINs are never reused as node credentials.

The bundled Rip Node API requires the configured bearer token on status, storage, drive mapping, rip/control and other protected routes. Basic `/health` and `/api/info` remain usable for service/update health checking without exposing credentials or control capability. Do not intentionally deploy a node with a blank `RIP_NODE_TOKEN`, and do not port-forward node API port 8000 to the public internet.

Keep node tokens, NAS credentials, GitHub tokens and API keys outside source control. SSH/sudo credentials used during adoption are request-only and are not persisted by Rip Manager.

## Trusted LAN and reverse proxy guidance

The intended deployment is a trusted LAN/controller device, optionally reached through a properly secured VPN or HTTPS reverse proxy. If exposing the Manager through HTTPS, set `RIP_MANAGER_COOKIE_SECURE=1`. Do not directly expose Rip Node control APIs to the internet. A reverse proxy does not replace the local controller PIN or node bearer tokens.

## Health and updater behaviour

`/health` remains intentionally small for Docker/container monitoring. It does not disclose users, credentials, tokens or sensitive configuration.

The v0.20.0 security changes do not replace the existing updater. Manager updates, version reporting, manual install/force behaviour where supported, node update checks, node dependency checks and restart/health verification remain part of the existing release/update system.

## Automatic releases

This repository publishes a GitHub Release whenever `release.json` changes on `main`. Before publishing:

1. Update `rip_manager_container/app/config.py`.
2. Update `release.json` with the same version and plain-English release notes.
3. Run the automated tests and update/install regression checks.
4. Merge the tested changes into `main`.

GitHub Actions checks the source, packages the `rip_manager_container` folder, creates the matching `vX.Y.Z` tag and publishes the Manager updater ZIP. Existing releases are never overwritten.

See `rip_manager_container/README.md` for the full installation, operation, settings and rollback guidance.

## One-time v0.20.0 upgrade steps

No Authentik or account migration is required. Upgrade normally through the existing Manager updater. After restart, confirm Settings shows the expected controller lock/PIN state and that each real node shows authenticated API status. If an old manually configured node has no bearer token, re-adopt/repair that node before exposing its API beyond the trusted host/network.
