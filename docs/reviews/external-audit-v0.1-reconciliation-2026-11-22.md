# Externer v0.1-Audit — Abgleich & Status

**Quelle:** unabhängige Architektur-/Sicherheits-/Code-Analyse von NUCLEON **v0.1**
(Snapshot 2026-11-19, 23 Commits auf `main`), vom Inhaber hochgeladen am 2026-11-22.
**Abgleich** gegen den aktuellen Stand (v0.2/v0.3 + Gutachter-Review R-001…R-026).

Lob der Analyse: die Capability-Gate-Architektur („the capabilities are the boundary",
Lethal-Trifecta-Härtung) ist im Entwurf ungewöhnlich konsequent. Die 6 Befunde liegen an den
Außengrenzen (IMAP-Ordner, LLM-Input, Fehler-Transaktionalität, CI).

## Status der 6 Audit-Befunde (heutiger Code)

| Audit-Befund | Status heute |
|---|---|
| **#1** Label-Bypass in System-Ordner · unquotiertes COPY | System-Ordner **geschlossen** = **R-001**. COPY/MOVE-Quoting **heute gefixt** (`8b81308`). |
| **#2** Statischer Delimiter · keine Größenlimits · kein URL-Schema | Schema = **R-007**, Größenlimits = **R-011** (beide zu). Zufalls-Delimiter **heute gefixt** (`b3bf60c`). Redirect-Header-Leak (Unterpunkt R-007) **heute gefixt** (`b3bf60c`). |
| **#3** auto_trash per Prompt-Injection | **Offen** (default-off) → neu als **R-027** registriert. |
| **#4** `\r`/`\n` in draft_subject → Absturz | **Heute gefixt** (`b3bf60c`, Steuerzeichen-Strip in `gate.py`). |
| **#5** logout · uid=0 · Journal O(N²)/0o600 · Config-Grenzen | `limit` = **R-026**, `port`/`logout` **heute gefixt** (`b3bf60c`/`8b81308`); uid=0 = **R-010**; Teil-Skip = **R-004/R-014**; **0o600 war bereits** via `appendlog` (R-013) gesetzt; Journal-O(N²) offen → **R-028**. |
| **#6** CI: Major-Tag-Pins · kein `permissions` · nur pytest | `permissions` + Kit-Testschritt (**R-022**) **heute gefixt** (`b3bf60c`). SHA-Pinning → **R-029**, statische Prüfungen → **R-030** (beide offen). |

## Heute gefixt (B, Commits auf `claude/v0.3-drive-seam`)
- Zufalls-Delimiter, No-Redirect-Opener (R-007), `port`-Grenze, draft_subject-Sanitizing,
  CI-`permissions` + Kit-Tests: `b3bf60c`.
- COPY/MOVE-Quoting, `main()`-logout im finally: `8b81308`.
- (`limit`-Grenze bereits `5fab80f` = R-026.) Suite 361 grün.

## Neu registriert (offen) — ins Gutachter-Ping-Pong eingetragen
- **R-027** design·wichtig·NEEDS-OWNER — auto_trash ohne deterministische Schutzregel.
- **R-028** correctness·klein·OFFEN — `Journal.has_processed` O(N²); In-Memory-Index verschoben.
- **R-029** security·nit·OFFEN — CI-Actions auf Commit-SHA pinnen (braucht echte SHAs).
- **R-030** security·klein·OFFEN — CI statische Prüfungen (ruff/mypy/bandit/pip-audit).

**Fazit:** Die externe Analyse bestätigt den Gutachter-Weg unabhängig; die schwersten Punkte
sind zu. Die verbliebenen offenen Punkte sind günstige Pre-Live-Härtungen (R-027…R-030).
