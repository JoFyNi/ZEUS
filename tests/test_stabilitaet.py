"""Tests für die gemeldeten Stabilitätsprobleme.

1. "Kein verwertbarer Plan" bei den verschiedensten Aufgaben.
2. Der Agent behauptet Erfolg, obwohl das Werkzeug fehlgeschlagen ist:
   "Die Notiz '_Lernen/gleichstrom' wurde erfolgreich verschoben." – war sie nicht.
3. Neue Werkzeuge für Code und externe Dateien.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_stab_"))
VAULT = TMP / "Zeus"
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"
os.environ["WARMUP"] = "false"

from jarvis.agent import JarvisAgent, ToolCallRecord, _behauptet_erfolg  # noqa: E402
from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.tools import ToolResult  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def build_vault() -> None:
    (VAULT / "_Lernen").mkdir(parents=True)
    (VAULT / "Elektrotechnik").mkdir()
    (VAULT / "_Lernen" / "Gleichstrom.md").write_text(
        "---\ntags: [et]\n---\n\n# Gleichstrom\n\n## Grundlagen\nOhmsches Gesetz.\n",
        encoding="utf-8")
    (VAULT / "Elektrotechnik" / "Wechselstrom.md").write_text(
        "# Wechselstrom\n\nSinusförmig.\n", encoding="utf-8")


def antwort_llm(plan_json, prosa="## Kurzfassung\nText. " + "Inhalt. " * 60):
    def chat(self, messages, stream_cb=None, temperature=None, format_json=False, **kwargs):
        if format_json:
            return json.dumps(plan_json, ensure_ascii=False) if plan_json is not None else "kaputt"
        return prosa
    return chat


def main() -> int:
    build_vault()
    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    agent = JarvisAgent(index=index)
    agent.llm.chat = antwort_llm({}).__get__(agent.llm)
    agent.sync()
    planer = agent.planner

    # ============================== 1. Kein verwertbarer Plan – Diagnose
    faelle = [
        ("Ich würde die Notiz verschieben.", "kein JSON"),
        ('{"operationen": []}', "leere Liste"),
        ('{"operationen":[{"aktion":"zaubern","parameter":{}}]}', "erfundene Aktion"),
        ('{"operationen":[{"aktion":"notiz_verschieben","parameter":{}}]}', "Pflichtfeld fehlt"),
        ("", "leere Antwort"),
    ]
    for roh, beschreibung in faelle:
        planer.diagnose = []
        plan = planer._plan_parsen(roh) if roh else None
        check(f"Diagnose statt Schweigen: {beschreibung}",
              plan is None and (bool(planer.diagnose) or not roh),
              "; ".join(planer.diagnose)[:70])

    # ------------------------------------------------ Synonyme retten Pläne
    synonyme = [
        ({"operationen": [{"action": "move_note",
                           "parameters": {"note": "Gleichstrom",
                                          "target_folder": "Elektrotechnik"}}]},
         "notiz_verschieben", "englische Namen"),
        ({"operationen": [{"tool": "create", "titel": "Spannungsteiler"}]},
         "notiz_erstellen", "flache Parameter"),
        ({"aktion": "notiz_loeschen", "parameter": {"name": "Gleichstrom"}},
         "notiz_loeschen", "einzelne Operation ohne Liste"),
        ({"operationen": [{"aktion": "append", "parameter": {"note": "Gleichstrom",
                                                             "content": "mehr"}}]},
         "notiz_ergaenzen", "Synonym für Aktion und Parameter"),
    ]
    for roh, erwartet, beschreibung in synonyme:
        planer.diagnose = []
        plan = planer._plan_parsen(json.dumps(roh))
        check(f"Plan gerettet: {beschreibung}",
              plan is not None and plan.operationen[0].aktion == erwartet,
              (plan.operationen[0].aktion if plan else "NICHTS"))

    # ------------------------------------------- Ersatzplan ohne Modell
    saetze = [
        ("verschiebe die Notiz Gleichstrom nach Elektrotechnik", "notiz_verschieben"),
        ("benenne Gleichstrom in Gleichstromkreise um", "notiz_verschieben"),
        ("verknüpfe Gleichstrom mit Wechselstrom", "notizen_verknuepfen"),
        ("lösche die Notiz Gleichstrom", "notiz_loeschen"),
        ("erstelle einen Ordner Messtechnik", "ordner_anlegen"),
    ]
    for satz, erwartet in saetze:
        plan = planer.ersatzplan(satz)
        check(f"Ersatzplan: „{satz[:40]}…“",
              plan is not None and plan.operationen[0].aktion == erwartet,
              plan.operationen[0].aktion if plan else "kein Muster")
    check("Reine Frage erzeugt keinen Ersatzplan",
          planer.ersatzplan("was ist das ohmsche Gesetz") is None)

    # ------------------------- Ende-zu-Ende: Modell versagt, Ersatzplan greift
    agent.llm.chat = antwort_llm(None).__get__(agent.llm)   # liefert immer Murks
    antwort = agent.ask("verschiebe die Notiz Gleichstrom nach Elektrotechnik")
    check("Trotz unbrauchbarem Modell wird verschoben",
          (VAULT / "Elektrotechnik" / "Gleichstrom.md").exists(),
          antwort.text[:80])

    # ================= 2. Falsche Erfolgsmeldung wird als solche markiert
    check("Erfolgsbehauptung wird erkannt",
          _behauptet_erfolg("Die Notiz wurde erfolgreich verschoben."))
    check("Sachliche Antwort löst nichts aus",
          not _behauptet_erfolg("Hier ist die Zusammenfassung deiner Notizen."))

    luegen_text = ("Die Notiz '_Lernen/gleichstrom' wurde erfolgreich in Ordner "
                   "'_Lernen/elektrotechnik/' verschoben.")
    gescheitert = [ToolCallRecord("notiz_verschieben", {"name": "gleichstrom"},
                                  ToolResult(False, "Zielordner nicht beschreibbar"))]
    ergebnis = agent._tatsachen_anhaengen(luegen_text, gescheitert)
    check("Widerspruch wird ausdrücklich gewarnt", "stimmt nicht vollständig" in ergebnis,
          ergebnis.splitlines()[-1][:60])
    check("Der echte Fehlergrund steht drin", "Zielordner nicht beschreibbar" in ergebnis)
    check("Fehlgeschlagenes wird als solches gelistet", "**Fehlgeschlagen:**" in ergebnis)

    geglueckt = [ToolCallRecord("notiz_verschieben", {"name": "Gleichstrom"},
                                ToolResult(True, "'Gleichstrom' liegt jetzt unter …",
                                           {"pfad": "Elektrotechnik/Gleichstrom.md"}))]
    ok_text = agent._tatsachen_anhaengen("Erledigt, Sir.", geglueckt)
    check("Bei Erfolg keine Warnung", "stimmt nicht vollständig" not in ok_text)
    check("Erfolg wird trotzdem belegt", "Tatsächlich ausgeführt" in ok_text
          and "Elektrotechnik/Gleichstrom.md" in ok_text)

    check("Ohne verändernde Werkzeuge bleibt der Text unangetastet",
          agent._tatsachen_anhaengen("Nur eine Auskunft.",
                                     [ToolCallRecord("notizen_suchen", {},
                                                     ToolResult(True, "3 Treffer"))])
          == "Nur eine Auskunft.")

    # ===================================== 3. Code und externe Dateien
    tools = agent.tools
    code = "def widerstand(u, i):\n    # R = U / I\n    return u / i\n"
    ergebnis = tools.codeblock_hinzufuegen("Wechselstrom", code, sprache="python",
                                           beschreibung="Hilfsfunktion:")
    inhalt = (VAULT / "Elektrotechnik" / "Wechselstrom.md").read_text(encoding="utf-8")
    check("Codeblock eingefügt", ergebnis.ok and "```python" in inhalt, ergebnis.meldung)
    check("Code steht vollständig drin", "return u / i" in inhalt)
    check("Raute im Code wird nicht zur Überschrift",
          inhalt.count("\n# ") <= 1, "")

    doppelt = tools.codeblock_hinzufuegen("Wechselstrom", "```python\nx = 1\n```")
    inhalt = (VAULT / "Elektrotechnik" / "Wechselstrom.md").read_text(encoding="utf-8")
    check("Mitgelieferte Backticks werden nicht verdoppelt",
          doppelt.ok and "````" not in inhalt and "```python\nx = 1" in inhalt)

    gelesen = tools.code_auslesen("Wechselstrom", sprache="python")
    check("Code wieder auslesbar",
          gelesen.ok and len(gelesen.daten["bloecke"]) == 2,
          str(len(gelesen.daten.get("bloecke", []))))

    # -------------------------------------------- Datei importieren
    quelle = TMP / "regler.py"
    quelle.write_text("class PID:\n    def __init__(self, kp):\n        self.kp = kp\n",
                      encoding="utf-8")
    importiert = tools.datei_importieren(str(quelle), ordner="Elektrotechnik")
    check("Datei importiert", importiert.ok, importiert.meldung)
    if importiert.ok:
        neu = (VAULT / importiert.daten["pfad"]).read_text(encoding="utf-8")
        check("Import als Codeblock mit erkannter Sprache", "```python" in neu)
        check("Quelldatei im Frontmatter vermerkt", "quelldatei:" in neu)
        check("Inhalt vollständig", "self.kp = kp" in neu)

    binaer = TMP / "bild.png"
    binaer.write_bytes(b"\x89PNG\x00\x00binary")
    check("Binärdatei wird abgelehnt", not tools.datei_importieren(str(binaer)).ok)
    check("Fehlende Datei wird sauber gemeldet",
          not tools.datei_importieren(str(TMP / "gibtsnicht.py")).ok)

    exe = TMP / "programm.exe"
    exe.write_text("egal", encoding="utf-8")
    check("Nicht erlaubte Endung wird abgelehnt", not tools.datei_importieren(str(exe)).ok)

    # ============================ 4. Selbstinspektion: eigener Quellcode
    from jarvis.config import settings

    codeordner = TMP / "quellcode"
    (codeordner / "jarvis").mkdir(parents=True)
    (codeordner / "jarvis" / "planer.py").write_text(
        "def plan_erstellen(auftrag):\n"
        "    # TODO: Wiederholung ergaenzen\n"
        "    return None\n", encoding="utf-8")
    (codeordner / "jarvis" / "__pycache__").mkdir()
    (codeordner / "jarvis" / "__pycache__" / "planer.pyc").write_text("x", encoding="utf-8")
    (codeordner / "start.py").write_text("import jarvis\n", encoding="utf-8")
    settings.code_roots = str(codeordner)

    liste = tools.code_dateien("*.py")
    namen = [e["datei"] for e in liste.daten["dateien"]]
    check("Quelldateien werden gefunden", liste.ok and len(namen) == 2, str(namen))
    check("__pycache__ wird übersprungen", not any("pycache" in n for n in namen))

    gelesen = tools.quellcode_lesen("jarvis/planer.py")
    check("Quelldatei lesbar", gelesen.ok and "plan_erstellen" in gelesen.daten["code"],
          gelesen.meldung)
    check("Zeilennummern sind dabei", "    1|" in gelesen.daten["code"])
    teil = tools.quellcode_lesen("jarvis/planer.py", 2, 2)
    check("Zeilenbereich funktioniert",
          teil.ok and "TODO" in teil.daten["code"] and "return None" not in teil.daten["code"],
          teil.meldung)

    gesucht = tools.code_suchen("TODO")
    check("Suche findet Fundstelle mit Zeilennummer",
          gesucht.ok and gesucht.daten["treffer"][0]["zeile"] == 2,
          str(gesucht.daten.get("treffer")))

    check("Pfad außerhalb der Freigabe wird abgelehnt",
          not tools.quellcode_lesen(str(TMP / "geheim.py")).ok)
    ausserhalb = TMP / "fremd.py"
    ausserhalb.write_text("geheim = 1\n", encoding="utf-8")
    check("Auch existierende Dateien außerhalb bleiben gesperrt",
          not tools.quellcode_lesen(str(ausserhalb)).ok)

    uebernommen = tools.codebasis_importieren("*.py", ordner="_System/Code")
    check("Codebasis wird in die Vault übernommen", uebernommen.ok,
          uebernommen.meldung)
    if uebernommen.ok:
        notizen = list((VAULT / "_System" / "Code").glob("*.md"))
        check("Für jede Datei eine Notiz", len(notizen) == 2,
              str([n.name for n in notizen]))
        inhalt = notizen[0].read_text(encoding="utf-8")
        check("Code ist eingezäunt und die Quelle vermerkt",
              "```python" in inhalt and "quelldatei:" in inhalt)
        index.sync()
        check("Code wird auffindbar",
              any("Code" in h.metadata.get("folder", "")
                  for h in index.search("plan_erstellen Wiederholung")), "")

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
