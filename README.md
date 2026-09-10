# Rip Manager v0.20.2

Remote controller for Rip Manager and its Rip Nodes.

## Security model

Rip Manager is deliberately **not** a conventional user/account service. It does not use Authentik, usernames, email login, registration, user profiles or email password recovery. Its normal interface remains a lightweight appliance/controller protected by the existing local PIN when controller protection is enabled.

The controller PIN is never stored in plaintext. It is salted and hashed server-side with PBKDF2-HMAC-SHA256. Successful unlocks use random application session tokens; only a SHA-256 hash of each session token is stored in SQLite. Repeated incorrect PIN attempts are throttled. Changing/resetting the PIN revokes existing controller sessions.

## Changing the controller PIN

Use the normal Settings interface. Once a PIN exists, the current PIN is required before a replacement is accepted. PINs are 4–8 digits.

Emergency console reset:

```bash
docker exec -it rip-manager python /app/controller_admin.py reset-pin
```

To view non-secret security status:

```bash
docker exec rip-manager python /app/controller_admin.py status
```

## Rip Node / API authentication

Human controller security and machine-to-machine security are separate. During Install & Adopt, Rip Manager generates a random bearer token for the node. The Manager stores the node token in its persistent database and sends it as `Authorization: Bearer ...`.

Basic `/health` and `/api/info` remain usable for service/update health checking without exposing credentials or control capability. Do not port-forward node API port 8000 to the public internet.

## AD53 Shared App Updater

From v0.20.2, **local Rip Manager container updates are handled by the central `ad53-shared-updater` container**.

Rip Manager reaches it using:

```text
http://host.docker.internal:8093/apps/rip-manager
```

The URL can be overridden with:

```text
RIP_MANAGER_SHARED_UPDATER_URL
```

The Manager no longer needs a local Docker-control updater for replacing the `rip-manager` container. The shared updater checks the repository, backs up managed source files, rebuilds only the Rip Manager service, validates the running version using `/api/info`, and automatically rolls back if validation fails.

Because this repository is private, the shared updater uses the existing GitHub token mounted read-only at:

```text
/run/secrets/rip-github-token
```

Docker socket access stays in `ad53-shared-updater`; it is not added to the Rip Manager container.

## Important: Rip Node updates remain separate

Rip Node updating is **not** merged into the central Docker updater.

That is intentional: Rip Nodes are other machines running their own node API/system service, not containers in the local Unraid Docker stack. Rip Manager continues to handle node-version checking, bundled node packages and node update requests.

So the split is now:

- **Rip Manager container update** → `ad53-shared-updater`
- **Remote Rip Node updates** → Rip Manager's existing node update system

This keeps one Docker updater for the server while preserving the node fleet workflow.

## Versioning

The repository now includes a root `VERSION` file used by the shared updater. It must match the Manager runtime version in:

```text
rip_manager_container/app/config.py
```

and the version in:

```text
release.json
```

When preparing a new Manager release, update all three to the same version.

## Automatic releases

This repository publishes a GitHub Release whenever `release.json` changes on `main`.

Before publishing:

1. Update root `VERSION`.
2. Update `rip_manager_container/app/config.py`.
3. Update `release.json` with the same version and release notes.
4. Run the automated tests and update/install regression checks.
5. Merge the tested changes into `main`.

GitHub Actions can continue packaging release assets for compatibility and node workflows. The local Manager install path now uses the AD53 Shared App Updater.

## Shared updater health checks

Check the Manager:

```bash
curl http://127.0.0.1:8088/api/info
```

Check the shared updater entry:

```bash
curl http://127.0.0.1:8093/apps/rip-manager/status
```

Check updater logs:

```bash
docker logs ad53-shared-updater
```

## Trusted LAN and reverse proxy guidance

The intended deployment is a trusted LAN/controller device, optionally reached through a secured VPN or HTTPS reverse proxy. If exposing the Manager through HTTPS, set `RIP_MANAGER_COOKIE_SECURE=1`. Do not directly expose Rip Node control APIs or the shared updater to the public internet.

See `rip_manager_container/README.md` for the full installation and operation guide.
