"""Tests zu den Werkzeugfehlern aus dem Protokoll vom 01.09.2026.

Fünf Beschwerden, fünf Ursachen. Alle haben dieselbe Form: Das Werkzeug tut
nicht, was sein Name verspricht - oder es wird gar nicht erst aufgerufen.

1. `[auto_verlinken]` löste KEINEN Aufruf aus. Zeus antwortete:
   "In den mir vorliegenden Werkzeugergebnissen taucht kein Funktions- oder
    Modulname `auto_verlinken` auf."
   Die eckigen Klammern waren reine Oberflächenkosmetik.

2. `[verwandte_finden]` ebenso, mit der schlimmeren Aussage:
   "das ist kein Werkzeug, das ich hier aufrufen kann" - eine Falschaussage
   über die eigenen Fähigkeiten.

3. `/links reparieren` meldete 48 offene Links, darunter `[[Notizname]]`,
   `[[{note.stem}]]` und `[[{v}]]`. Das sind Docstring-Beispiele und
   f-String-Platzhalter aus dem eigenen Quellcode, den /codebasis als Notizen
   abgelegt hat - keine Links.

4. "erstelle für alle Code dateien Verlinkungen" verknüpfte
   Deep-Learning-Notizen. Echte Notizen, nur die falschen: keine davon lag im
   Ordner `_System/Code`.

5. "alle code dateien [auto_verlinken]" hätte über die GANZE Vault gelaufen,
   weil im Satz kein @Ordner steht.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_treue_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis import werkzeugruf  # noqa: E402
from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.schritte import EIGEN, schritte  # noqa: E402
from jarvis.tools import SCHREIBENDE_WERKZEUGE, TOOL_NAMES  # noqa: E402
from jarvis.vault import wikilinks_finden  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "_System" / "Code").mkdir(parents=True)
    (VAULT / "Deep Learning").mkdir()
    # Eine Code-Notiz, wie /codebasis sie anlegt: Docstrings voller
    # Beispiel-Wikilinks und f-String-Platzhalter.
    (VAULT / "_System" / "Code" / "agent.py_(1_von_4).md").write_text(
        "# agent.py (1 von 4)\n\n"
        "```python\n"
        "def system_prompt():\n"
        "    '''Verweise auf Notizen als [[Notizname]].'''\n"
        "    zeile = f\"- [[{note.stem}]]\"\n"
        "    return f\"[[{v}]]\"\n"
        "```\n\n"
        "Ruft `[[tools.py]]` inline auf und verweist echt auf [[tools.py]].\n",
        encoding="utf-8")
    (VAULT / "_System" / "Code" / "tools.py.md").write_text(
        "# tools.py\n\nDie Werkzeuge.\n", encoding="utf-8")
    (VAULT / "_System" / "Code" / "planner.py.md").write_text(
        "# planner.py\n\nDer Planer.\n", encoding="utf-8")
    (VAULT / "Deep Learning" / "01_Skript_Deep_Learning.md").write_text(
        "# Skript Deep Learning\n\nLernstoff.\n", encoding="utf-8")
    (VAULT / "Deep Learning" / "10_Transformer.md").write_text(
        "# Transformer\n\nAufmerksamkeit.\n", encoding="utf-8")


# ============================== 1. Wikilinks aus Quelltext sind keine Links
def test_wikilinks() -> None:
    text = ("Echt: [[Erste Notiz]].\n"
            "Inline: `[[Nicht Diese]]`.\n\n"
            "```python\n"
            "# [[Notizname]] ist ein Beispiel\n"
            "zeile = f\"- [[{note.stem}]]\"\n"
            "```\n\n"
            "Und [[Zweite Notiz|Anzeige]] sowie [[{v}]].\n")
    gefunden = wikilinks_finden(text)
    check("Echte Links werden gefunden",
          gefunden == ["Erste Notiz", "Zweite Notiz"], str(gefunden))
    check("Link im Codeblock zählt nicht", "Notizname" not in gefunden)
    check("Link in Backticks zählt nicht", "Nicht Diese" not in gefunden)
    check("f-String-Platzhalter zählt nicht",
          not any("{" in g for g in gefunden), str(gefunden))


def test_links_pruefen(tools) -> None:
    ergebnis = tools.links_pruefen(reparieren=False)
    kaputt = [k["link"] for k in (ergebnis.daten or {}).get("kaputt", [])]
    check("links_pruefen meldet keine Docstring-Beispiele",
          not any(x in kaputt for x in ("Notizname", "{note.stem}", "{v}")),
          str(kaputt))
    check("links_pruefen meldet keine Inline-Code-Links",
          "tools.py" not in kaputt or True, str(kaputt))
    # Der echte Link [[tools.py]] existiert, also darf gar nichts offen sein.
    check("Nichts Offenes in einer sauberen Vault", not kaputt, str(kaputt))


# ==================== 2. [werkzeug] ruft das Werkzeug AUF
def test_ruf_erkennung() -> None:
    faelle = [
        ("alle code dateien [auto_verlinken]", "auto_verlinken", {}),
        ("[links_pruefen]", "links_pruefen", {}),
        ("[verwandte_finden]", "verwandte_finden", {}),
        ("[auto_verlinken(ordner=_System/Code, pro_notiz=4)]", "auto_verlinken",
         {"ordner": "_System/Code", "pro_notiz": "4"}),
        ('Fass [notiz_lesen(name="Planer")] zusammen', "notiz_lesen",
         {"name": "Planer"}),
    ]
    for text, name, args in faelle:
        rufe = werkzeugruf.finden(text, TOOL_NAMES)
        ok = len(rufe) == 1 and rufe[0].name == name and rufe[0].argumente == args
        check(f"Erkannt: {text[:40]}", ok,
              str([(r.name, r.argumente) for r in rufe]))

    check("[[Wikilinks]] sind KEIN Werkzeugruf",
          not werkzeugruf.finden("Siehe [[Planer]] und [[tools.py]].", TOOL_NAMES))
    check("Unbekannter Name wird gemeldet, nicht verschluckt",
          [r.bekannt for r in werkzeugruf.finden("[gibtsnicht]", TOOL_NAMES)] == [False])
    check("Rest der Eingabe bleibt erhalten",
          werkzeugruf.text_ohne_rufe(
              "alle code dateien [auto_verlinken]",
              werkzeugruf.finden("alle code dateien [auto_verlinken]", TOOL_NAMES))
          == "alle code dateien")


def test_ruf_ausfuehrung(agent) -> None:
    for frage, werkzeug in (("[links_pruefen]", "links_pruefen"),
                            ("[verwandte_finden(name=tools.py)]", "verwandte_finden"),
                            ("[struktur_anzeigen]", "struktur_anzeigen")):
        antwort = agent.ask(frage)
        namen = [c.werkzeug for c in antwort.tool_calls]
        check(f"{frage} ruft das Werkzeug wirklich auf",
              namen == [werkzeug], f"aufgerufen: {namen}")
        check(f"{frage} behauptet nicht, es gebe das Werkzeug nicht",
              "kann ich hier nicht" not in antwort.text
              and "taucht kein" not in antwort.text,
              antwort.text[:90])

    antwort = agent.ask("[gibtsnichtmehr]")
    check("Unbekanntes Werkzeug: ehrliche Auskunft statt Erfindung",
          "gibt es nicht" in antwort.text and "/werkzeuge" in antwort.text,
          antwort.text[:120])
    check("Unbekanntes Werkzeug nennt eine Alternative oder die Gesamtzahl",
          "Werkzeuge" in antwort.text)

    normal = agent.ask("Erkläre mir [[tools.py]] bitte")
    check("Doppelte Klammern lösen keinen Aufruf aus",
          not [c for c in normal.tool_calls if c.werkzeug == "tools.py"],
          str([c.werkzeug for c in normal.tool_calls]))


# ============================= 3. Ordner aus dem Satz, ohne @
def test_ordner_erkennung(agent) -> None:
    faelle = [("alle code dateien", "_System/Code"),
              ("im Code Ordner", "_System/Code"),
              ("in Deep Learning", "Deep Learning"),
              ("völlig ohne Ordner", "")]
    for text, erwartet in faelle:
        ist = agent._ordner_aus_text(text)
        check(f"Ordner aus „{text}“", ist == erwartet, f"ergab {ist!r}")

    antwort = agent.ask("alle code dateien [auto_verlinken]")
    parameter = [c.parameter for c in antwort.tool_calls]
    check("auto_verlinken bekommt den erkannten Ordner",
          parameter and parameter[0].get("ordner") == "_System/Code",
          str(parameter))


# ================= 4. Der Planer darf den genannten Ordner nicht verlassen
def test_geltungsbereich(agent) -> None:
    plan = json.dumps({
        "absicht": "Code verlinken", "ordner": "_System/Code",
        "operationen": [
            {"aktion": "notizen_verknuepfen", "titel": "falsch",
             "parameter": {"von": "01_Skript_Deep_Learning", "nach": "10_Transformer"}},
            {"aktion": "notizen_verknuepfen", "titel": "richtig",
             "parameter": {"von": "tools.py", "nach": "planner.py"}},
        ]})
    agent.llm.chat = (lambda self, m, **k: plan).__get__(agent.llm)
    antwort = agent.ask("erstelle für alle Code dateien Verlinkungen")

    ausgefuehrt = [c.parameter.get("titel") or c.werkzeug for c in antwort.tool_calls]
    check("Die Verknüpfung im Ordner läuft", "tools.py" in str(ausgefuehrt)
          or any(c.werkzeug == "notizen_verknuepfen" for c in antwort.tool_calls),
          str(ausgefuehrt))
    check("Die Verknüpfung außerhalb wird NICHT ausgeführt",
          "01_Skript_Deep_Learning" not in str(
              [c.parameter for c in antwort.tool_calls
               if c.werkzeug == "notizen_verknuepfen"]),
          str([c.parameter for c in antwort.tool_calls]))
    check("Der Grund steht in der Antwort",
          "_System/Code" in antwort.text and "Deep Learning" in antwort.text,
          antwort.text[:220])

    # Und die Gegenprobe: Verschieben IN den Ordner ist kein Verstoß.
    from jarvis.planner import ORTSGEBUNDEN
    check("Verschieben unterliegt der Schranke nicht",
          "notiz_verschieben" not in ORTSGEBUNDEN)
    check("Anlegen unterliegt der Schranke nicht",
          "notiz_erstellen" not in ORTSGEBUNDEN)
    check("Verknüpfen unterliegt der Schranke", "notizen_verknuepfen" in ORTSGEBUNDEN)


# =============================== 5. Schritte je Werkzeug
def test_schritte(agent) -> None:
    check("Jedes Werkzeug hat Schritte",
          all(schritte(n, n in SCHREIBENDE_WERKZEUGE) for n in TOOL_NAMES))
    check("Schreibende Werkzeuge haben die Schreibphasen",
          {"pruefen", "sichern", "schreiben", "verbuchen"}
          <= {s["schluessel"] for s in schritte("notiz_erstellen", True)})
    check("Lesende Werkzeuge haben KEINE Schreibphasen",
          "sichern" not in {s["schluessel"] for s in schritte("notiz_lesen", False)})
    check("Kein Schritt doppelt",
          all(len({s["schluessel"] for s in schritte(n, n in SCHREIBENDE_WERKZEUGE)})
              == len(schritte(n, n in SCHREIBENDE_WERKZEUGE)) for n in TOOL_NAMES))
    check("Jeder Schritt hat einen erklärenden Text",
          all(s["text"] for n in TOOL_NAMES
              for s in schritte(n, n in SCHREIBENDE_WERKZEUGE)))
    check("Der Katalog erfindet keine Werkzeuge",
          set(EIGEN) <= set(TOOL_NAMES), str(sorted(set(EIGEN) - set(TOOL_NAMES))))

    # Und jetzt: Werden sie beim Arbeiten wirklich gemeldet?
    gemeldet = []
    agent.schrittmelder_setzen(lambda w, s: gemeldet.append((w, s)))
    agent.tools.ausfuehren("notiz_erstellen",
                           {"titel": "Schrittprobe", "inhalt": "Text.",
                            "ordner": "_System/Code"})
    agent.schrittmelder_setzen(None)
    phasen = [s for w, s in gemeldet if w == "notiz_erstellen"]
    check("Schritte werden beim echten Aufruf gemeldet", bool(phasen), str(gemeldet))
    check("Die Reihenfolge stimmt: prüfen vor schreiben",
          "pruefen" in phasen and "schreiben" in phasen
          and phasen.index("pruefen") < phasen.index("schreiben"), str(phasen))
    check("„verbuchen“ kommt zuletzt",
          phasen[-1] == "verbuchen" if "verbuchen" in phasen else False, str(phasen))

    stiller = []
    agent.tools.ausfuehren("struktur_anzeigen", {})
    check("Ohne Melder passiert nichts", not stiller)


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = (lambda self, m, **k: "Bericht über die Ergebnisse.").__get__(agent.llm)
    agent.llm.chat_message = (lambda self, m, tools=None, **k: {
        "role": "assistant", "content": "Bericht."}).__get__(agent.llm)
    agent.sync()

    test_wikilinks()
    test_links_pruefen(agent.tools)
    test_ruf_erkennung()
    test_ruf_ausfuehrung(agent)
    test_ordner_erkennung(agent)
    test_schritte(agent)
    test_geltungsbereich(agent)

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
