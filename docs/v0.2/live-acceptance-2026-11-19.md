# v0.2 — Live-Abnahme + Provider-Naht (2026-11-19)

## Ergebnis: einsatzbereit auf dem Wegwerf-Account (TestAccount)
Echter Lauf über die Proton Bridge mit **echtem Claude-Classifier** (kein Fake-Stub):
- `processed=2, drafted=2, errors=0`, Exit 0.
- Zwei echte Entwürfe im Drafts-Ordner (**drafts-only, nichts gesendet**), u. a. eine
  deutsche Antwort auf eine Terminbestätigungs-Anfrage, die den Absender korrekt
  anspricht und **Platzhalter statt erfundener Fakten** lässt.
- R-011-Budget-Truncation griff bei einer 52k-Zeichen-Mail (Input auf 32k gekürzt).
- Gate: Empfänger = Absender (R-009, sender-only), Label-Allowlist, drafts-only.

## Provider-Naht (Classifier-Backend, provider-neutral)
Der Classifier spricht das **OpenAI-kompatible** `/v1/chat/completions`-Protokoll —
dasselbe für lokale Modelle (Ollama/vLLM), Claude, Gemini, OpenAI, Grok, DeepSeek,
OpenRouter. Zwei Erkenntnisse, die zu Code-Änderungen führten:

1. **Auth-Header-Naht** (commit `dec5e77`): optionaler `Authorization: Bearer <key>`-
   Header; Key NUR aus der Umgebung (`NUCLEON_CLASSIFIER_API_KEY`), nie aus Config/URL
   (R-007 bleibt). Endpoint darf Basis-URL sein (→ `/v1/chat/completions` angehängt)
   ODER eine volle Chat-Completions-URL verbatim (Gemini:
   `/v1beta/openai/chat/completions`).
2. **`temperature` optional, Default weglassen** (commit `2d19ddc`): Anthropics
   OpenAI-Kompatibilitätsschicht **lehnt `temperature < 1` auf Claude 4.7+ mit 400 ab**
   (Gemini analog). Der Classifier hatte `temperature: 0` hartkodiert. Jetzt:
   `ClassifierConfig.temperature` (Default `None` → weggelassen); nur ein lokales
   Modell, das Determinismus will, setzt es explizit.

### Anthropic OpenAI-kompatibler Endpoint (verifiziert, stable)
- Endpoint: `https://api.anthropic.com/v1/chat/completions`
- Auth: `Authorization: Bearer <ANTHROPIC_API_KEY>`
- Modell: Claude-Modellname direkt. Für den Classifier günstig: `claude-haiku-5-5`
  ($0.10/$0.50 je MTok). `claude-haiku-4-5-20251001` erlaubt zusätzlich `temperature: 0`.
- Caveat (laut Anthropic-Doku): `temperature < 1` → 400 auf 4.7+ (darum weglassen);
  der Kompatibilitäts-Endpoint ist für Test/Vergleich gedacht, nicht als
  Langzeit-Produktionsweg.

## Deployment auf dem Cube (`/home/claude/nucleon-run/`)
- `config.yaml`: Bridge-IMAP `127.0.0.1:2143` (STARTTLS, Bridge-CA via
  `mail.tls_ca_file`), Classifier endpoint/model/`allow_remote: true`,
  `labels.namespace: Labels/`, `draft.label: Labels/nucleon-proposed`,
  `create_missing_folders: true`.
- Secrets in `pass` (User `claude`): `nucleon/bridge-imap-test`,
  `nucleon/classifier-api-key`. Entschlüsselung funktioniert in Minimal-/cron-Umgebung.
- `run.sh`: Secrets→env, `nucleon run`, Logging (`nucleon-cron.log`), flock gegen Überlapp.
- Dauerbetrieb: systemd-System-Timer (`nucleon.service`/`nucleon.timer`) **vorbereitet**;
  Installation braucht host-root (an BOFH übergeben). Interim läuft `loop.sh` (alle 15 min)
  als `claude`, bis der Timer steht (dann `kill $(cat nucleon-run/loop.pid)`).

## Privacy-Einordnung
Wegwerf-Account + Remote-Claude ist für Test/Abnahme ok (Testdaten). Für das **echte**
Postfach ist der bewusste nächste Schritt ein lokales/eigenes Modell über dieselbe Naht
(Provider-Wechsel = reine Config), damit Mailinhalt das Gerät nicht verlässt.
