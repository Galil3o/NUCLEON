# E-Mail → PDF-Abdruck — Methode & Records-Checkliste

**Stand:** 2026-11-21 (Nacht-Recherche gegen Primär-/Fachquellen). Für Phase 2 (relevante
Mails als PDF-Abdruck sichern). **Keine Rechtsberatung.**

## Leitprinzip
Das **rohe `.eml` (RFC822) ist das Beweismittel**, das PDF nur **Lesefassung/Abdruck**. Beide
behalten (`.eml` + Anhänge im Originalformat, PDF ergänzend). Die Render-Pipeline macht
**keinerlei Netzwerkzugriff** — sonst ist der Abdruck weder privacy-sicher noch reproduzierbar.

## Pipeline
`.eml` → stdlib `email` parsen → **self-contained, sanitiertes HTML** (alles inline als
`data:`, keine externen URLs) → **Playwright + gebündeltes Chromium** print-to-PDF → PDF-
Metadaten normalisieren → **SHA-256 über `.eml`** (Integritätsanker) + SHA-256 über das PDF.

## Bibliotheken (minimal, auditierbar)
- **Parsen:** stdlib `email` (`BytesParser(policy=policy.default)`); bewusst keine
  Pflicht-Dependency wie `mailparser` (Auditierbarkeit fürs Rechtsarchiv). Nutzt
  `get_body(preferencelist=('html','plain'))`, `iter_attachments()`, `get_content()`
  (dekodiert CTE + RFC-2047-Header automatisch — **nicht** doppelt dekodieren).
- **Charset-Fallback:** `charset-normalizer` (für falsch deklarierte deutsche Mails,
  Windows-1252-als-ISO-8859-1 → kaputte Umlaute).
- **Sanitizing:** **`nh3`** (ammonia-Binding). **`bleach` ist seit Jan 2025 deprecated** → nicht neu verwenden.
- **Rendern:** `playwright` (sync) gegen vorinstalliertes Chromium.
- **PDF-Metadaten:** `pikepdf` / `qpdf --deterministic-id`. **Hash:** `hashlib` (SHA-256).

## Chromium (Binary unter /opt/pw-browsers)
- `PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers` oder `chromium.launch(executable_path=...)`.
- `headless=True`, `args=["--no-sandbox","--disable-gpu","--font-render-hinting=none"]`.
- `page.pdf()` (intern `Page.printToPDF`, nur headless): `print_background=True`,
  `format="A4"`, `prefer_css_page_size`, `--no-pdf-header-footer` (sonst Live-Datum/URL im Blatt).
- Oft treuer: `page.emulate_media(media="screen")` voranstellen (Mails sind für Screen designt).

## Faithful rendern
- **multipart/alternative** → HTML bevorzugen. **Text-Mails** → `<pre>` + `white-space:pre-wrap`.
- **Header-Block** oben als Tabelle: From/To/Cc/Date/Subject/Message-ID (+ Reply-To), Fuß
  „gerendert am … / `.eml`-SHA-256 …".
- **Anhang-Verzeichnis** im PDF: je Anhang Name (RFC-2231-dekodiert), Content-Type, Größe,
  **SHA-1 + SHA-256**. Anhang-Binaries NICHT ins Mail-PDF, sondern separat neben dem `.eml`
  ablegen und im Verzeichnis per Hash referenzieren. (sha1 verbindet direkt mit dem Drive-DataMap.)

## Externe Inhalte blocken (Tracking-Pixel!) — defense in depth
1. **Self-contained HTML:** nur `cid:`-Teile (aus `multipart/related`) → `data:`-inline;
   externe `http(s)`-Bilder/CSS-`url()` durch sichtbaren Platzhalter `[externes Bild entfernt: <URL>]`
   ersetzen (dokumentiert den Tracking-Versuch, statt ihn still zu schlucken).
2. **CSP-Meta** im Output: `default-src 'none'; img-src data:; style-src 'unsafe-inline' data:; font-src data:`.
3. **Playwright hart:** `context.route("**/*", lambda r: r.abort())` + `page.set_content(html, wait_until="load")`
   (kein `goto` → keine Base-URL) → null Egress, auch wenn das Sanitizing etwas übersieht.

## Fallstricke
- **UTF-8:** Output immer `<meta charset="utf-8">`. **Fonts:** Chromium nutzt fontconfig; ohne
  **fonts-liberation** (Arial/Times/Courier-Ersatz) + **Noto** (core/CJK/Emoji) → Tofu-Kästchen.
  Mit `fc-list` im Container prüfen, bevor produktiv gerendert wird.
- **Blanke PDFs** meist nicht Fonts, sondern falsches `wait_until`/Chromium nicht gestartet.

## Reproduzierbarkeit
Chromium schreibt je Lauf `CreationDate`/`ModDate` + zufällige `/ID` → zwei Läufe **nicht
byte-identisch**. **Empfehlung:** `.eml`-SHA-256 als kanonischen Anker; PDF als abgeleitete
Sicht, Renderer-+Pipeline-Version im Footer/Sidecar-Manifest („prozess-reproduzierbar").
Optional Byte-Gleichheit via `pikepdf`/`qpdf` (Dates aus Mail-`Date`-Header, `/ID` fixen).

## Records-/Belastbarkeits-Checkliste (deutscher Kontext, KEINE Rechtsberatung)
- **Original `.eml` + Anhänge** im Originalformat behalten (PDF nur ergänzend).
- Header erhalten: From/To/Cc/Date/Subject/Message-ID **+ Received-Kette, Return-Path,
  Authentication-Results (DKIM/SPF/DMARC)** als Authentizitätsindiz.
- **Integrität:** SHA-256 je `.eml`+Anhang in ein Manifest, Ablage in **git** (Historie =
  Manipulationsindiz + Zeitbezug). Optional qualifizierter **RFC-3161/eIDAS**-Zeitstempel.
- **GoBD** (BMF 14.07.2025): gilt rechtlich nur für steuerlich/handelsrechtlich Pflichtige,
  **nicht** rein privat — aber als Orientierung: Unveränderbarkeit, Nachvollziehbarkeit,
  Vollständigkeit, Durchsuchbarkeit, **Aufbewahrung im Eingangsformat** (`.eml` behalten).
- **PDF/A** (ISO 19005) für den Abdruck erwägen; `.eml` bleibt führend.
- **beA/beSt/EGVP:** nicht nur die Nachricht — **Prüfprotokoll + Transfervermerk (VHN)**,
  ggf. **eEB**, Original-Container + Signaturdateien historisieren. Prüfprotokoll belegt nur
  die Signatur, **nicht** die Zustellung (BGH VI ZB 36/22) → Zugang separat über eEB.
- **Beweiswert:** E-Mail-PDF i. d. R. **keine Urkunde**, sondern freie Beweiswürdigung
  (§ 286 ZPO); `.eml` + Hash + Authentication-Results erhöhen die Belastbarkeit.

## Offene Punkte
Byte- vs. Prozess-Reproduzierbarkeit entscheiden (Empf.: `.eml`-Hash-Anker). GoBD-Rz./beA-
Details vor verbindlicher Nutzung an Primärquellen prüfen. Remote-Bild-Policy: sichtbarer
Platzhalter (empfohlen). Fonts im Container via `fc-list` sicherstellen.
