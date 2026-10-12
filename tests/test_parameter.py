"""Tests für die Parameter-Annahme im Werkzeug-Dispatcher.

Anlass: Josh meldete am 27.08. Schwierigkeiten mit notiz_erstellen,
notiz_bearbeiten und listenpunkt_hinzufuegen. Die Schemata waren in Ordnung -
der Fehler lag im Dispatcher:

* Die Synonymtabelle für Feldnamen gab es nur im PLANER. Rief das Modell ein
  Werkzeug direkt auf (nativ oder per JSON-Protokoll) und schrieb englische
  Feldnamen (title/content, old/new, note/item), verwarf _clean_params sie
  stillschweigend.
* Danach lief der Aufruf in einen nackten Python-TypeError: "missing 2
  required positional arguments". Aus dieser Meldung kann ein Modell weder
  ablesen, welches Feld fehlt, noch wie es richtig heißt - es probierte
  dasselbe erneut.

Beides ist hier abgesichert - inklusive der Gegenrichtung: Ein Feldname, der
für DIESES Werkzeug gültig ist, darf NICHT übersetzt werden ('datei' bei
quellcode_lesen bleibt 'datei' und wird nicht zu 'name').
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_param_"))
VAULT = TMP / "Zeus"
CODE = TMP / "code"
VAULT.mkdir(parents=True)
CODE.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["CODE_ROOTS"] = str(CODE)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.index import KnowledgeIndex  # noqa: E402
from jarvis.tools import (TOOL_REQUIRED, VaultTools,  # noqa: E402
                          ollama_tool_definitions)

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


def main() -> int:
    (VAULT / "Aufgaben.md").write_text(
        "# Aufgaben\n\n## Offen\n\n- [ ] Erster Punkt\n", encoding="utf-8")
    (VAULT / "Notiz.md").write_text("# Notiz\n\nAlter Text hier.\n", encoding="utf-8")
    (CODE / "modul.py").write_text("print('x')\n", encoding="utf-8")

    index = KnowledgeIndex(embeddings=stubs.StubEmbedding())
    index.sync()
    tools = VaultTools(index)

    # ======================= 1. Schemata sind für Ollama wohlgeformt
    probleme = []
    for d in ollama_tool_definitions():
        fn = d["function"]
        props = fn["parameters"]["properties"]
        for r in fn["parameters"]["required"]:
            if r not in props:
                probleme.append(f"{fn['name']}: required '{r}' fehlt in properties")
        for pname, p in props.items():
            if "type" not in p:
                probleme.append(f"{fn['name']}.{pname}: kein 'type'")
            if p.get("type") == "array" and "items" not in p:
                probleme.append(f"{fn['name']}.{pname}: array ohne 'items'")
    check("Alle Werkzeug-Schemata sind wohlgeformt", not probleme, str(probleme[:3]))

    # ======================= 2. Englische Feldnamen werden übersetzt
    r = tools.ausfuehren("notiz_erstellen", {"title": "Englisch Test", "content": "Inhalt."})
    check("notiz_erstellen akzeptiert title/content", r.ok, r.meldung)

    r = tools.ausfuehren("notiz_bearbeiten",
                         {"name": "Notiz", "old": "Alter Text", "new": "Neuer Text"})
    check("notiz_bearbeiten akzeptiert old/new", r.ok, r.meldung)
    check("Die Ersetzung ist wirklich passiert",
          "Neuer Text" in (VAULT / "Notiz.md").read_text(encoding="utf-8"))

    r = tools.ausfuehren("listenpunkt_hinzufuegen", {"note": "Aufgaben", "item": "Zweiter Punkt"})
    check("listenpunkt_hinzufuegen akzeptiert note/item", r.ok, r.meldung)
    check("Der Punkt steht in der Liste, nicht am Dateiende",
          "- [ ] Erster Punkt\n- [ ] Zweiter Punkt" in (VAULT / "Aufgaben.md").read_text(encoding="utf-8"),
          (VAULT / "Aufgaben.md").read_text(encoding="utf-8"))

    # ============= 3. Gegenrichtung: gültige Namen werden NICHT übersetzt
    r = tools.ausfuehren("quellcode_lesen", {"datei": "modul.py"})
    check("quellcode_lesen(datei=…) bleibt unangetastet", r.ok, r.meldung)
    r = tools.ausfuehren("listenpunkt_hinzufuegen", {"name": "Aufgaben", "text": "Dritter Punkt"})
    check("listenpunkt_hinzufuegen(text=…) wird nicht zu 'inhalt' verbogen", r.ok, r.meldung)

    # ============= 4. Fehlendes Pflichtfeld: brauchbare Meldung statt TypeError
    r = tools.ausfuehren("notiz_erstellen", {"titel": "Nur Titel"})
    check("Fehlendes Pflichtfeld wird sauber gemeldet", not r.ok, r.meldung[:60])
    check("Die Meldung nennt das fehlende Feld", "inhalt" in r.meldung, r.meldung[:90])
    check("Die Meldung nennt alle Pflichtfelder",
          "titel" in r.meldung and "Pflichtfelder" in r.meldung)
    check("Kein durchgereichter Python-Fehler mehr",
          "positional argument" not in r.meldung, r.meldung[:90])
    check("Strukturierte Daten für das Modell",
          r.daten.get("fehlt") == ["inhalt"] and "pflicht" in r.daten, str(r.daten)[:80])

    # ============= 5. Leerer Pflichtwert zählt als fehlend
    r = tools.ausfuehren("notiz_erstellen", {"titel": "X", "inhalt": ""})
    check("Leerer Pflichtwert wird als fehlend behandelt", not r.ok, r.meldung[:70])

    # ============= 6. TOOL_REQUIRED deckt die bemängelten Werkzeuge ab
    for name in ("notiz_erstellen", "notiz_bearbeiten", "listenpunkt_hinzufuegen"):
        check(f"{name} hat Pflichtfelder hinterlegt", bool(TOOL_REQUIRED.get(name)),
              str(TOOL_REQUIRED.get(name)))

    print("\n" + ("Alle Tests bestanden." if not FAILURES else f"FEHLER: {FAILURES}"))
    shutil.rmtree(TMP, ignore_errors=True)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
