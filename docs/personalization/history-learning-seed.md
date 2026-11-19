# Nucleon — Historien-Personalisierung (Design-Seed)

Status: **Design/Brainstorming** (eigener Baustein, aus dem Drive-Seed ausgelagert
2026-11-19). Zukünftige Funktion, **nachrangig** zu V0.3/Drive und zum v0.2-Live-Gang.
NICHTS wird gebaut vor Owner-Freigabe.

## Intent / Warum
Nucleon soll aus dem **eigenen** Mail- und Drive-Bestand des Nutzers lernen, **wie er
selbst** klassifiziert, ablegt und **formuliert**, und damit personalisieren:
- **Mail-Klassifikation** (wie reagiert/kategorisiert der Nutzer?),
- **thematische Drive-Zuordnung** (Platzierungs-**Modus 3** der Drive-Naht),
- **Sprache/Stimme** — Entwürfe so formulieren, **wie der Nutzer selbst schreibt**.

Der Nutzer-Bestand wird so zum „Training Space" für den persönlichen LLM-Einsatz. Ein
**bewusst freigebbarer** Initial-Vollscan + Bewertung, **getrennt für Mail und Drive**,
erzeugt die Lernbasis.

## Entscheidung 1 (Owner, 2026-11-19): (a) Profil zuerst, (b) folgt definitiv
Zwei Lernformen, als **Stufen mit kompletter Naht**:

- **(a) Verhaltensprofil — der Start.** Der Scan destilliert einsehbare **Muster**
  (Routing: „Behörden-Post → Ordner X"; Label-Gebrauch; Antwort-/Reaktionsmuster) in ein
  **lokales, lesbares Profil**. Das Modell nutzt es zur Laufzeit als Hinweise/Few-Shot-
  Beispiele; **keine** Modell-Gewichte werden verändert. Vollständig lokal und **prüfbar**
  (lesen + korrigieren). Funktioniert auch mit Remote-Modell, weil nur abstrakte Muster
  einfließen (für Remote zusätzlich minimiert). Hartregel: **nichts über den Korpus
  verlässt das Gerät für ein Remote-Modell.**

- **(b) Fein-Tuning — kommt definitiv, als bewusst aktivierbare Zukunftsfunktion.** Ein
  Modell wird **auf** dem Korpus nachtrainiert (Wissen in den Gewichten).
  - **Opt-in-Pflicht:** passiert nur, wenn der Nutzer es **bewusst aktiviert** — nie
    automatisch.
  - **Pflicht-Warnhinweis bei Aktivierung:** „Damit verlässt du den Privacy-Kern" — der
    Nutzer bestätigt diesen Tradeoff aktiv.
  - **Komplette Naht von (a):** die Personalisierungs-Schnittstelle (ein
    `PersonalizationProfile`-Provider) wird so gebaut, dass (b) als **alternativer
    Backend** sauber eintritt, ohne (a) umzubauen.
  - **HOW offen (später):** Methode (lokales vs. remote Fein-Tuning), Pipeline, Compute.
    *Fürs (b)-Design festgehalten:* es gibt eine **lokale** Fein-Tuning-Variante, bei der
    der Korpus das Gerät **nicht** verlässt — dann greift der „Kern-verlassen"-Hinweis
    evtl. nur für die Remote-Variante. Beim (b)-Design mit offenen Augen entscheiden.
  - **Scope erweitert (Owner):** (b) lernt auch **Sprache/Stimme** — Ziel: Nucleon
    formuliert Entwürfe wie der Nutzer. Tiefes Lernen (Gewichte) ist dafür der eigentliche
    Hebel, nicht nur Muster.
  - **Trainingsdaten (Owner-Haltung, „keine Gefangenen"):** die **komplette verfügbare
    Historie** aus Mail und Drive wird genutzt — **binär**: offenlegen oder nicht. Einzige
    Ausnahme: eine **Ausschlussliste** von Orten/Ordnern, die der Nutzer ausnimmt.
  - **Modell:** ein **starkes Open-Source-Modell**, **gemeinsam mit dem Owner ausgewählt**,
    **ergänzend fein-getunt** (Wissen in den Gewichten).

## Klare Naht (Privacy-Kern)
- **Opt-in, getrennt für Mail und Drive** — bewusste Freigabe je Welt.
- **Profil = lokaler, prüfbarer Artefakt** (Muster, keine gehorteten Rohinhalte).
- **Provider-Naht:** volles Profil nur mit lokalem/vertrautem Modell; ein Remote-Modell
  bekommt minimale, unsensible Merkmale oder nichts.
- **(a) hält den Kern unversehrt; (b) macht den Tradeoff sichtbar und
  aktiv-bestätigungspflichtig.**

## Konsumenten
- **Mail-Classifier** (B3-Modell der Mail-Naht) — personalisierte Klassifikation.
- **Drive-Naht Modus 3** — gelernte thematische Platzierung (bis dahin Modus 1+2).
- **Entwurfs-Formulierung** — Antworten/Entwürfe in der Stimme des Nutzers (v. a. via (b)).

## Offen (wenn dieser Baustein dran ist)
- Scan-Umfang/-Tiefe; Profil-Schema; Merkmals-Minimierung für Remote-Modelle.
- (b): Methode (lokal/remote) + exakter Warntext + Aktivierungs-Fluss.
- Auffrischung des Profils (einmalig vs. periodisch); Zusammenspiel mit der Drive-Data-Map.
- **Modellwahl (b):** starkes Open-Source-Modell gemeinsam auswählen; Fein-Tuning-Compute.
- **Ausschlussliste:** Orte/Ordner, die vom Training ausgenommen werden.
- **Berührt SGBs Schwestergraph** (Stil/Stimme, config/08): selbe Grundidee „Stimme des
  Inhabers" — beim (b)/Stil-Design auf geteilte Basis prüfen (Bridge, kein doppeltes
  Know-how).

> Reihenfolge: eigener Strang **nach** V0.3/Drive und dem v0.2-Live-Gang. Gebaut wird
> erst nach Owner-Freigabe dieses Designs.
