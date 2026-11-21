# LLM-Basis-Recherche — selbst-gehostetes, langzeit-konsistentes Modell

**Stand:** 2026-11-21 (Nacht-Recherche, Web gegen Primärquellen).
**Frage:** sinnvollstes **offenes, selbst-trainierbares** LLM als dauerhafte Basis für Nucleon
(Mail ⋈ Drive — Klassifikation/Routing, Fall-/Ordner-Zuordnung, Entwurf im Nutzerstil),
auf deutschem Rechts-Schriftverkehr. Kriterium: **Unabhängigkeit/Langzeit-Konsistenz vor
Spitzenleistung** (keine Provider-Abschaltungen wie bei OpenAI/Anthropic).

> Ergebnis einer delegierten Recherche. **NIE RATEN:** kein veröffentlichter Benchmark misst
> „deutscher anwaltlicher/behördlicher Schriftverkehr in DEINEM Stil" — Endauswahl muss gegen
> das eigene Korpus getestet werden (siehe § Pflicht vor Festlegung).

## 1. Lizenz — das zentrale Kriterium für Unabhängigkeit
**Apache 2.0 / MIT** = Goldstandard: unwiderruflich, kommerziell + Fine-Tuning erlaubt, keine
Nutzerzahl-/Acceptable-Use-Klausel, **kein Remote-Kill-Switch**, keine EU-Sonderregel.
Custom-Lizenzen (Gemma, Llama) sind nutzbar, widersprechen aber dem Unabhängigkeits-Ziel.

| Familie | Lizenz | Frei? | Fallstrick (EU-Einzelnutzer) |
|---|---|---|---|
| **Qwen3** (0.6–32B, dense) | **Apache 2.0** | ✅ | 2026er-Flaggschiffe teils Custom — lokale dense-Modelle aber frei |
| **Ministral 3 / Mistral 3** (3B/8B/14B, 12/2025) | **Apache 2.0** | ✅ | altes „Ministral 8B (2024)" war Research-License (non-comm.); neues „Ministral 3" = Apache 2.0 |
| **Gemma 3** | Google „Gemma Terms" | ⚠️ | breite Derivatives-Def., **Google kann Nutzung aus der Ferne einschränken** |
| **Llama 3.x/4** | Llama Community | ⚠️ | nicht OSI-frei; **AUP schließt EU-Ansässige bei MULTIMODAL aus** → Grauzone |
| **Phi-4 14B** | **MIT** | ✅ | fast reines Englisch → für Deutsch ungeeignet |
| **Teuken-7B** (Fraunhofer) | teils Apache / teils CC-BY-NC | teils | EU/deutsch, aber älter/schwächer; NC-Variante scheidet aus |
| **EuroLLM-9B** | offen | ✅ | Leistung unter Gemma-2-9B / Qwen-2.5-7B |
| **LeoLM/DiscoLM** | — | — | veraltet (2023/24), obsolet |

## 2. Deutsch-Fähigkeit
MÖVE-Benchmark „LLM für den deutschen öffentlichen Sektor" (2026, am nächsten am Use-Case):
**Mistral** führt deutsches QA an; **Qwen3** stark; Gemma solide; Llama brauchbar, aber mit
**Sprach-Rückfall-Risiko** (fällt im Deutschen teils ins Englische). Reine Englisch-Modelle
scheiden aus. → **Mistral (europäisch) und Qwen führen bei Deutsch.**

## 3. Fine-Tuning / Betrieb (Richtwerte, Q4_K_M, Inferenz)
Alle relevanten Familien: Unsloth/Axolotl/PEFT (LoRA/QLoRA/Full-FT). QLoRA(4-bit)+Unsloth =
Standard für knappe Hardware.

| Größe | VRAM Q4 (GPU) | CPU-only | Einordnung |
|---|---|---|---|
| 3–4B | ~2,5–4 GB | ~3–4 GB RAM, flüssig | läuft überall, CPU-only unkritisch |
| 7–8B | ~5–6 GB | ~5–7 GB RAM, 10–26 Tok/s | 8-GB-GPU reicht |
| 14B | ~9–10 GB (12-GB-Karte, ~23 Tok/s) | ~9–11 GB RAM, langsamer | Sweet Spot 12-GB-GPU |
| 24–32B | ~14–21 GB | — | 16–24-GB-GPU |

QLoRA-Training: 14B ~12–16 GB, 8B ~8–10 GB, 3B < 8 GB VRAM.

## 4. Klassifikation vs. Entwurf — wichtige Architektur-Einsicht
- **Fall-/Ordner-Zuordnung** (ändert sich über Zeit → neue Fälle): **RAG/Embeddings über die
  Ablage ist langzeit-konsistenter und wartbarer als ein feingetuntes Klassifikationsmodell**
  (kein Re-Training bei neuen Fällen). Fine-Tuning nur für stabile, grobe Kategorien.
- **Entwurf im Nutzerstil:** **LoRA auf eigenen beantworteten Mails** (einige hundert–tausend
  Paare) ist der Hebel; 14B > 3–8B bei Deutsch-/Stiltreue.
- **Konsequenz für Nucleon:** passt zur geplanten Naht — RAG gegen den Drive/DataMap für
  Zuordnung, LoRA für Stil. Verbindet die Mail⋈Drive-Idee direkt mit der Modellwahl.

## 5. EMPFEHLUNG
- **Basis: Ministral 3 14B (Instruct), Apache 2.0.** Wirklich frei + unwiderruflich (Kern-
  anforderung Unabhängigkeit), europäischer Hersteller mit Spitzen-Deutsch, 256k-Kontext +
  Vision (lange Schriftsatz-Threads, gescannte Briefe). Q4 ~9–10 GB → einzelne 12-GB-GPU
  (z. B. RTX 3060 12 GB), CPU-only möglich. Fine-Tuning: QLoRA/Unsloth für Stil, RAG für
  Zuordnung.
- **Kleine Variante: Ministral 3 3B (Instruct), Apache 2.0.** Gleiche Familie (ein Tokenizer/
  Prompt/Pipeline) → Langzeit-Konsistenz; übernimmt Klassifikation/Routing, eskaliert
  Entwürfe ans 14B. Q4 ~2,5–4 GB VRAM / CPU-only ~3–4 GB RAM. (8B als Mittelweg.)
- **Zweitwahl (bewusst dokumentiert): Qwen3-14B + Qwen3-4B (Apache 2.0).** Wechseln, falls im
  eigenen A/B-Test Deutsch-Entwurfsqualität besser oder maximale FT-Ökosystem-Tiefe gewünscht.
  Einziger Abwägungspunkt: chinesische Herkunft — kein Lizenz-/Rechtsproblem beim lokalen
  Self-Hosting, aber Governance-/Vertrauensaspekt im Rechtskontext.

## 6. Pflicht vor Festlegung (NIE RATEN)
Beide Kandidaten (Ministral 3 14B, Qwen3-14B) auf einem kleinen, repräsentativen Satz echter
eigener Mails blind gegeneinander testen (Klassifikationsgüte + Stiltreue), **dann** committen.
~½ Tag, ersetzt jede Benchmark-Spekulation.

## Quellen (Auswahl, Primär)
Qwen3: qwenlm.github.io/blog/qwen3, arXiv 2505.09388 · Mistral/Ministral 3: docs.mistral.ai,
HF Ministral-3-8B-Instruct-2512 · Gemma Terms: ai.google.dev/gemma/terms · Llama-4 EU-Ausschluss:
developer.meta.com/ai/llama4/use-policy · Phi-4: HF microsoft/phi-4 · Deutsch-Benchmark MÖVE:
arXiv 2606.13111; SuperGLEBer · Teuken/EuroLLM: Fraunhofer OpenGPT-X, arXiv 2506.04079.
Footprint/FT-Zahlen: Unsloth-Benchmarks, llama.cpp-CPU-Messungen (als Richtwerte gekennzeichnet).
