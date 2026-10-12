"""Tests gegen den Lauf vom 22.08.: fünfmal derselbe fehlschlagende Aufruf.

Aus dem Protokoll:

    ✔ links_pruefen – 4 offene, 0 reparierte Links.
    ✖ notizen_verknuepfen – Notiz '' nicht gefunden.
    ✖ notizen_verknuepfen – Notiz '' nicht gefunden.
    ✖ notizen_verknuepfen – Notiz '' nicht gefunden.
    …
    {"name": "notizen_verknuepfen",
     "arguments": {"nach": "", "von": ["01_Skript_Deep_Learning", …]}}

Vier Fehler in einem Bild:
  1. Es gab kein Werkzeug, um tote Links zu ENTFERNEN – nur um sie umzubiegen.
     Also hat das Modell notizen_verknuepfen zweckentfremdet.
  2. 'nach' war leer; die Meldung ("Notiz '' nicht gefunden") verriet nicht,
     welches Werkzeug gemeint war.
  3. 'von' war eine LISTE, wo ein Name erwartet wird – daraus wurde per str()
     die Zeichenfolge "['a', 'b']".
  4. Nichts hielt den identischen Aufruf auf. Fünf Versuche, kein Fortschritt.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_schleife_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").write_text(
        "# Skript Deep Learning\n\n## Neuron\nSiehe [[Gibt_Es_Nicht]] und [[Neuron_Detail]].\n",
        encoding="utf-8")
    (VAULT / "Deep Learning" / "02_Agent_Architektur.md").write_text(
        "# Agent Architektur\n\nVerweis auf [[Auch_Weg]].\n", encoding="utf-8")
    (VAULT / "Deep Learning" / "Neuron_Detail.md").write_text(
        "# Neuron Detail\n\nText.\n", encoding="utf-8")


def main() -> int:
    build_vault()
    agent = JarvisAgent(index=KnowledgeIndex(embeddings=stubs.StubEmbedding()))
    agent.llm.chat = (lambda self, m, **k: "Text.").__get__(agent.llm)
    agent.sync()
    tools = agent.tools

    # ------------------------------------------ 1. Leeres Ziel nennt das Werkzeug
    ergebnis = tools.notizen_verknuepfen(von="01_Skript_Deep_Learning", nach="")
    check("Leeres Ziel wird nicht als 'nicht gefunden' gemeldet",
          not ergebnis.ok and "links_bereinigen" in ergebnis.meldung,
          ergebnis.meldung[:120])
    check("Das richtige Werkzeug steht auch in den Daten",
          ergebnis.daten.get("richtiges_werkzeug") == "links_bereinigen")

    # ------------------------------------------------- 2. Liste statt Einzelwert
    ergebnis = tools.ausfuehren("notizen_verknuepfen", {
        "von": ["01_Skript_Deep_Learning", "02_Agent_Architektur"],
        "nach": "Neuron_Detail"})
    check("Liste statt Einzelwert wird als solche erkannt",
          not ergebnis.ok and "Liste mit 2 Einträgen" in ergebnis.meldung,
          ergebnis.meldung[:120])

    # -------------------------------------------- 3. Das fehlende Werkzeug selbst
    vorher = tools.links_pruefen(reparieren=False)
    check("Tote Links sind vorhanden",
          len(vorher.daten.get("kaputt") or []) == 2,
          str(len(vorher.daten.get("kaputt") or [])))

    ergebnis = tools.links_bereinigen(name="01_Skript_Deep_Learning")
    check("links_bereinigen räumt die Notiz auf", ergebnis.ok, ergebnis.meldung)
    text = (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").read_text(encoding="utf-8")
    check("Der tote Link ist weg", "[[Gibt_Es_Nicht]]" not in text, text.strip()[-70:])
    check("Der Text bleibt als Klartext erhalten", "Gibt_Es_Nicht" in text)
    check("Der GÜLTIGE Link bleibt unangetastet", "[[Neuron_Detail]]" in text)

    ergebnis = tools.links_bereinigen(modus="entfernen")
    check("Zweiter Durchlauf räumt den Rest", ergebnis.ok, ergebnis.meldung)
    nachher = tools.links_pruefen(reparieren=False)
    check("Danach ist kein toter Link mehr übrig",
          len(nachher.daten.get("kaputt") or []) == 0,
          str(nachher.daten.get("kaputt")))

    ergebnis = tools.links_bereinigen()
    check("Ohne Fund wird das ehrlich gemeldet",
          ergebnis.ok and "Keine toten Links" in ergebnis.meldung, ergebnis.meldung)

    # --------------------------------------------------- 4. Wiederholungssperre
    agent._aufrufe.clear()
    agent._letzte_meldung.clear()
    args = {"von": "01_Skript_Deep_Learning", "nach": ""}
    erster = agent._run_tool("notizen_verknuepfen", dict(args), None)
    zweiter = agent._run_tool("notizen_verknuepfen", dict(args), None)
    dritter = agent._run_tool("notizen_verknuepfen", dict(args), None)

    check("Der erste Versuch läuft normal durch",
          not erster.ergebnis.ok and "wiederholung" not in erster.ergebnis.daten,
          erster.ergebnis.meldung[:80])
    check("Der zweite identische Aufruf wird abgefangen",
          not zweiter.ergebnis.ok and "Identischer Aufruf" in zweiter.ergebnis.meldung,
          zweiter.ergebnis.meldung[:90])
    check("Die Sperre nennt das richtige Werkzeug",
          "links_bereinigen" in str(zweiter.ergebnis.daten.get("hinweis", "")),
          str(zweiter.ergebnis.daten.get("hinweis"))[:80])
    check("Auch der dritte wird gezählt",
          dritter.ergebnis.daten.get("wiederholung") == 3,
          str(dritter.ergebnis.daten.get("wiederholung")))

    # Ein ANDERER Aufruf desselben Werkzeugs darf laufen
    anders = agent._run_tool("notizen_verknuepfen", {
        "von": "01_Skript_Deep_Learning", "nach": "Neuron_Detail"}, None)
    check("Ein geänderter Aufruf wird nicht gesperrt",
          "Identischer Aufruf" not in anders.ergebnis.meldung, anders.ergebnis.meldung[:80])

    # -------------------------------------------------- 5. Aufgabenliste führt Buch
    liste = agent.aufgaben
    liste.laden()
    offen = liste.offene() + liste.festgefahren()
    check("Fehlschläge sind automatisch festgehalten", bool(offen),
          ", ".join(a.schluessel for a in offen))
    check("Mehrfaches Scheitern gilt als festgefahren",
          any(a.status == "festgefahren" for a in liste.alle.values()),
          ", ".join(f"{a.schluessel}={a.status}({a.versuche})" for a in liste.alle.values()))
    # Derselbe Vorgang: erst scheitern, dann gelingen -> muss abgehakt werden.
    # Ein anderer, erfolgreicher Aufruf hakt NICHTS ab; die Liste führt nur
    # das Buch über das, was einmal schiefging.
    agent._run_tool("notiz_ergaenzen", {"name": "Neuron_Detail", "inhalt": ""}, None)
    vorher = liste.alle.get("notiz_ergaenzen:neuron_detail")
    check("Der Fehlschlag steht als offener Punkt",
          vorher is not None and vorher.status == "offen",
          f"{vorher.status if vorher else 'fehlt'}")
    agent._run_tool("notiz_ergaenzen",
                    {"name": "Neuron_Detail", "inhalt": "Nachtrag."}, None)
    nach = liste.alle.get("notiz_ergaenzen:neuron_detail")
    check("Erfolg hakt genau diesen Punkt ab",
          nach is not None and nach.status == "erledigt",
          f"{nach.status if nach else 'fehlt'} – {nach.beleg[:60] if nach else ''}")
    check("Der Beleg ist die echte Werkzeugmeldung",
          nach is not None and "ergänzt" in nach.beleg, nach.beleg[:60] if nach else "")

    # Der Bericht muss beides zeigen
    bericht = liste.bericht()
    check("Der Bericht trennt offen und erledigt",
          "Erledigt" in bericht and ("[x]" in bericht or "[!]" in bericht),
          bericht.splitlines()[0])

    datei = liste.notiz_schreiben()
    pfad = VAULT / (agent.cfg.learning_folder or "_Lernen") / "Offene_Punkte.md"
    check("Die Liste liegt als Notiz in der Vault", pfad.is_file(), datei)

    # ------------------------------------------- 6. Abarbeiten hakt nur Echtes ab
    liste.eintragen("notiz_erstellen", "Gibt_Es_Wirklich_Nicht", "Notiz anlegen")
    liste.eintragen("notiz_erstellen", "Neuron_Detail", "Notiz anlegen")
    ergebnis = liste.abarbeiten()
    check("Vorhandene Notiz wird abgehakt",
          any("Neuron_Detail" in z for z in ergebnis["erledigt"]),
          str(ergebnis["erledigt"]))
    check("Fehlende Notiz bleibt offen",
          any("Gibt_Es_Wirklich_Nicht" in z for z in ergebnis["weiterhin_offen"]),
          str(ergebnis["weiterhin_offen"]))
    check("Der Beleg kommt aus der Prüfung, nicht aus einer Behauptung",
          any("existiert" in z for z in ergebnis["erledigt"]), str(ergebnis["erledigt"]))

    # Links-Punkt ist inzwischen wirklich erledigt -> muss abgehakt werden
    liste.eintragen("links_bereinigen", "", "Tote Links bereinigen")
    ergebnis = liste.abarbeiten()
    check("Erledigter Link-Punkt wird abgehakt",
          any("links_bereinigen" in z for z in ergebnis["erledigt"]),
          str(ergebnis["erledigt"]))

    # ------------------------------------------- 7. Offene Punkte im Systemprompt
    prompt = agent.system_prompt(with_tools=True)
    check("Offene Punkte stehen im Systemprompt",
          "OFFENE PUNKTE" in prompt, "fehlt" if "OFFENE PUNKTE" not in prompt else "")

    # ------------------------------------- 8. /tempo stürzte ab: wmic gibt es nicht
    # FileNotFoundError: [WinError 2] – wmic ist in Windows 11 entfernt worden.
    import subprocess as _sp

    from jarvis.hardware import _programm_ausgabe, _ram_gb, analysieren, gpu_erkennen

    check("Ein fehlendes Programm liefert leeren Text statt einer Ausnahme",
          _programm_ausgabe(["gibt_es_garantiert_nicht_xyz"]) == "")
    check("Auch mit Argumenten kein Absturz",
          _programm_ausgabe(["gibt_es_nicht", "--was", "auch", "immer"], 5) == "")

    original = _sp.run

    def _wie_windows11(*args, **kwargs):
        """Tut so, als wäre jedes externe Programm verschwunden."""
        raise FileNotFoundError(2, "Das System kann die angegebene Datei nicht finden")

    _sp.run = _wie_windows11
    try:
        check("RAM-Ermittlung überlebt ein fehlendes wmic",
              isinstance(_ram_gb(), float), str(_ram_gb()))
        check("GPU-Erkennung überlebt ein fehlendes nvidia-smi",
              gpu_erkennen() is not None)
        befund = analysieren(agent.cfg, tempo=1.2)
        check("Die Hardware-Analyse läuft trotzdem durch",
              bool(befund.bericht()), befund.bericht().splitlines()[0])
    finally:
        _sp.run = original

    # ------------- 9. Der Prüfbericht überschrieb eine fremde Notiz (24.08.)
    # 'Code-Prüfung 2026-08-24' wurde von resolve_note semantisch auf
    # 'Lauffähiger_Code_für_Agentenarchitekturen' aufgelöst - und dann
    # überschrieben. Die Notiz war weg.
    (VAULT / "Deep Learning" / "Lauffaehiger_Code_fuer_Agentenarchitekturen.md").write_text(
        "# Lauffähiger Code für Agentenarchitekturen\n\nWichtiger Inhalt von Josh.\n",
        encoding="utf-8")
    agent.sync()

    ungefaehr = tools.resolve_note("Code-Prüfung 2026-08-24")
    check("resolve_note trifft bei einem neuen Titel etwas Ähnliches",
          ungefaehr is not None, ungefaehr.stem if ungefaehr else "nichts")
    check("notiz_exakt trifft dagegen NICHTS",
          tools.notiz_exakt("Code-Prüfung 2026-08-24") is None,
          str(tools.notiz_exakt("Code-Prüfung 2026-08-24")))

    ergebnis = tools.notiz_ueberschreiben(
        name="Code-Prüfung 2026-08-24", inhalt="# Bericht\n\nNeu.\n")
    check("Überschreiben mit ungefährem Treffer wird verweigert",
          not ergebnis.ok and "exakten Namen" in ergebnis.meldung,
          ergebnis.meldung[:110])
    check("Die Verweigerung nennt, was gemeint sein könnte",
          "gemeint_vielleicht" in ergebnis.daten,
          str(ergebnis.daten.get("gemeint_vielleicht")))

    bestand = (VAULT / "Deep Learning"
               / "Lauffaehiger_Code_fuer_Agentenarchitekturen.md").read_text(encoding="utf-8")
    check("Joshs Notiz ist unversehrt", "Wichtiger Inhalt von Josh" in bestand,
          bestand[:60].replace("\n", " "))

    # Mit dem exakten Namen bzw. Pfad muss es weiterhin gehen
    ergebnis = tools.notiz_ueberschreiben(
        name="Deep Learning/Lauffaehiger_Code_fuer_Agentenarchitekturen.md",
        inhalt="# Ersetzt\n\nAbsichtlich.\n")
    check("Mit exaktem Pfad wird weiterhin überschrieben", ergebnis.ok, ergebnis.meldung)

    # Ergänzen bleibt großzügig - nur das Ganz-Ersetzen ist streng
    ergebnis = tools.notiz_ergaenzen(name="Lauffaehiger Code fuer Agentenarchitekturen",
                                     inhalt="Nachtrag.")
    check("Ergänzen bleibt tolerant gegenüber Schreibweisen", ergebnis.ok,
          ergebnis.meldung[:90])

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
