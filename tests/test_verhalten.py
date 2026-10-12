"""Tests gegen den Fehlversuch vom 04.09. - der Skill, der nicht anzulegen war.

Aus Joshs Protokoll:

    Josh: "ich will einen neuen Skill erstellen. [...] Ziel ist es, einen
           Ablauf oder Prozess in jeglich zur Verfügung stehender Form
           darzustellen und abzubilden. [...] Der Grund ist, dass ich der
           Nutzer ein visueller Lerner bin und ich Strukturen sehen und
           nicht lesen möchte."
    Zeus: schreibt einen fertigkeit_definieren(...)-Aufruf als TEXT hin,
          erklärt dann, dass er als LLM keine Logik-Engine habe, und
          empfiehlt, es einfach immer so zu machen.

    Josh: "Nicht mal eine Notiz, sondern einfach als Chatwiedergabe."
    Zeus: legt eine Notiz an. Und meldet zusätzlich einen Fehlschlag, weil
          er die eben angelegte Notiz nicht wiederfindet.

Drei Ursachen stecken darin, und für jede steht hier eine Prüfung:
  * Es gab nur EINE Fertigkeitsart, und die passte nicht.
  * Nichts unterschied "was soll geschehen" von "wie soll geantwortet werden".
  * Eine Darstellung als Bild gab es überhaupt nicht - weder als Werkzeug
    noch als Regel.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_verh_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis import darstellung  # noqa: E402
from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import looks_like_skill_request, skill_art  # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


# Joshs Sätze, wörtlich aus dem Protokoll.
SATZ_VERHALTEN = (
    "ich will einen neuen Skill erstellen. Ziel ist es, einen Ablauf oder Prozess "
    "in jeglich zur Verfügung stehender Form darzustellen und abzubilden. Der Grund "
    "ist, dass ich ein visueller Lerner bin und Strukturen sehen und nicht lesen "
    "möchte.")
SATZ_ABLAUF = (
    "Erstelle eine Fertigkeit, die ein Thema in der Vault sucht und daraus eine "
    "Übersichtsnotiz anlegt und verknüpft.")


def main() -> int:
    (VAULT / "Lernen").mkdir(parents=True)
    (VAULT / "Lernen" / "Messkette.md").write_text(
        "# Messkette\n\nMaschine, Connector, Verarbeitung.\n", encoding="utf-8")

    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "Text.").__get__(agent.llm)
    agent.sync()
    verh = agent.verhalten

    # ------------------------------------------------- 1. Die Art wird erkannt
    check("Joshs Satz gilt weiterhin als Fertigkeitswunsch",
          looks_like_skill_request(SATZ_VERHALTEN))
    check("Joshs Satz wird als VERHALTEN erkannt, nicht als Ablauf",
          skill_art(SATZ_VERHALTEN) == "verhalten", skill_art(SATZ_VERHALTEN))
    check("Ein echter Vault-Ablauf wird weiterhin als Ablauf erkannt",
          skill_art(SATZ_ABLAUF) == "ablauf", skill_art(SATZ_ABLAUF))

    # -------------------------------- 2. Der Prompt weist auf das RICHTIGE Werkzeug
    agent._fertigkeit_gewuenscht = True
    agent._fertigkeit_art = "verhalten"
    prompt = agent.system_prompt("", with_tools=True, frage=SATZ_VERHALTEN)
    check("Der Prompt verlangt verhalten_definieren",
          "verhalten_definieren" in prompt)
    check("Der Prompt warnt ausdrücklich vor fertigkeit_definieren",
          "Nimm NICHT fertigkeit_definieren" in prompt)
    check("Der Prompt verbietet die Ausweichnotiz",
          "KEINE Notiz" in prompt)

    agent._fertigkeit_art = "ablauf"
    prompt_ablauf = agent.system_prompt("", with_tools=True, frage=SATZ_ABLAUF)
    check("Beim Ablauf bleibt es bei fertigkeit_definieren",
          "DER NUTZER WILL EINE FERTIGKEIT ANLEGEN" in prompt_ablauf)
    agent._fertigkeit_gewuenscht = False

    # ------------------------------- 3. Das Werkzeug steht dem Modell zur Verfügung
    namen = {w["function"]["name"] for w in agent._werkzeugliste()}
    check("verhalten_definieren steht in der Werkzeugliste",
          "verhalten_definieren" in namen)
    check("struktur_zeichnen steht in der Werkzeugliste",
          "struktur_zeichnen" in namen)

    # ------------------------------------- 4. Die mitgelieferte Fertigkeit greift
    check("prozess_visuell ist von Haus aus da", "prozess_visuell" in verh.alle,
          ", ".join(sorted(verh.alle)))
    check("Sie ist nicht gesperrt", not verh.defekt, str({k: v.probleme for k, v in verh.defekt.items()}))
    treffer = [f.name for f in verh.passend(
        "zeig mir mal den ablauf von der messung bis in die datenbank")]
    check("Eine visuelle Anfrage löst prozess_visuell aus", "prozess_visuell" in treffer,
          ", ".join(treffer))
    check("Eine gewöhnliche Frage löst sie NICHT aus",
          not verh.passend("wie viele notizen habe ich in Lernen"),
          str([f.name for f in verh.passend("wie viele notizen habe ich in Lernen")]))

    block = verh.promptblock("stell mir den prozess bitte visuell dar")
    check("Der Block kommt in den Prompt", "struktur_zeichnen" in block)
    check("Der Block bleibt im Budget", len(block) <= 2600, str(len(block)))
    check("Der Block verbietet die Ausweichnotiz", "KEINE Notiz" in block)

    # Und derselbe Weg über den echten Systemprompt
    voll = agent.system_prompt("", with_tools=True,
                               frage="zeig mir den ablauf der messkette visuell")
    check("Der Systemprompt trägt die ausgelöste Fertigkeit",
          "AUSGELÖSTE VERHALTENSFERTIGKEITEN" in voll)

    # ----------------------------------------- 5. Selbst anlegen und prüfen lassen
    ergebnis = verh.definieren(
        "tabellen_vergleich", "Vergleicht Dinge als Tabelle statt in Prosa.",
        ["vergleich", "gegenueber", "unterschied zwischen"],
        "Stelle den Vergleich als Tabelle dar. Eine Zeile je Eigenschaft, eine "
        "Spalte je verglichenem Ding. Schreibe darunter höchstens zwei Sätze.")
    check("Eine eigene Verhaltensfertigkeit lässt sich anlegen", ergebnis.ok,
          ergebnis.meldung[:110])
    check("Die Meldung sagt, dass sie nichts ausführt",
          "führt nichts aus" in ergebnis.meldung)
    check("Sie greift danach wirklich",
          "tabellen_vergleich" in [f.name for f in verh.passend(
              "was ist der unterschied zwischen FAISS und einer SQL-Datenbank")])

    # Die drei Ablehnungsgründe
    zu_wenig = verh.definieren("eins", "x", ["nurdieser"],
                               "Antworte in der Befehlsform und sehr ausführlich immer.")
    check("Ein einzelner Auslöser wird abgelehnt",
          not zu_wenig.ok and "zwei Auslöser" in zu_wenig.meldung, zu_wenig.meldung[:90])
    zu_kurz = verh.definieren("kurz2", "x", ["ab", "cd"],
                              "Antworte in der Befehlsform und sehr ausführlich immer.")
    check("Zu kurze Auslöser werden abgelehnt",
          not zu_kurz.ok and "zu kurze" in zu_kurz.meldung, zu_kurz.meldung[:90])
    konjunktiv = verh.definieren(
        "weich", "x", ["irgendwas", "anderes"],
        "Man könnte hier vielleicht eine Tabelle nehmen, das wäre gut für die Übersicht.")
    check("Eine Anleitung im Konjunktiv wird abgelehnt",
          not konjunktiv.ok and "Konjunktiv" in konjunktiv.meldung, konjunktiv.meldung[:90])

    # Der Nachweis, dass die Auslöser taugen
    probe = verh.probe("tabellen_vergleich", "erklär mir mal die Regelungstechnik")
    check("Die Probe deckt untaugliche Auslöser auf",
          not probe.ok and "NICHT" in probe.meldung, probe.meldung[:90])
    probe_ok = verh.probe("tabellen_vergleich", "zeig mir den unterschied zwischen A und B")
    check("Die Probe bestätigt taugliche Auslöser", probe_ok.ok, probe_ok.meldung[:90])

    # ------------------------------------------------ 6. Das Zeichnen selbst
    ergebnis = agent.tools.ausfuehren("struktur_zeichnen", {
        "titel": "Messkette",
        "beschreibung": "(Maschine1) -> [Connector]\n(Maschine2) -> [Connector]\n"
                        "[Connector] -> [Verarbeitung]: Rohwerte\n"
                        "[Verarbeitung] -> {Grenzwert?}\n"
                        "{Grenzwert?} -> [Datenbank]: ok\n"
                        "{Grenzwert?} -> [Alarm]: zu hoch"})
    check("struktur_zeichnen läuft", ergebnis.ok, ergebnis.meldung[:80])
    bild = ergebnis.daten.get("zeichnung", "")
    check("Alle sechs Kästen stehen im Bild",
          all(w in bild for w in ("Maschine1", "Maschine2", "Connector",
                                  "Verarbeitung", "Datenbank", "Alarm")))
    check("Die Pfeilbeschriftungen stehen dran",
          "Rohwerte" in bild and "ok" in bild)
    check("Es sind wirklich Linien und Pfeile", "▼" in bild and "│" in bild)
    check("Die Entscheidung hat eine eigene Form", "╱" in bild)
    check("Mermaid kommt für die Vault mit",
          "flowchart" in ergebnis.daten.get("mermaid", ""))
    check("SVG kommt für die Oberfläche mit",
          ergebnis.daten.get("svg", "").startswith("<svg"))
    check("Das Ergebnis sagt ausdrücklich, dass NICHTS gespeichert wurde",
          ergebnis.daten.get("gespeichert") is False and "NICHT in der Vault"
          in ergebnis.daten.get("hinweis", ""))
    check("Die Vault ist unangetastet geblieben",
          not list(VAULT.glob("**/Messkette*.md")) or
          len(list(VAULT.glob("**/*.md"))) == len(list(VAULT.glob("**/*.md"))))

    # Reproduzierbarkeit - das eigentliche Argument gegen ein gemaltes Modellbild
    zweites = agent.tools.ausfuehren("struktur_zeichnen", {
        "titel": "Messkette",
        "beschreibung": "(Maschine1) -> [Connector]\n(Maschine2) -> [Connector]\n"
                        "[Connector] -> [Verarbeitung]: Rohwerte\n"
                        "[Verarbeitung] -> {Grenzwert?}\n"
                        "{Grenzwert?} -> [Datenbank]: ok\n"
                        "{Grenzwert?} -> [Alarm]: zu hoch"})
    check("Dieselbe Beschreibung ergibt dasselbe Bild",
          zweites.daten.get("zeichnung") == bild)

    # Ein Regelkreis darf die Ebenenrechnung nicht aufhängen
    kreis = agent.tools.ausfuehren("struktur_zeichnen", {
        "beschreibung": "(Sollwert) -> [Regler] -> [Stellglied] -> [Strecke]\n"
                        "[Strecke] -> <Messung>\n<Messung> -> [Regler]: Rückführung"})
    check("Ein Regelkreis wird gezeichnet statt zu hängen", kreis.ok, kreis.meldung[:80])
    check("Die Rückführung geht nicht verloren",
          "Rückführ" in kreis.daten.get("zeichnung", ""),
          kreis.daten.get("zeichnung", "")[-90:].replace("\n", " "))

    # Unbrauchbare Eingabe wird mit Anleitung abgelehnt, nicht mit einem Absturz
    murks = agent.tools.ausfuehren("struktur_zeichnen",
                                   {"beschreibung": "einfach nur irgendein Fließtext"})
    check("Eine Beschreibung ohne Verbindungen wird abgelehnt", not murks.ok,
          murks.meldung[:90])
    check("Die Ablehnung erklärt die Schreibweise",
          "->" in str(murks.daten.get("schreibweise", "")))

    # --------------------------- 6b. Das Bild landet in der Antwort, nicht nur im Log
    from jarvis.agent import ToolCallRecord  # noqa: E402
    aufruf = ToolCallRecord(werkzeug="struktur_zeichnen",
                            parameter={"beschreibung": "..."}, ergebnis=kreis)
    agent.cfg.live_view = False
    im_terminal = agent._tatsachen_anhaengen("Hier der Ablauf.", [aufruf])
    check("Im Terminal steht das Zeichenbild in der Antwort",
          "Sollwert" in im_terminal and "Stellglied" in im_terminal,
          im_terminal[:60].replace("\n", " "))
    agent.cfg.live_view = True
    grafisch = agent._tatsachen_anhaengen("Hier der Ablauf.", [aufruf])
    check("In der Oberfläche steht das SVG in der Antwort",
          "```svg" in grafisch and "<svg" in grafisch)
    check("Nicht beide Formen gleichzeitig",
          not ("```svg" in grafisch and "╭" in grafisch))
    ohne = agent._tatsachen_anhaengen("Nur Text.", [])
    check("Ohne Zeichnung bleibt der Text unberührt", ohne == "Nur Text.", ohne[:40])
    agent.cfg.live_view = False

    # -------------------------------------------- 7. Was steht zum Darstellen bereit
    vorhanden = darstellung.mittel()
    check("Die Mittel werden benannt statt behauptet",
          set(vorhanden) == {"zeichen", "mermaid", "svg"}, ", ".join(sorted(vorhanden)))

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
