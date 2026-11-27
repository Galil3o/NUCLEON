# R-027 — auto_trash Policy (Design, default-off)

Owner-Entscheidung 2026-11-27. Status: **Design only**, noch nicht implementiert.
auto_trash bleibt **default-OFF** und **reversibel** (Trash = Verschieben in den
Trash-Ordner, NIE endgültiges Löschen).

## Ziel
Der Nutzer wählt selbst, wie viel Konvenienz vs. Sicherheit er will — von „nur labeln,
ich entscheide" bis „die Kategorie allein reicht, mach einfach".

## Zwei orthogonale Achsen
1. **Trigger** — was wahr sein muss, damit gehandelt wird:
   - `off` (Default): nie.
   - `category_only`: die Classifier-Kategorie ist in `categories` → handeln. (Konvenienz; opt-in.)
   - `category_and_rules`: Kategorie in `categories` **UND** der `rules`-Satz trifft zu → handeln.
2. **Action** — was bei Auslösung passiert:
   - `label` (sicherer Default wenn an): Label `nucleon/trash-candidate` setzen; nichts wird
     verschoben. Der Nutzer sucht nach dem Label, prüft, und trasht/behält per Hand.
   - `trash`: in den Trash-Ordner verschieben (reversibel).

„Label-only-Modus" = beliebiger Trigger + `action=label`. Das sanfteste nützliche „an" ist
`category_only` + `label`.

## Rules-Modell (STRUKTURIERT halten, kein Freitext-Parser)
`rules` = `{ combine: all|any, conditions: [ {type, value...}, ... ] }`.
Condition-Typen sind ein kleiner fester Satz: `sender_in` (Adressen/Domains),
`subject_contains` (Schlüsselworte), `from_header_matches`, `category_in`, …
Kombiniert durch EIN top-level `all`/`any` (UND/ODER). Eine freie boolesche
Ausdruckssprache ist **bewusst ausgeschlossen** (zu komplex, schlecht auditierbar).

## Harte Schutzregeln (immer, unabhängig vom Modus)
- NIE bei **abgeschnittener/überlanger** Klassifizierungseingabe handeln (Teildaten → skip,
  Quelle behalten).
- NIE endgültig löschen; nur Trash (reversibel) — gemäß dem bestehenden No-Hard-Delete-Gate.
- Default off; Aktivierung ist eine ausdrückliche Pro-Konto-Entscheidung.
- Die deterministischen Regeln wertet das **Gate** über validierte, typisierte Felder aus —
  nie das Modell, nie aus Freitext, den das Modell kontrolliert (R-001/R-009-Grenze).
