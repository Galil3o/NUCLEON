# Nucleon v0.3 — Drive-Seam (Design-Seed)

Status: **Design/Brainstorming** (wächst inkrementell wie v0.2; NICHTS wird gebaut
vor Owner-Freigabe des Designs). Erstellt 2026-11-19.

## Intent / Warum
Nucleon soll nicht nur Mail-Client sein, sondern mail-abgeleitete Dateien auf
**Proton Drive** ablegen und Drive-Dateien an Entwürfe anhängen können:
- **Schreiben:** Anhänge aus E-Mails, oder eine **PDF als „Abdruck" einer E-Mail**,
  auf das Drive speichern.
- **Lesen/Anhängen:** auf alle Ordner oder einen **Teilbereich** zugreifen, um
  **Dateien an ausgehende Entwürfe anzuhängen**.
- Gestützt auf eine **selbst-generierende Data-Map** (Scan → abstrakter uid-Index
  „welche Datei liegt wo").

**Optional / „Fusion".** Das Drive-Modul ist **zu-/abschaltbar** (Default **AUS**).
Opt-in: „Wenn du Proton Drive nutzt und einverstanden bist, reichert Nucleon damit
an." (Atommodell: Nucleon „steigt eine Klasse auf".)

## Gewählter Weg (Owner-Entscheidung 2026-11-19): Weg 1 — gemeinsame Bibliothek
Der generische Drive-Kern wird aus SGBs vorhandenem Tooling in ein **eigenständiges
Paket `proton-drive-kit`** extrahiert, das **beide** Repos nutzen (Nucleon +
SGB_IX_BASE). Nucleons Mail-Gebrauch und SGBs juristische Landkarte bleiben je im
eigenen Repo. Prinzip aus SGB `config/08`: **„geteilte Quelle/Core, kein geteiltes
Repo."** (Verworfen: Weg 2 „Nucleon hostet, SGB konsumiert" — SGBs Stand ist reifer,
Nachbau verlöre Know-how; Weg 3 „loser Vertrag" — Duplizierung, Drift.)

## Recon-Grundlage (SGB_IX_BASE, belegt)
- **Zugriff:** Proton Drive **CLI** `/opt/proton-drive-cli/0.9.0/proton-drive`
  (Cube). `proton-drive filesystem list -j <pfad>` → JSON je Eintrag mit `uid`,
  `type`, `name`, `activeRevision.{storageSize, claimedDigests.sha1}`,
  `modificationTime`, `mediaType`, `isSharedByUrl`. Auth via dbus + gnome-keyring
  (`run_scan2.sh`). Früh (0.9.0), aber funktional — wir entwickeln bewusst dagegen.
- **Scanner** (`drive_scan2.py`): BFS, Thread-Pool, Tiefe begrenzt → TSV-Zeilen.
- **Data-Map / Landkarte** (`graph/_index/drive-landkarte-AGG.tsv`, Branch `cai_/…`):
  Spalten `uid · pfad · größe · sha1 · mtime · media · dup_status · dup_grp ·
  map_status`. **uid-basiert** (übersteht Umbenennen/Verschieben), **keine
  Datei-Inhalte im Git** (nur Verortung), **Delta-Modell** (Re-Scan + Diff nach uid
  → NEU/GEÄNDERT/VERSCHOBEN/GELÖSCHT), Dubletten-Klassifikation, optional
  **OpenTimestamps**-Anker.

## Komponenten
### 1. `proton-drive-kit` (neues, geteiltes Paket — account-neutral)
- **Access-Layer (Zugriffs-Eigentümer — entschieden: Weg C, 2026-11-19):** das kit
  **besitzt** den Drive-Zugriff als **Bibliothek**, kein Dienst/Daemon. Dünner
  Wrapper um die `proton-drive`-CLI (`list` / `download` / `upload` / ggf. `mkdir`)
  hinter einem Interface (später andere Backends / Drive-API plugbar); Keyring-/dbus-
  Unlock und ein **konto-weiter Lock** (nur eine CLI-Operation je Konto gleichzeitig)
  leben **einmal** hier. Beide Repos importieren das kit; weder SGB noch Nucleon
  sprechen selbst mit der CLI. (Proton **Bridge ≠ Drive**: Nucleons Bridge-Zugriff ist
  Mail-only; Drive ist neue Fähigkeit, die ohnehin CLI oder Drive-API braucht.)
- **Scanner:** Verzeichnisbaum (oder Teilbereich) → Datei-Records
  (uid/pfad/größe/sha1/mtime/media/shared).
- **Data-Map:** uid-basierter Index + Delta-Modell + Dubletten-Klassifikation +
  optionaler OTS-Anker. Pluggable Storage (TSV/JSON).
- Keine Domänenlogik, keine Secrets, keine Mail-/Rechts-Spezifika.

### 2. Nucleon Drive-Modul (optional, Default AUS)
- Config `drive.enabled: false`; bei an: Pfad zum CLI-Binary/Account, Schreib-
  Zielordner, erlaubter Lese-Teilbereich (Whitelist von Ordnern).
- **`DriveStore`-Seam** (analog `MailStore`): list/read/write über das kit.
- **Write-Use-Cases:** (a) Anhänge einer Mail speichern; (b) **E-Mail-als-PDF**
  („Abdruck") rendern + ablegen. **Findbarkeit = Data-Map + Journal, nicht
  Ordner-Gedächtnis:** jeder Schreibvorgang wird protokolliert (Quell-Mail → uid,
  Ziel-Pfad, Name) und in der Data-Map geführt; du fragst „was hat Nucleon für Mail X
  gespeichert und wohin", statt dir den Ordner zu merken. **Floor (immer): sprechende
  Umbenennung** — `att0001.pdf` → `JJJJMMTT_Kontext_Originalname` (ISO-Basisformat, ohne Trennstriche). Nucleon
  referenziert nur **uid/Metadaten**, nie Inhalt.
- **Platzierung (Schreibziel) — entschieden außer Modus:** immer in einen **begrenzten
  Schreibbereich** (Whitelist). Modi (offen): **Eingang-Default** (ein `Nucleon/
  Eingang`, du verteilst später; Map macht's auffindbar) vs. **Routing-Regeln** (opt-in,
  deterministisch Kategorie/Absender/Label → thematischer Unterordner; Regeln legst
  **du** fest, das Modell erfindet keinen Ordner). Freie Modell-Platzierung (Modell
  liest Taxonomie, entscheidet selbst) **zurückgestellt** — Fehlablage ist der eine
  Fehler, den die Map nachvollziehbar, aber nicht ungeschehen macht.
- **Attach-Use-Case — entschieden 2026-11-19:** die **harte Grenze ist der freigegebene
  Lese-Teilbereich** (Ordner-Whitelist; Default eng/explizit, per Config bis „alle"
  weitbar). **Innerhalb** darf das Modell Dateien frei referenzieren (uid oder Name) →
  kit löst **nur im Teilbereich** auf → Download → Anhang. **Außerhalb: unmöglich.**
  Terminal-Backstop = die ohnehin vorhandene **menschliche Entwurfs-Prüfung**; Anhänge
  werden dort **laut** gezeigt (Name + Pfad + „aus Drive"), weil der Prüfer den Datei-
  *Inhalt* — anders als einen Empfänger-String — nicht mitliest. (Der Teilbereich ist
  der R-009-Analog, nicht die Modell-Freiheit darin.)
- **Lauf-Bericht (Drive-Schreibzugriff) — entschieden, Kanal offen:** ein Nucleon-Lauf
  mit Drive-Schreibrecht gibt am Ende eine **menschenlesbare Zusammenfassung** „welche
  Datei aus welcher Mail wohin abgelegt" (aus Journal/Map gespeist; Daten liegen ohnehin
  vor). Als **Lauf-Digest**, nicht pro Datei. Kein GUI-Popup bei headless-Cube-Lauf —
  realistische Kanäle: Bericht-Datei + stdout (Pull) und/oder Digest in die eigene
  Mailbox (Push, nur bei Läufen mit Schreibzugriff).
- **Data-Map-Scan:** Nucleon scannt einen konfigurierten Teilbereich → lokaler
  uid-Index (separater Store wie Marker/Journal).

### 3. Die „Bridge"
Beide Repos hängen an `proton-drive-kit`; SGBs vorhandenes Landkarte-/Scan-Tooling
wird **in das kit migriert** (Know-how an einem Ort, beide profitieren von Fixes).
SGB behält seine juristik-spezifische Nutzung; Nucleon seine Mail-Nutzung.

## Sicherheit & Datenschutz
- **Gate als Grenze (lethal trifecta erweitert):** Drive ist zugleich **Datenquelle**
  (Datei-Inhalte = untrusted, wenn extern) **und Schreib-/Exfil-Kanal**. Die harten
  Grenzen sind die **Ordner-Whitelists**: Schreiben nur in den **Schreibbereich**,
  Anhängen nur aus dem **Lese-Teilbereich**; keine beliebige Pfad-Navigation.
  **Innerhalb** der Whitelist darf das Modell frei wählen; die **menschliche
  Entwurfs-Prüfung** (Anhänge laut gezeigt) ist der Terminal-Check — nicht eine
  Einschränkung der Modell-Wahl innerhalb des erlaubten Bereichs.
- **Datenminimierung:** Nucleons Zustand speichert nur **uid/Hash/Metadaten**, nie
  Datei-Inhalte; die Data-Map ist ein separater Store. **Sensible Pfad-/Dateinamen**
  (SGB-Befund R-012) beachten: ggf. opake uids nach außen, Namen nur lokal.

## Offen (nächste Runden — Owner/Design)
- **kit-Repo/Name** (`Galil3o/proton-drive-kit`?), Lizenz, Sprache (Python, passend
  zu beiden).
- **SGB-Migration jetzt oder später** (zuerst extrahieren, dann SGB umstellen?).
- **E-Mail-als-PDF**: Rendering-Weg (welches Tool erzeugt den „Abdruck")?
- **Data-Map-Format in Nucleon** (SGBs TSV wiederverwenden vs. JSON).
- **Schreib-Platzierungsmodus:** Eingang-Default vs. Routing-Regeln (freie
  Modell-Platzierung zurückgestellt); OTS-Anker optional für Nucleon?
- **Lauf-Bericht-Kanal:** Bericht-Datei + stdout (Pull) vs. zusätzlich Mailbox-Digest
  (Push, nur bei Schreib-Läufen).
- **CLI-Auth/Setup** (dbus/keyring) für den Dauerbetrieb auf dem Cube.

> Reihenfolge: nachrangig zum laufenden **v0.2-Live-Gang** (echtes Modell, Pilot).
> Gebaut wird erst nach Owner-Freigabe dieses Designs.
