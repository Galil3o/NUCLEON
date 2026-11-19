# V0.3 Nacht-Arbeits-Charter — 2026-11-19

Owner-Freigabe für autonome Nacht-Arbeit in diesem Rahmen erteilt.

## Freigegebene Arbeit
1. **V0.3 `proton-drive-kit`-Fundament** (Design-Freigabe erteilt): Access-Wrapper um
   die Proton-Drive-CLI, Scanner, uid/sha1-Data-Map. TDD. Zielbild Weg 1 (eigenes Repo);
   nachts zunächst **in-repo gestaged** (eigenes GitHub-Repo kann der Agent nicht anlegen —
   Scope), spätere Extraktion vermerkt.
2. **Nucleon Attachment-Presence-Modul** (Freigabe erteilt): gated, **default-off** —
   sha1 eines Mail-Anhangs → Lookup in der Data-Map („liegt schon im Drive?").
3. **Doku + Entscheidungen**: **STEHENDE Freigabe** — nicht je Mal nachfragen
   (Owner: im Zweifel immer dokumentieren; gute Doku rettet im Super-GAU).
4. **Monitoring + Traffic-Simulation** auf dem Wegwerf-Account **TestAccount**
   (`TestAccount@proton.me`): variierte Selbst-/Wegwerf-Mails einliefern
   (Kanzlei/Behörde/Job-Absage/Rechnung …), Nucleons Verhalten unter „Last" beobachten,
   morgens berichten.

## Lesescope auf ECHTEN Daten — nur LESEN (Owner bestätigt)
Echtes **Drive** via CLI (Struktur/Metadaten/sha1), zur Vorbereitung des Scharfschaltens:
- **Legal/** → **Arbeitsgericht**, **AGG** (vorgelabelt)
- **Legal/** → **Versicherungen** und alle Ordner mit Präfix **RS**, **Z-RS**, **Z-erledigt-RS**
- **HR/Ongoing Applications/** → Unterordner **Open**, **Rejections**, **Job Ads**
Ziel: reale Ordner-Taxonomie verstehen; ableiten, was ein Go-Live erfordert
(Routing-Regeln, Data-Map-Umfang).
**Hinweis:** das echte **Mail**-Postfach ist NICHT erreichbar (Bridge auf Echt-Account
nicht eingerichtet) — wird nachts nicht angefasst. Nur Drive.

## Harte Grenzen
- Nur **LESEN** auf echtem Drive. **Nichts** aufs echte Postfach schreiben/senden.
- Kein Push auf `main` (keines Repos). Entwicklung auf `claude/*`.
- Keine kostenpflichtigen Zusatzdienste, keine Budget-Erhöhung.
- **Selbst-Budgetdeckel** (Owner gab keine Zahl): API-Ausgaben klein (deutlich unter den
  $5 Anthropic-Credits; Traffic-Sim mit Haiku = Cent); vor Erschöpfung stoppen; auf das
  Agent-Wochenkontingent achten (checkpointen). Git/Doku = laufende Sicherung.
- Fortlaufende Übergabe in `docs/HANDOFF.md` + Morgenbericht.

## Reihenfolge (grob)
Drive-Lesescope-Recon (Taxonomie + sha1-Grounding) → `proton-drive-kit` TDD
(Access/Scanner/Data-Map) → Attachment-Presence-Modul → Traffic-Sim + Monitoring →
Doku/Decisions laufend.
