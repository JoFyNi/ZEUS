"""Test der Werkzeug-Ebene: Schreibzugriff, Verlinkung, Undo, Tool-Schleife."""
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_tools_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.agent import JarvisAgent, _parse_action  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.llm import OllamaClient, ToolsUnsupported  # noqa: E402
from jarvis.vault import extract_section, trim_at_boundary  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


LANGES_SKRIPT = """# Skript Deep Learning

## Das Problem: Funktionsapproximation
Wir suchen eine Funktion, die Eingaben auf Ausgaben abbildet.
""" + ("Fülltext zur Approximation. " * 120) + """

## Das Neuron
Ein Neuron bildet eine gewichtete Summe und schickt sie durch eine Aktivierung.
""" + ("Fülltext zum Neuron. " * 120) + """

## Warum die Nichtlinearität zwingend ist
Ohne Nichtlinearität kollabiert jedes tiefe Netz zu einer einzigen linearen Abbildung.
Das ist der zentrale Punkt: Verkettung linearer Abbildungen bleibt linear.
""" + ("Fülltext zur Nichtlinearitaet. " * 120) + """

## Der Vorwärtsdurchlauf
Schicht für Schicht werden Aktivierungen berechnet, bis die Ausgabe entsteht.
""" + ("Fülltext zum Vorwaertsdurchlauf. " * 120) + """

## Backpropagation
Der Gradient wird per Kettenregel rückwärts durch den Graphen gereicht.
"""


def build_vault() -> None:
    (VAULT / "Deep Learning").mkdir(parents=True)
    (VAULT / "Regelungstechnik").mkdir()
    (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").write_text(
        "---\ntags: [dl]\n---\n\n" + LANGES_SKRIPT, encoding="utf-8"
    )
    (VAULT / "Deep Learning" / "02_Agent_Architektur.md").write_text(
        "# Agent Architektur\n\nRAG mit FAISS und Ollama. Siehe [[01_Skript_Deep_Learning]].\n",
        encoding="utf-8",
    )
    (VAULT / "Regelungstechnik" / "PID_Regler.md").write_text(
        "# PID Regler\n\nP-, I- und D-Anteil.\n", encoding="utf-8"
    )


# --------------------------------------------------------- LLM-Attrappen
class ScriptedLLM:
    """Spielt eine feste Folge von Modellantworten ab."""

    def __init__(self):
        self.native_calls = []
        self.plan = []
        self.supports_tools = True

    def install(self, agent: JarvisAgent, plan):
        self.plan = list(plan)
        agent.llm.chat_message = self._chat_message  # type: ignore[assignment]
        agent.llm.chat = self._chat  # type: ignore[assignment]

    def _chat_message(self, messages, tools=None, temperature=None, format_json=False, **kwargs):
        if tools and not self.supports_tools:
            raise ToolsUnsupported("llama3 does not support tools")
        if not self.plan:
            return {"role": "assistant", "content": "Fertig."}
        step = self.plan.pop(0)
        if tools and self.supports_tools and step.get("tool"):
            self.native_calls.append(step["tool"])
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"function": {"name": step["tool"], "arguments": step["args"]}}],
            }
        if step.get("tool"):  # JSON-Protokoll
            return {
                "role": "assistant",
                "content": "```json\n"
                + json.dumps({"werkzeug": step["tool"], "parameter": step["args"]}, ensure_ascii=False)
                + "\n```",
            }
        return {"role": "assistant", "content": step.get("text", "Fertig.")}

    def _chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
        if format_json:
            return '{"ordner": "Deep Learning", "neu": false}'
        return "Zusammenfassung."


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.sync()
    tools = agent.tools

    # ------------------------------------------- 1. Vollständige Abschnitte
    note = tools.resolve_note("01_Skript_Deep_Learning")
    abschnitt = extract_section(note.body, "Warum die Nichtlinearität zwingend ist", 8000)
    check("Abschnitt kommt vollständig zurück",
          "kollabiert jedes tiefe Netz" in abschnitt and abschnitt.rstrip().endswith("."),
          f"{len(abschnitt)} Zeichen")
    check("Kürzung schneidet nie mitten im Wort",
          not trim_at_boundary("Wort " * 400, 500).replace(
              "[… gekürzt – vollständigen Text mit notiz_lesen anfordern]", "").rstrip().endswith("Wo"),
          "")
    gelesen = tools.notiz_lesen("01_Skript_Deep_Learning")
    check("notiz_lesen listet alle Abschnitte",
          len(gelesen.daten["abschnitte"]) >= 5, str(gelesen.daten["abschnitte"]))

    frage = "Nichtlinearität Netz linear"
    hits = index.search(frage)
    kontext = agent._build_context(hits, structural=False, frage=frage)
    check("Kontext enthält den ganzen Gedanken statt eines Fragments",
          "Verkettung linearer Abbildungen bleibt linear" in kontext, f"{len(kontext)} Zeichen")

    # -------------------------------------------------- 2. Notiz wirklich anlegen
    vorher = len(list(VAULT.rglob("*.md")))
    ergebnis = tools.notiz_erstellen(
        titel="Aktivierungsfunktionen",
        inhalt="ReLU, Sigmoid und Tanh im Vergleich.",
        tags=["dl", "grundlagen"],
        quellen=["01_Skript_Deep_Learning"],
    )
    nachher = len(list(VAULT.rglob("*.md")))
    check("Datei existiert tatsächlich auf der Platte", ergebnis.ok and nachher == vorher + 1,
          f"{vorher} → {nachher}")
    neue = VAULT / ergebnis.daten["pfad"]
    check("Landet im richtigen Ordner", neue.parent.name == "Deep Learning", ergebnis.daten["pfad"])
    inhalt = neue.read_text(encoding="utf-8")
    check("Quelle verlinkt", "[[01_Skript_Deep_Learning]]" in inhalt)
    check("Tags im Frontmatter", "- dl" in inhalt and "- grundlagen" in inhalt)
    check("Sofort auffindbar", any(
        h.metadata.get("stem") == "Aktivierungsfunktionen" for h in index.search("ReLU Sigmoid Tanh")),
        "")

    # ---------------------------------------------------------- 3. Bearbeiten
    tools.notiz_ergaenzen("Aktivierungsfunktionen", "GELU ist die moderne Variante.")
    check("Ergänzung steht in der Datei", "GELU ist die moderne Variante." in
          neue.read_text(encoding="utf-8"))
    tools.notiz_bearbeiten("Aktivierungsfunktionen", "ReLU, Sigmoid und Tanh im Vergleich.",
                           "ReLU, Sigmoid, Tanh und GELU im Vergleich.")
    check("Gezielte Ersetzung greift", "Tanh und GELU im Vergleich" in
          neue.read_text(encoding="utf-8"))
    fehl = tools.notiz_bearbeiten("Aktivierungsfunktionen", "gibt es nicht", "x")
    check("Nicht vorhandener Suchtext wird sauber abgelehnt", not fehl.ok, fehl.meldung)

    # ------------------------------------------------ 4. Verschieben + Backlinks
    verschoben = tools.notiz_verschieben("02_Agent_Architektur", ziel_ordner="Deep Learning",
                                         neuer_name="Agent_Architektur")
    check("Umbenennen funktioniert", verschoben.ok and
          (VAULT / "Deep Learning" / "Agent_Architektur.md").exists(), verschoben.meldung)
    check("Alte Datei ist weg", not (VAULT / "Deep Learning" / "02_Agent_Architektur.md").exists())

    (VAULT / "Deep Learning" / "Verweis.md").write_text(
        "# Verweis\n\nSiehe [[PID_Regler]] und [[PID_Regler|den Regler]].\n", encoding="utf-8"
    )
    index.sync()
    tools.notiz_verschieben("PID_Regler", neuer_name="PID_Regelung")
    verweis = (VAULT / "Deep Learning" / "Verweis.md").read_text(encoding="utf-8")
    check("Backlinks werden mitgezogen",
          "[[PID_Regelung]]" in verweis and "[[PID_Regelung|den Regler]]" in verweis, verweis.strip())

    # --------------------------------------------------------- 5. Verknüpfen
    verkn = tools.notizen_verknuepfen("Aktivierungsfunktionen", "01_Skript_Deep_Learning",
                                      beschreibung="Grundlagen")
    a = neue.read_text(encoding="utf-8")
    b = (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").read_text(encoding="utf-8")
    check("Beidseitige Verknüpfung geschrieben", verkn.ok and
          "[[01_Skript_Deep_Learning]]" in a and "[[Aktivierungsfunktionen]]" in b, verkn.meldung)
    nochmal = tools.notizen_verknuepfen("Aktivierungsfunktionen", "01_Skript_Deep_Learning")
    check("Doppelte Links werden vermieden",
          b.count("[[Aktivierungsfunktionen]]") == 1, nochmal.meldung)

    # ------------------------------------------------------- 6. Kaputte Links
    (VAULT / "Deep Learning" / "Kaputt.md").write_text(
        "# Kaputt\n\nVerweis auf [[Aktivierungsfunktion]].\n", encoding="utf-8"
    )
    index.sync()
    pruef = tools.links_pruefen(reparieren=False)
    check("Kaputter Link erkannt",
          any(e["link"] == "Aktivierungsfunktion" for e in pruef.daten["kaputt"]),
          str(pruef.daten["kaputt"]))
    tools.links_pruefen(reparieren=True)
    check("Kaputter Link repariert",
          "[[Aktivierungsfunktionen]]" in (VAULT / "Deep Learning" / "Kaputt.md").read_text(encoding="utf-8"))

    # ---------------------------------------------------------- 7. Papierkorb
    tools.notiz_loeschen("Kaputt", grund="Test")
    check("Notiz ist aus der Vault verschwunden",
          not (VAULT / "Deep Learning" / "Kaputt.md").exists())
    check("… liegt aber im Papierkorb", any((VAULT / "_Papierkorb").glob("Kaputt_*.md")))
    check("Papierkorb wird nicht indexiert",
          all("_Papierkorb" not in n.rel_path for n in index.notes))

    # --------------------------------------------------------- 8. Rückgängig
    vor_undo = neue.read_text(encoding="utf-8")
    tools.notiz_ergaenzen("Aktivierungsfunktionen", "VERSEHENTLICHER TEXT")
    check("Änderung ist da", "VERSEHENTLICHER TEXT" in neue.read_text(encoding="utf-8"))
    undo = tools.rueckgaengig()
    check("Rückgängig stellt den alten Stand her",
          undo.ok and "VERSEHENTLICHER TEXT" not in neue.read_text(encoding="utf-8"), undo.meldung)
    check("Inhalt exakt wiederhergestellt", neue.read_text(encoding="utf-8") == vor_undo)

    ergebnis2 = tools.notiz_erstellen(titel="Wegwerf", inhalt="Nur ein Test.")
    pfad2 = VAULT / ergebnis2.daten["pfad"]
    tools.rueckgaengig()
    check("Rückgängig entfernt neu angelegte Datei", not pfad2.exists(), ergebnis2.daten["pfad"])

    # ------------------------------------------------------ 9. Pfad-Sicherheit
    aus = tools.ordner_anlegen("../../ausbruch")
    check("Kein Ausbruch aus der Vault",
          not (TMP / "ausbruch").exists() and not (VAULT.parent / "ausbruch").exists(), aus.meldung)

    # ------------------------------------------- 10. Tool-Schleife (nativ)
    llm = ScriptedLLM()
    llm.install(agent, [
        {"tool": "notizen_suchen", "args": {"frage": "Backpropagation"}},
        {"tool": "notiz_erstellen", "args": {
            "titel": "Kettenregel", "inhalt": "Die Kettenregel ist der Kern von Backprop.",
            "tags": ["dl"]}},
        {"text": "Ich habe [[Kettenregel]] in Deep Learning angelegt."},
    ])
    antwort = agent.ask("Leg mir eine Notiz zur Kettenregel an")
    kettenregel = list(VAULT.rglob("Kettenregel*.md"))
    check("Agent legt über die Werkzeugschleife wirklich an", bool(kettenregel),
          str([p.name for p in kettenregel]))
    check("Beide Werkzeuge wurden aufgerufen",
          [c.werkzeug for c in antwort.tool_calls] == ["notizen_suchen", "notiz_erstellen"],
          str([c.werkzeug for c in antwort.tool_calls]))
    check("Geänderte Dateien werden gemeldet", bool(antwort.changed_files),
          str(antwort.changed_files))

    # -------------------------------- 11. Rückfallebene für Modelle ohne Tools
    agent2 = JarvisAgent(index=index)
    llm2 = ScriptedLLM()
    llm2.supports_tools = False
    llm2.install(agent2, [
        {"tool": "notiz_erstellen", "args": {"titel": "Lernrate", "inhalt": "Schrittweite beim Abstieg."}},
        {"text": "Notiz [[Lernrate]] angelegt."},
    ])
    antwort2 = agent2.ask("Notiz zur Lernrate anlegen")
    check("Fallback-Protokoll erkennt Werkzeugaufruf",
          agent2.tool_mode == "json-protokoll" and bool(list(VAULT.rglob("Lernrate*.md"))),
          agent2.tool_mode)
    check("Fallback liefert Abschlusstext", "Lernrate" in antwort2.text, antwort2.text[:60])

    # ---------------------------------------------- 12. JSON-Parser robust
    check("Parser findet Block mit Vortext",
          _parse_action('Ich mache das.\n```json\n{"werkzeug":"notiz_lesen","parameter":{"name":"X"}}\n```')
          == ("notiz_lesen", {"name": "X"}))
    check("Parser ignoriert reine Prosa", _parse_action("Kein Werkzeug nötig.") is None)
    check("Parser versteht nacktes JSON",
          _parse_action('{"werkzeug":"struktur_anzeigen","parameter":{}}') is not None)

    # ------------------------------------------------ 13. Parameter-Bereinigung
    r = tools.ausfuehren("notizen_suchen", {"frage": "Neuron", "anzahl": "3", "unbekannt": 1})
    check("Zahl als String wird akzeptiert", r.ok, r.meldung)
    r2 = tools.ausfuehren("gibts_nicht", {})
    check("Unbekanntes Werkzeug meldet sauber", not r2.ok, r2.meldung)

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
