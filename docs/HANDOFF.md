# HANDOFF — Nachtstand 2026-11-19 (Nucleon / V0.3)

Branch: `claude/v0.3-drive-seam` (v0.2-Code + v0.3-Design + Kit). Alles committet + gepusht.

## Erledigt in dieser Nacht
- **v0.2 einsatzbereit** auf Wegwerf-Account Interface_Zero (echter Claude-Classifier Haiku 5.5,
  Anthropic OpenAI-Endpoint, drafts-only). Doku: `docs/v0.2/live-acceptance-2026-11-19.md`.
- **Provider-Naht**: Auth-Header + `temperature` optional (gemerged aus v0.2).
- **proton-drive-kit gebaut, reviewt, gefixt** unter `packages/proton-drive-kit/`
  (`records`/`datamap`/`access`/`scanner`), TDD, **26 Tests grün**. Plan:
  `docs/v0.3/plan-proton-drive-kit-2026-11-19.md`. Unabhängiger Reviewer (opus) fand 3 echte
  Bugs — alle gefixt (commit `1c9ff56`): Geschwister-Präfix-Leak im `under`-Scope,
  „/"-im-Namen-Pfadfälschung, Liste-von-Nicht-Objekten → sauberer Fehler.
- **Drive-Recon** (read-only): `docs/v0.3/drive-recon-2026-11-19.md` (Taxonomie § LEGAL/HR/INSURANCE,
  sha1 pro Datei). Cube-Skripte: `drive_list.sh`, `drive_names.sh`.
- **Traffic-Sim**: 4 variierte Mails (Kanzlei/Behörde/Job-Absage/Newsletter) an Interface_Zero.

## Produktionsbefunde (Traffic-Sim) — für Folge-Arbeit
1. **Classifier konservativ bei Reply-Erkennung:** alle 4 → `classified`, **drafted=0**, 0 Fehler.
   Auch die Kanzlei-„bitte Frist bestätigen" + die Behörden-Fristsetzung bekamen KEINEN Entwurf.
   → Prompt-Tuning der Reply-/Draft-Entscheidung ist ein eigener Schritt (war nicht Nacht-Scope).
2. **Bridge-SEARCH-Index-Lag:** neu zugestellte Mails sind per SELECT sofort da, aber Nucleons
   SEARCH-basierter Fetch sieht sie erst nach dem Bridge-Index (Minuten). Für den 15-min-Loop
   benign (nächster Tick), aber relevant, falls man „sofort" erwartet.

## Läuft / Infra
- Interim-Loop: `/home/claude/nucleon-run/loop.sh` (alle 15 min), PID `nucleon-run/loop.pid`,
  Log `nucleon-run/nucleon-cron.log`.
- Durable systemd-Timer (`nucleon-run/nucleon.{service,timer}`) **an BOFH übergeben**
  (`pingpong:general/to-bofh-2026-11-19.md`); nach Install Loop stoppen.
- Secrets in `pass` (claude): `nucleon/bridge-imap-interface_zero`, `nucleon/classifier-api-key`.

## Offen / nächste Schritte
1. **Nucleon Attachment-Presence-Modul** (approved, nächster Build): gated, default-off.
   Design-Notizen: (a) Anhänge aus der IMAP-Message extrahieren + sha1 bilden (neu — Nucleon
   liest bisher nur Body-Text); (b) gegen ein `PresenceLookup`-Protocol prüfen, das der Kit-
   `DataMap.contains_sha1(sha1, under=)` erfüllt (keine harte Import-Kopplung an die Staging-
   Lage); (c) Pipeline-Einhängepunkt + Config-Gate `drive.presence_check.enabled: false`.
   Verdient einen eigenen kleinen Plan wie der Kit (nicht hastig am Sessionende).
2. **Reply-Erkennung / Prompt-Tuning** (Befund 1 oben).
3. **Echt-Account-Mail-Bridge** — NICHT eingerichtet; bewusster späterer Schritt (dann lokales Modell).
4. Kit in eigenes Repo `Galil3o/proton-drive-kit` extrahieren (Weg 1).

## Grenzen (Charter `docs/v0.3/night-2026-11-19-charter.md`)
Nur lesen auf echtem Drive; nichts aufs echte Postfach; kein main-Push; Selbst-Budgetdeckel.
