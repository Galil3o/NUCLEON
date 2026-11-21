# Platten-sparsamer Proton-Mail-Zugang — Recherche & Optionen

**Stand:** 2026-11-21 (Nacht-Recherche, teils Quellcode gelesen). Unterfüttert Morgen-
Entscheidung #5 (Mail-Zugang für den ordnerweisen `fetch → sha1 → vergleichen → löschen`-Zyklus).

## Kernbefund
**Kein offizielles Proton-Werkzeug erfüllt „ordnerweise + harter Platten-Deckel + read-only"
zugleich.** Proton Bridge ist dafür das schlechteste (voller verschlüsselter Dauer-Cache des
ganzen Kontos, kein Größenlimit, keine Ordnerwahl, kein Lazy-Modus) — **das ist die Ursache
der 13 GiB.** Der einzige Weg, der den Zyklus exakt abbildet, ist ein **eigenes read-only
Skript gegen die Proton-API** mit `LabelID`-Filter.

## Optionen

### A) Eigenes read-only API-Tool (bester Fit, Eigenbau)
- **`go-proton-api`** (Protons eigene Lib, wie Bridge/Export-Tool) hat `MessageFilter{LabelID}`
  → ordner-/labelgefiltertes Paging ist API-seitig möglich. Zyklus: Login (SRP) + 2FA →
  `GetLabels` → `GetMessageMetadataPage(LabelID=…)` → pro Nachricht `GetMessage` + je Anhang
  `GetAttachment` → entschlüsseln → **sha1 → vergleichen → schreiben/verwerfen → löschen** →
  nächste. Peak-Verbrauch = „1 Nachricht + Anhänge" → **harter Deckel trivial**. Inkrementell
  über Protons Event-API.
- **Aufwand:** echte Implementierung (SRP-Auth + PGP), kein Copy-Paste. Inoffiziell für
  Drittnutzung → Raten drosseln (CAPTCHA/Account-Flag-Risiko). Python-Alternative
  `protonmail-api-client` existiert (inoffiziell, CAPTCHA-Risiko, Attachment-Fähigkeit ungeprüft).

### B) Offizielles Export-Tool auf große Platte (pragmatische Zwischenlösung, kein Eigenbau)
- `proton-mail-export-cli`: **nur Vollexport** (kein Label-Filter — Quellcode bestätigt), aber
  schreibt **entschlüsselte `.eml` + Sidecar-`.json`** (mit `LabelIDs`!) **direkt**, ohne gluon-
  Cache. Scriptbar via Env: `ET_USER_EMAIL`, `ET_USER_PASSWORD`, `ET_TOTP_CODE`, `ET_DIR`,
  `ET_OPERATION=backup`, `ET_TELEMETRY_OFF=1`. Auth = **eigener Proton-Login**, nicht Bridge.
- **Haken:** gelöschte Exporte werden beim Re-Run **neu geladen** → ein Lösch-Deckel *während*
  des Laufs geht nicht. Taugt, wenn eine einmalige entschlüsselte Vollkopie auf eine
  **ausreichend große/externe** Platte passt; danach lokal ordnerweise per `.json` filtern.

### Nicht nutzen
- **Bridge** (voller Mirror, kein Limit/Filter/Lazy; Cache verschlüsselt + nicht-deterministisch
  → nicht mal hashbar). **rclone** (nur Proton *Drive*, nicht Mail). **hydroxide** (IMAP WIP/riskant).

## Ehrliche Konsequenz für die 13-GiB-„Basis"
Die 13 GiB sind Bridges **verschlüsselter gluon-Store** (10.481 Nachrichten-Bodies). Sie sind
**nur über ein laufendes Bridge lesbar** (= der disqualifizierte Weg, der die Platte füllt) und
**verschlüsselt/nicht-deterministisch** (nicht direkt hashbar). Heißt: Als „Arbeits-Basis" für
den Anhang-Abgleich ist der Cache **weniger nützlich als erhofft** — der tragfähige Weg ist
Option A oder B, die ohnehin frisch + entschlüsselt + ordnerbezogen ziehen. Die 13 GiB werden
**nicht gelöscht** (Inhaber-Wunsch), aber wir sollten nicht darauf bauen.

## Empfehlung für Morgen (#5)
- **Zielbild:** Option **A** (eigenes read-only API-Tool, ordnerweise, hart gedeckelt) — passt
  exakt zum Ground-Zero-Zyklus und zu „nie wieder Voll-Sync".
- **Schneller Start, falls Platte da:** Option **B** einmalig auf externe/große Platte, dann
  lokal filtern — offizieller, risikoärmster Weg zu entschlüsselten `.eml`.
- Zu entscheiden: A bauen (mehr Aufwand, dauerhaft richtig) vs. B zuerst (schnell, braucht
  Platz) — und ob/woher mehr Platte (BOFH).

## Ehrlichkeits-Markierung
Code-belegt: Export-Tool-Flags/Env + „Vollexport ohne LabelID"; `MessageFilter.LabelID` existiert;
Bridge-Cache = verschlüsselte Vollkopie, nur Ort (nicht Größe) einstellbar. Geschlossen (nicht
per Statement belegt): Bridge-Lazy/Ordner-Ausschluss fehlt. Ungeprüft: `protonmail-api-client`-
Attachments, Eigenbau nicht lauffähig verifiziert. Artefakt: der Worker hat `ProtonMail/
proton-mail-export` read-only nach `/home/user/protonmail/` geklont (nur Quellcode-Lesen).
