# HANDOFF — Nachtstand 2026-11-19 (Nucleon / V0.3)

Branch: `claude/v0.3-drive-seam` (hat v0.2-Code + v0.3-Design + Kit). Letzter Push aktuell.

## Erledigt in dieser Nacht
- **v0.2 einsatzbereit** auf Wegwerf-Account Interface_Zero (echter Claude-Classifier,
  Haiku 5.5, Anthropic OpenAI-Endpoint, drafts-only). Doku: `docs/v0.2/live-acceptance-2026-11-19.md`.
- **Provider-Naht**: Auth-Header + `temperature` optional (commits auf v0.2, gemerged).
- **proton-drive-kit gebaut** (TDD, 5 Tasks, 21 Tests grün) unter
  `packages/proton-drive-kit/`: `records` (parse), `datamap` (sha1-Presence),
  `access` (CLI-Wrapper, injizierbarer Runner), `scanner` (BFS). Plan:
  `docs/v0.3/plan-proton-drive-kit-2026-11-19.md`. **Review läuft** (frischer Reviewer).
- **Drive-Recon** (read-only): `docs/v0.3/drive-recon-2026-11-19.md` (Taxonomie § LEGAL/HR/INSURANCE,
  sha1 pro Datei bestätigt). Hilfsskripte auf dem Cube: `drive_list.sh`, `drive_names.sh`.
- **Traffic-Sim**: 4 variierte Mails (Kanzlei/Behörde/Job-Absage/Newsletter) an Interface_Zero
  gesendet (`/home/claude/traffic_sim.py`); Auswertung des Classifier-Verhaltens ausstehend.

## Läuft / Infra
- Interim-Dauerbetrieb: `/home/claude/nucleon-run/loop.sh` (alle 15 min), PID in
  `nucleon-run/loop.pid`; Logs `nucleon-run/nucleon-cron.log`.
- Durable systemd-Timer (`nucleon-run/nucleon.{service,timer}`) **an BOFH übergeben**
  (`pingpong:general/to-bofh-2026-11-19.md`) — braucht host-root; danach Loop stoppen.
- Secrets in `pass` (User claude): `nucleon/bridge-imap-interface_zero`, `nucleon/classifier-api-key`.
- Cube-Deployment: `/home/claude/nucleon-run/config.yaml` (Bridge 2143/2025, Haiku 5.5).

## Offen / nächste Schritte
1. **Kit-Review-Befunde** einarbeiten (Fix-Pass, sobald Reviewer zurück).
2. **Nucleon Attachment-Presence-Modul** (gated, default-off) — konsumiert den Kit
   (`DataMap.contains_sha1`); nächster Build-Schritt.
3. **Traffic-Sim auswerten** (Journal/Drafts) + ins Morgen-Reporting.
4. **Echt-Account-Mail-Bridge** (für reales Postfach) — NICHT eingerichtet; bewusster
   späterer Schritt (Privacy: dann lokales/eigenes Modell).
5. Kit später in eigenes Repo `Galil3o/proton-drive-kit` extrahieren (Weg 1).

## Grenzen (Charter `docs/v0.3/night-2026-11-19-charter.md`)
Nur lesen auf echtem Drive; nichts aufs echte Postfach; kein main-Push; Selbst-Budgetdeckel.
