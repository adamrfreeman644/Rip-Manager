# Rip Manager

Remote control and update system for the Rip Manager container and its Rip
Nodes.

## Automatic releases

The repository publishes a GitHub Release whenever `release.json` changes on
`main`.

Before publishing:

1. Update `rip_manager_container/app/config.py`.
2. Update `release.json` with the same version and plain-English release notes.
3. Merge the tested changes into `main`.

GitHub Actions checks the source, packages the `rip_manager_container` folder,
creates the matching `vX.Y.Z` tag and publishes exactly one ZIP asset for the
Manager updater. Existing releases are never overwritten.

See `rip_manager_container/README.md` for installation, operation, settings and
rollback guidance.
