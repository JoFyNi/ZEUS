"""Tests für die gemeldeten Fehlschläge: erfundene Notiznamen und Meta-Fragen.

Aus dem Protokoll:
  ✖ Erfolgreiche Optimierung von nicht-linearen Modellen:
    Keine Notiz namens '…' gefunden.
Das Modell formuliert einen Titel statt den Dateinamen zu übernehmen. Und die
Nachfrage "Kannst du mir sagen wieso du es nicht schaffst … zu löschen?" wurde
als Schreibauftrag behandelt statt beantwortet.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_namen_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.planner import Operation, WritePlan, looks_like_write_request  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Agenten").mkdir()
    (VAULT / "Deep Learning" / "Optimierung_nichtlinearer_Modelle.md").write_text(
        "---\ntags: [dl]\ntitle: \"Optimierung nichtlinearer Modelle\"\n---\n\n"
        "# Optimierung nichtlinearer Modelle\n\n"
        "## Ansatz\nRegularisierung und Architektur.\n\n"
        "## Ausblick\nWas noch offen ist.\n",
        encoding="utf-8")
    (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").write_text(
        "# Skript Deep Learning\n\n## Neuron\nGewichtete Summe.\n", encoding="utf-8")
    (VAULT / "Agenten" / "Lernende_Agenten.md").write_text(
        "---\ntitle: \"Evaluation eines lernenden Agenten\"\n---\n\n"
        "# Evaluation eines lernenden Agenten\n\nBeobachtung und Rückmeldung.\n",
        encoding="utf-8")


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "## Kurzfassung\nText. " + "Inhalt. " * 60
                      ).__get__(agent.llm)
    agent.sync()
    tools = agent.tools

    # ================================================= 1. Namensauflösung
    faelle = [
        ("Optimierung_nichtlinearer_Modelle", "exakter Dateiname"),
        ("Optimierung nichtlinearer Modelle", "Titel mit Leerzeichen statt Unterstrichen"),
        ("optimierung nichtlinearer modelle", "Kleinschreibung"),
        ("Erfolgreiche Optimierung von nicht-linearen Modellen", "umformulierter Titel (dein Fall)"),
        ("Optimierung nicht-linearer Modelle", "Bindestrich-Variante"),
        ("Optimierung_nichtlinearer_Modelle.md", "mit Dateiendung"),
        ("[[Optimierung nichtlinearer Modelle]]", "als Wikilink geschrieben"),
        ("Deep Learning/Optimierung_nichtlinearer_Modelle.md", "voller Pfad"),
    ]
    for eingabe, beschreibung in faelle:
        note = tools.resolve_note(eingabe)
        check(f"Findet Notiz: {beschreibung}",
              note is not None and note.stem == "Optimierung_nichtlinearer_Modelle",
              f"'{eingabe}' → {note.stem if note else 'NICHTS'}")

    titel = tools.resolve_note("Evaluation eines lernenden Agenten")
    check("Findet über den Frontmatter-Titel",
          titel is not None and titel.stem == "Lernende_Agenten",
          titel.stem if titel else "NICHTS")

    unsinn = tools.resolve_note("Quantenfeldtheorie in der Molekularküche")
    check("Wirklich Fremdes wird nicht erzwungen", unsinn is None,
          unsinn.stem if unsinn else "None")

    vorschlaege = tools.aehnliche_namen("Optimierung von Modellen")
    check("Vorschläge werden geliefert",
          "Optimierung_nichtlinearer_Modelle" in vorschlaege, str(vorschlaege[:3]))

    # =========================================== 2. Plan mit erfundenem Namen
    plan = WritePlan(absicht="Test", operationen=[
        Operation(aktion="notiz_ergaenzen",
                  parameter={"name": "Erfolgreiche Optimierung von nicht-linearen Modellen"},
                  auftrag="Ergänze die Erkenntnisse"),
    ])
    probleme = agent.planner.namen_aufloesen(plan)
    check("Erfundener Name wird VOR der Ausführung repariert",
          not probleme and plan.operationen[0].parameter["name"] == "Optimierung_nichtlinearer_Modelle",
          str(plan.operationen[0].parameter.get("name")))

    plan2 = WritePlan(absicht="Test", operationen=[
        Operation(aktion="notiz_loeschen", parameter={"name": "Gibt es wirklich nicht xyz"}),
        Operation(aktion="notiz_verschieben",
                  parameter={"name": "01_Skript_Deep_Learning", "ziel_ordner": "Agenten"}),
    ])
    probleme2 = agent.planner.namen_aufloesen(plan2)
    check("Unauflösbarer Name wird ausgesondert",
          len(probleme2) == 1 and len(plan2.operationen) == 1,
          f"{len(probleme2)} Problem(e), {len(plan2.operationen)} Operation(en) übrig")
    check("Fehlermeldung nennt echte Alternativen",
          bool(probleme2[0].get("vorschlaege")), str(probleme2[0].get("vorschlaege", [])[:3]))
    check("Die gültige Operation bleibt erhalten",
          plan2.operationen[0].aktion == "notiz_verschieben")

    # ------------------------------------ Ende-zu-Ende mit erfundenem Namen
    def plan_llm(plan_json):
        def chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
            if format_json:
                return json.dumps(plan_json, ensure_ascii=False)
            return "## Kurzfassung\nNeuer Text. " + "Inhalt. " * 60
        return chat

    agent.llm.chat = plan_llm({
        "absicht": "Erkenntnisse ergänzen",
        "operationen": [{
            "aktion": "notiz_ergaenzen",
            "parameter": {"name": "Erfolgreiche Optimierung von nicht-linearen Modellen"},
            "auftrag": "Ergänze die neuen Erkenntnisse",
        }],
    }).__get__(agent.llm)
    antwort = agent.ask("ergänze die Erkenntnisse zur Optimierung nichtlinearer Modelle")
    inhalt = (VAULT / "Deep Learning" / "Optimierung_nichtlinearer_Modelle.md").read_text(
        encoding="utf-8")
    check("Ergänzung landet trotz erfundenem Namen in der echten Notiz",
          "Neuer Text" in inhalt, antwort.text[:90])
    check("Kein Fehlschlag im Bericht", "fehlgeschlagen" not in antwort.text,
          antwort.text[:90])

    # -------------------------- Verschieben mit Titel statt Dateiname
    agent.llm.chat = plan_llm({
        "absicht": "Verschieben",
        "operationen": [{
            "aktion": "notiz_verschieben",
            "parameter": {"name": "Evaluation eines lernenden Agenten",
                          "ziel_ordner": "Deep Learning"},
        }],
    }).__get__(agent.llm)
    agent.ask("verschiebe die Evaluation eines lernenden Agenten nach Deep Learning")
    check("Verschieben klappt über den Titel",
          (VAULT / "Deep Learning" / "Lernende_Agenten.md").exists(),
          str(sorted(p.name for p in VAULT.rglob("*.md") if ".jarvis" not in p.parts)))

    # ============================================ 3. Meta-Fragen erkennen
    fragen = [
        "Kannst du mir sagen wieso du es nicht schaffst manche Daten zu löschen "
        "oder zu verschieben bzw. zu bearbeiten?",
        "Warum hast du die Notizen nicht verschoben?",
        "Wieso klappt das Löschen nicht?",
        "Weshalb konntest du die Datei nicht bearbeiten?",
        "Erkläre mir, warum das Verschieben fehlgeschlagen ist.",
        "Was war da schief beim Anlegen?",
    ]
    for frage in fragen:
        check(f"Frage bleibt Frage: „{frage[:45]}…“",
              not looks_like_write_request(frage))

    auftraege = [
        "verschiebe die Notiz nach Deep Learning",
        "Kannst du die Notiz nach Agenten verschieben?",
        "lösche die alte Notiz",
        "erstelle eine Struktur für alle Punkte",
        "ergänze im Skript den Abschnitt Ausblick",
        "führe die Agenten-Notizen zusammen",
        "lege eine Notiz zu Backpropagation an",
        "benenne die Datei um",
        "teile das lange Skript auf",
        "räume den Ordner Deep Learning auf",
    ]
    for auftrag in auftraege:
        check(f"Auftrag bleibt Auftrag: „{auftrag[:45]}…“",
              looks_like_write_request(auftrag))

    # ------------------------------- Meta-Frage läuft durch die Antwortstrecke
    gesehen = {"plan": False}

    def chat_mit_marker(self, messages, stream_cb=None, temperature=None,
                        format_json=False, **kwargs):
        if format_json:
            gesehen["plan"] = True
            return "{}"
        return "Weil die genannte Notiz nicht existierte, Sir."

    agent.llm.chat = chat_mit_marker.__get__(agent.llm)
    agent.llm.chat_message = (lambda self, messages, tools=None, **k: {
        "role": "assistant", "content": "Weil die genannte Notiz nicht existierte, Sir."}
    ).__get__(agent.llm)
    antwort = agent.ask("Kannst du mir sagen wieso du es nicht schaffst manche Daten zu löschen?")
    check("Meta-Frage wird beantwortet statt geplant",
          not gesehen["plan"] and antwort.kind != "plan" and "existierte" in antwort.text,
          f"kind={antwort.kind}, Plan angefragt={gesehen['plan']}")

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
