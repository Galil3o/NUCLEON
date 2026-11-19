# V0.3 Drive-Recon + Go-Live-Befund (2026-11-19, read-only)

Lesender Recon des echten Proton-Drives via CLI, zur Vorbereitung der Drive-Naht.
**Nur Struktur/Metadaten; Fall-, Partei- und Personennamen bleiben aus dem Repo.**

## Zugriff bestätigt
- CLI: `/opt/proton-drive-cli/0.9.0/proton-drive filesystem list -j <pfad>` unter
  `dbus-run-session` + `gnome-keyring-daemon --unlock` (**leeres Passwort**, kein Secret).
  Hilfsskripte auf dem Cube: `drive_list.sh` (Einzel-Listing), `drive_names.sh` (Namen).
- Jeder Datei-Eintrag liefert: `uid`, `name`, `type`, `mediaType`, `totalStorageSize`,
  `modificationTime` und **`activeRevision.claimedDigests.sha1`** (+ `sha1Verified`,
  i. d. R. `false` → client-claimed, als Präsenz-Hinweis gut genug, als „claimed" kennzeichnen).
- Drive-Wurzel: Protons Standardbereiche; echte Dateien unter **`/my-files`**.

## Reale Taxonomie (relevante Zweige)
- `/my-files/§ LEGAL/` → nach Gericht/Rechtsgebiet gegliedert: `COURT - LABOR`
  (Arbeitsgericht), `COURT - CIVIL/ADMINISTRATIVE/FAMILY/SOCIAL/CRIM/TAX`,
  `LAW - CIVIL/CORPORATE/FAMILY/SOCIAL/TAX/IFG/DSGVO/ADMINSTRATIVE`,
  `V INSURANCE`, `X POLICE`, `X StA`, `Y OTHERS`, `Z REGISTRATIONS`, `1 RESSOURCES`.
- `/my-files/HR/` → `Applications`, `Employers`, `Example CVs`, `Resources CV`,
  `Certificates…`, `Medizinstudium`, `Old`, … (viele CV-Versionen, Zeugnisse, LORs).
- `/my-files/INSURANCE/` (separat, zusätzlich zu `§ LEGAL/V INSURANCE`).
- Fall-Ablagen tragen oft Jahr+Partei im Namen (z. B. Schriftsätze als `.docx`).

## Attachment-Presence-Check: feasibel (bewiesen)
„Liegt der Anhang schon im Drive (zum Fall)?" =
1. `sha1` des Mail-Anhangs berechnen,
2. Lookup im **Data-Map-sha1-Index** (aus einem Scan der relevanten Teilbäume),
   optional eingegrenzt auf den Fall-/Ordner-Teilbereich.
Treffer → „schon vorhanden unter Pfad X" (claimed-sha1-Hinweis); kein Treffer → neu.
Die Scan-/sha1-Maschinerie existiert in SGBs `drive_scan2.py` bereits — der DriveKit
generalisiert sie.

## Was ein Go-Live („scharf schalten") erfordern würde
1. **Data-Map** über die relevanten Teilbäume (`§ LEGAL`, `HR`, `INSURANCE`):
   uid·pfad·sha1·größe·mtime, Delta-fähig (Re-Scan), **keine Inhalte** persistiert.
2. **Routing Mail→Ordner:** die Gliederung ist grob (Gericht/Rechtsgebiet, HR-Bereiche)
   → deterministische Regeln (Kategorie/Absender → Zielzweig) sind machbar;
   feinere Fall-Ordner-Zuordnung braucht die Lern-Schicht oder explizite Regeln.
3. **Gate/Scope:** Schreib-Zielordner-Whitelist + Lese-Teilbereich (die o. g. Zweige),
   drafts-only bleibt; Anhänge beim Review laut.
4. **Mail-Zugang zum Echt-Account** (Bridge-Vault) — heute NICHT eingerichtet; offener
   Punkt für den echten Betrieb (getrennt von der Drive-Leseseite).

## Privacy
Echtes Drive = sensibel (Rechts-/Gesundheits-/Bewerbungsdaten). Nur Metadaten lesen,
nichts schreiben; Data-Map führt uid/sha1/pfad, nie Inhalte; sensible Namen nur lokal.
