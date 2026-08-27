#!/usr/bin/env python3
"""Local-owner recovery commands for Rip Manager.

This tool is intentionally console-only. It never opens an HTTP recovery
endpoint, never prints a PIN, and only changes controller authentication rows.
Nodes, settings, history, storage locations and update state are untouched.
"""
from __future__ import annotations

import argparse
import getpass

import auth
import db


def reset_pin(disable_lock: bool = False) -> int:
    db.init_db()
    first = getpass.getpass("New controller PIN (4-8 digits): ")
    second = getpass.getpass("Confirm new controller PIN: ")
    if first != second:
        raise SystemExit("PINs did not match; nothing was changed.")
    if not first.isdigit() or not 4 <= len(first) <= 8:
        raise SystemExit("PIN must contain 4-8 digits; nothing was changed.")
    db.set_setting("pin_hash", auth.hash_pin(first))
    db.set_setting("lock_enabled", "0" if disable_lock else "1")
    auth.revoke_all_sessions()
    print("Controller PIN reset successfully. Existing configuration and history were preserved.")
    print("Controller protection is " + ("disabled." if disable_lock else "enabled."))
    return 0


def status() -> int:
    db.init_db()
    nodes = db.query("SELECT id,token FROM nodes ORDER BY id")
    protected = sum(1 for node in nodes if node["token"])
    print(f"Controller protection: {'enabled' if db.get_setting_bool('lock_enabled') else 'disabled'}")
    print(f"PIN configured: {'yes' if auth.pin_is_set() else 'no'}")
    print(f"Node/API authentication: {protected}/{len(nodes)} configured nodes have bearer tokens")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rip Manager local controller administration")
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset-pin", help="Reset the local controller PIN without deleting data")
    reset.add_argument("--disable-lock", action="store_true", help="Reset the PIN but leave controller protection disabled")
    sub.add_parser("status", help="Show non-secret controller/API security status")
    args = parser.parse_args()
    if args.command == "reset-pin":
        return reset_pin(args.disable_lock)
    return status()


if __name__ == "__main__":
    raise SystemExit(main())
