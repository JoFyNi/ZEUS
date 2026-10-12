"""Tests gegen den echten Fehlversuch beim ersten Selbstbau.

Aus dem Protokoll von Josh:

    ✖ fertigkeit_definieren – Fertigkeit abgelehnt: notiz_ergaenzen:
      Platzhalter {neue_fertigkeit} ist nicht in der Parameterliste
    ✔ fertigkeit_definieren – Fertigkeit 'aufgaben_kontrolle' mit 1 Schritten
      angelegt.       ->  1. notiz_ergaenzen(name='Aufgabenkontrolle',
                                             inhalt='{ergebnis}')
    ✔ fertigkeit_definieren – Fertigkeit 'entwickelung_optimierung' mit 4
      Schritten angelegt.
        2. notiz_ergaenzen(name="{ergebnis1}", inhalt="...")

Drei Fehler stecken darin:
  * {ergebnis} ohne Nummer wird nie ersetzt und landet wörtlich in der Notiz.
  * {ergebnis1} als Notizname: das Ergebnis einer Suche ist ein Text, keine
    Notiz - der Schritt scheitert im Betrieb immer.
  * Beide Fertigkeiten wurden aufgenommen, ohne je gelaufen zu sein, und das
    Modell hat danach von durchgeführten Ergänzungen berichtet.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_fert_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import AUTOMATIK_BEHAUPTUNG, JarvisAgent, ToolCallRecord  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.tools import ToolResult  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Lernen").mkdir(parents=True)
    (VAULT / "Lernen" / "Selbsterweiterung.md").write_text(
        "# Selbsterweiterung\n\n## Ansatz\nNeue Werkzeuge aus geprüften Teilen.\n",
        encoding="utf-8")
    (VAULT / "Lernen" / "Kontrolle.md").write_text(
        "# Kontrolle\n\n## Prüfen\nJede Aufgabe wird nachkontrolliert.\n",
        encoding="utf-8")
    (VAULT / "Aufgabenkontrolle.md").write_text(
        "# Aufgabenkontrolle\n\nSammelnotiz.\n", encoding="utf-8")


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "Text.").__get__(agent.llm)
    agent.sync()
    fert = agent.fertigkeiten

    # ---------------------------------------------- 1. {ergebnis} im 1. Schritt
    ergebnis = fert.definieren(
        "aufgaben_kontrolle", "Stellt sicher, dass jede Aufgabe korrekt ausgeführt wird.",
        [], ["notiz_ergaenzen(name='Aufgabenkontrolle', inhalt='{ergebnis}')"])
    check("{ergebnis} im ersten Schritt wird abgelehnt",
          not ergebnis.ok and "ERSTEN" in ergebnis.meldung, ergebnis.meldung)
    check("Die abgelehnte Fertigkeit wurde nicht gespeichert",
          not (fert.ordner / "aufgaben_kontrolle.md").exists())

    # ------------------------------- 2. Suchergebnis als Notizname wird abgelehnt
    ergebnis = fert.definieren(
        "entwickelung_optimierung", "Verbessere mein Verständnis.", [],
        ['notizen_suchen(frage="Selbsterweiterung", anzahl=5)',
         'notiz_ergaenzen(name="{ergebnis1}", inhalt="Punkte zur Verbesserung:")',
         'notizen_suchen(frage="Kontrolle", anzahl=5)',
         'notiz_ergaenzen(name="{ergebnis2}", inhalt="Punkte zur Verbesserung:")'])
    check("Suchergebnis als Notizname wird abgelehnt",
          not ergebnis.ok and "liefert keinen Notiznamen" in ergebnis.meldung,
          ergebnis.meldung[:110])
    check("Die Ablehnung erklärt das richtige Muster",
          "inhalt" in str(ergebnis.daten.get("hilfe", "")))

    # ------------------------------------------ 3. Verweis nach vorn wird erkannt
    ergebnis = fert.definieren(
        "vorgriff", "Verweist auf einen späteren Schritt.", ["thema"],
        ['notiz_erstellen(titel="{thema}", inhalt="{ergebnis3}")',
         'notizen_suchen(frage="{thema}")'])
    check("Verweis auf einen späteren Schritt wird abgelehnt",
          not ergebnis.ok and "noch kein" in ergebnis.meldung, ergebnis.meldung[:110])

    # ------------------------------------------- 4. Erfundener Platzhalter (Altfall)
    ergebnis = fert.definieren(
        "erfunden", "Erfundener Platzhalter.", [],
        ['notiz_ergaenzen(name="Aufgabenkontrolle", inhalt="{neue_fertigkeit}")'])
    check("Erfundener Platzhalter wird weiterhin abgelehnt",
          not ergebnis.ok and "neue_fertigkeit" in ergebnis.meldung, ergebnis.meldung[:90])

    # ------------------------------------------------- 5. Pflichtparameter fehlen
    ergebnis = fert.definieren(
        "unvollstaendig", "Ohne Pflichtparameter.", [],
        ['notiz_ergaenzen(inhalt="Text ohne Ziel")'])
    check("Fehlender Pflichtparameter wird erkannt",
          not ergebnis.ok and "Pflichtparameter" in ergebnis.meldung, ergebnis.meldung[:90])

    # ----------------------------------------------- 6. Die RICHTIGE Form geht durch
    ergebnis = fert.definieren(
        "thema_aufarbeiten", "Recherchiert ein Thema und legt eine Übersicht an.",
        ["thema"],
        ['notizen_suchen(frage="{thema}", anzahl=5)',
         'notiz_erstellen(titel="Übersicht {thema}", inhalt="{ergebnis1}")'])
    check("Korrekt gebaute Fertigkeit wird aufgenommen", ergebnis.ok, ergebnis.meldung[:110])
    check("Die Meldung sagt ausdrücklich 'angelegt, nicht ausgeführt'",
          "ANGELEGT" in ergebnis.meldung and ergebnis.daten.get("ausgefuehrt") is False,
          ergebnis.meldung[:110])
    check("Der Probelauf ist protokolliert",
          len(ergebnis.daten.get("probelauf") or []) == 2,
          str(ergebnis.daten.get("probelauf")))

    notiz = (fert.ordner / "thema_aufarbeiten.md").read_text(encoding="utf-8")
    check("Der Probelauf steht in der Notiz", "## Probelauf" in notiz)
    check("Der schreibende Schritt wurde nur simuliert", "simuliert" in notiz)
    check("Der Probelauf hat nichts angelegt",
          not (VAULT / "Übersicht Probe-thema.md").exists()
          and not list(VAULT.glob("**/Übersicht Probe*.md")))

    # ------------------------------------- 7. {ergebnis} mit Vorschritt wird repariert
    ergebnis = fert.definieren(
        "reparabel", "Nutzt {ergebnis} ohne Nummer.", ["thema"],
        ['notizen_suchen(frage="{thema}", anzahl=3)',
         'notiz_erstellen(titel="Bericht {thema}", inhalt="{ergebnis}")'])
    check("{ergebnis} nach einem Vorschritt wird repariert statt abgelehnt",
          ergebnis.ok and any("ergebnis1" in k for k in ergebnis.daten.get("korrekturen", [])),
          str(ergebnis.daten.get("korrekturen")))

    # ------------------- 7b. Verworfener Schritt verschiebt die Nummerierung
    # Schritt 2 ist nicht lesbar und fliegt raus. Der Bezug {ergebnis3} in
    # Schritt 4 zeigte danach auf den falschen Schritt - er muss auf {ergebnis2}
    # nachgezogen werden, weil Schritt 3 nach dem Verwerfen auf Platz 2 liegt.
    ergebnis = fert.definieren(
        "nachgezogen", "Bezug nach einem verworfenen Schritt.", ["thema"],
        ['notizen_suchen(frage="{thema}", anzahl=3)',
         'kaputt(',
         'notizen_suchen(frage="Kontrolle", anzahl=3)',
         'notiz_erstellen(titel="Bericht {thema}", inhalt="{ergebnis3}")'])
    schritte_neu = ergebnis.daten.get("schritte") or []
    check("Fertigkeit trotz eines verworfenen Schritts angelegt", ergebnis.ok,
          ergebnis.meldung[:110])
    check("Der Bezug wurde auf die neue Schrittnummer nachgezogen",
          any("{ergebnis2}" in s for s in schritte_neu)
          and not any("{ergebnis3}" in s for s in schritte_neu),
          " | ".join(schritte_neu))

    # Ein Bezug AUF den verworfenen Schritt darf dagegen nicht durchgehen -
    # dieses Ergebnis wird es nie geben.
    ergebnis = fert.definieren(
        "totbezug", "Bezug auf einen verworfenen Schritt.", ["thema"],
        ['notizen_suchen(frage="{thema}", anzahl=3)',
         'kaputt(',
         'notiz_erstellen(titel="Bericht {thema}", inhalt="{ergebnis2}")'])
    check("Bezug auf einen verworfenen Schritt wird abgelehnt",
          not ergebnis.ok and "verworfen" in ergebnis.meldung, ergebnis.meldung[:120])
    check("Die abgelehnte Fertigkeit liegt nicht auf Platte",
          not (fert.ordner / "totbezug.md").exists())

    # -------------------------------------------- 8. Ausführung: Text statt Name
    ergebnis = fert.ausfuehren("thema_aufarbeiten", {"thema": "Kontrolle"})
    check("Fertigkeit läuft durch", ergebnis.ok, ergebnis.meldung)
    erzeugt = list(VAULT.glob("**/Übersicht*Kontrolle*.md"))
    check("Die Übersicht ist wirklich entstanden", bool(erzeugt),
          ", ".join(p.name for p in erzeugt))
    if erzeugt:
        text = erzeugt[0].read_text(encoding="utf-8")
        check("Kein Platzhalter im Ergebnis", "{ergebnis" not in text and "{thema}" not in text)
        check("Der Suchtreffer steht als lesbarer Text drin",
              "Kontrolle" in text and "[[" in text, text[-160:].replace("\n", " "))

    # -------------------- 9. Bericht widerspricht "wurde durchgeführt / regelmäßig"
    aufruf = ToolCallRecord(
        werkzeug="fertigkeit_definieren",
        parameter={"name": "thema_aufarbeiten"},
        ergebnis=ToolResult(True, "Fertigkeit 'thema_aufarbeiten' mit 2 Schritten ANGELEGT.",
                            {"ausgefuehrt": False, "probelauf": ["1. notizen_suchen → ok"]}),
    )
    behauptung = ("Die Notizen und Ergänzungen wurden entsprechend durchgeführt. "
                  "Diese Fertigkeit wird nun regelmäßig ausgeführt.")
    check("Automatik-Behauptung wird erkannt", bool(AUTOMATIK_BEHAUPTUNG.search(behauptung)))
    bericht = agent._tatsachen_anhaengen(behauptung, [aufruf])
    check("Der Bericht widerspricht der Behauptung",
          "geht zu weit" in bericht and "keine automatische Wiederholung" in bericht)
    check("Der Bericht sagt, dass nichts geändert wurde",
          "nichts geändert" in bericht)

    # Ehrlicher Text darf nicht angemeckert werden
    sachlich = agent._tatsachen_anhaengen("Ich habe den Ablauf hinterlegt.", [aufruf])
    check("Sachlicher Text bekommt keine Rüge", "geht zu weit" not in sachlich)
    check("Sachlicher Text bekommt trotzdem den Faktenbericht", "angelegt" in sachlich)

    # ------------ 10. Schon vorhandene, vor der Verschärfung angelegte Fertigkeit
    # Genau die zwei Dateien, die jetzt in Joshs Vault liegen.
    fert.ordner.mkdir(parents=True, exist_ok=True)
    (fert.ordner / "aufgaben_kontrolle.md").write_text(
        "---\nfertigkeit: aufgaben_kontrolle\n"
        'beschreibung: "Stellt sicher, dass jede Aufgabe korrekt ausgeführt wird."\n'
        "parameter: []\n---\n\n## Ablauf\n"
        "1. notiz_ergaenzen(name='Aufgabenkontrolle', inhalt='{ergebnis}')\n",
        encoding="utf-8")
    (fert.ordner / "entwickelung_optimierung.md").write_text(
        "---\nfertigkeit: entwickelung_optimierung\n"
        'beschreibung: "Verbessere mein Verständnis."\nparameter: []\n---\n\n## Ablauf\n'
        '1. notizen_suchen(frage="Selbsterweiterung", anzahl=5)\n'
        '2. notiz_ergaenzen(name="{ergebnis1}", inhalt="Punkte:")\n',
        encoding="utf-8")
    fert.laden()

    check("Kaputte Altfertigkeiten werden beim Laden erkannt",
          set(fert.defekt) == {"aufgaben_kontrolle", "entwickelung_optimierung"},
          ", ".join(sorted(fert.defekt)))
    check("Sie tauchen nicht als aufrufbare Fertigkeit auf",
          "aufgaben_kontrolle" not in fert.alle and "entwickelung_optimierung" not in fert.alle)
    check("Die intakte Fertigkeit bleibt nutzbar", "thema_aufarbeiten" in fert.alle)

    gesperrt = fert.ausfuehren("aufgaben_kontrolle")
    check("Der Aufruf nennt den Grund statt 'unbekannt'",
          not gesperrt.ok and "gesperrt" in gesperrt.meldung, gesperrt.meldung[:110])

    bericht = fert.pruefbericht()
    check("Der Prüfbericht trennt intakt und gesperrt",
          "Einsatzbereit" in bericht and "Gesperrt" in bericht
          and "thema_aufarbeiten" in bericht and "aufgaben_kontrolle" in bericht)

    # Das Werkzeugverzeichnis des Agenten darf die kaputten nicht anbieten
    namen = {w["function"]["name"] for w in agent._werkzeugliste()}
    check("Gesperrte Fertigkeiten stehen nicht in der Werkzeugliste",
          "aufgaben_kontrolle" not in namen and "thema_aufarbeiten" in namen)

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
