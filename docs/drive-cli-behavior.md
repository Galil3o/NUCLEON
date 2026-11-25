# Proton Drive CLI — gelerntes Verhalten (2026-11-25, synthetische Tests)

Getestet live mit Wegwerf-Dateien im `/my-files/0 NUCLEON INBOX/` (echtes Postfach/Daten
unangetastet; Testdateien danach getrasht).

## Upload / Konflikte (`fs upload [-f STRATEGY] [-d STRATEGY] localPath... parentPath`)
- **Ohne `-f` promptet die CLI interaktiv → hängt im non-interaktiven Betrieb.**
  → Der Stager MUSS immer eine `-f`-Strategie setzen.
- **Gleicher Inhalt wird automatisch übersprungen** (idempotent per Content) — Re-Upload
  eines bereits vorhandenen Anhangs ist ein No-op.
- `-f`-Strategien für Dateien: `create-new-revision` | `rename` (Suffix) | `replace`
  (remote trashen + neu) | `skip`.
- `-d` für Ordner: `merge` | `rename` | `replace` | `skip`.
- **Gewählt für Nucleon-Staging:** `-f rename` → gleicher Inhalt übersprungen, gleicher
  Name/anderer Inhalt bekommt einen eindeutigen Suffix (= Kollisions-Vorgabe des Inhabers).

## Reversible Operationen (bereits im Pre-Flight bestätigt)
- `fs trash path...` → Proton-Papierkorb (reversibel). `fs restore /trash/<Name>` holt zurück;
  `fs list /trash` zeigt den Papierkorb. **Nie** `fs delete` / `fs empty-trash`.
- `fs move sourcePath... targetParentPath`, `fs rename path newName`, `fs create-folder parentPath name`.
- Pfade immer mit Volume-Root `/my-files/...`; Node-Adressierung sonst per UID-Pfadsegment.

## Konsequenz fürs Filing
Stager = `fs upload -f rename <tmpfile> "<Zielordner>"`. Idempotenz gratis über Content-Skip;
Namenskollision → Suffix. Zielordner kommt aus der Routing-Schicht (default: Eingangsordner).
