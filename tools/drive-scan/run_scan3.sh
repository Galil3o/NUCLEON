#!/bin/bash
# UID-dedupe Scanner v3 unter dbus/keyring-Wrapper (leere Passphrase).
# Usage: run_scan3.sh /my-files [workers] [maxdepth]
# Liest read-only via proton-drive CLI; Ausgabe (TSV) nach stdout.
dbus-run-session -- bash -c 'printf "\n" | gnome-keyring-daemon --daemonize --unlock --components=secrets,pkcs11 >/dev/null 2>&1; exec python3 -u /home/claude/drive_scan3.py "$@"' _ "$@"
