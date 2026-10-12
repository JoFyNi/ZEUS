"""Zwei Planerfehler aus dem Protokoll vom 21.09.

**Fehler 1 - der Geltungsbereich verwarf jede Verknuepfung.**
Auftrag: "ja tue das und fuege es verlinkt in die aufgaben hinzu". Der Planer
leitete den Geltungsbereich "Aufgaben" ab und verwarf danach vier Mal:

    Aufgabenliste_&_Offene_Punkte: Der Auftrag gilt fuer "Aufgaben",
    aber 17_Lernpfad_und_Reihenfolge liegt in "Wissen".

Jedes "nochmal versuchen" erzeugte denselben Fehlschlag - die Wiederholung
konnte gar nicht gelingen, weil sich nichts aenderte. Dabei ist das der Sinn
einer Verknuepfung: Sie zeigt aus einem Ordner in den anderen. Geaendert wird
nur die Notiz auf der VON-Seite.

**Fehler 2 - Schritt 2 fand nicht, was Schritt 1 erst anlegt.**
Auftrag: "erstelle ein Werkzeug zum Online-Surfen".

    Plan steht: 2 Schritte (notiz_erstellen, tags_setzen)
    FEHLER: Notiz "Werkzeug_Online_Surfen" gibt es nicht
    danach erst: Notiz 'Werkzeug Online Surfen' angelegt

Der Fehler wurde gemeldet, BEVOR die Notiz entstand - `namen_aufloesen` laeuft
ueber den ganzen Plan, bevor ein einziger Schritt ausgefuehrt ist.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TMP = Path(tempfile.mkdtemp(prefix="jarvis_planfehler_"))
VAULT = TMP / "Zeus"
(VAULT / "Aufgaben").mkdir(parents=True)
(VAULT / "Wissen").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.config import settings as cfg  # noqa: E402
from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.planner import Operation, WritePlan  # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


def main() -> int:
    (VAULT / "Aufgaben" / "Aufgabenliste.md").write_text(
        "# Aufgabenliste\n\n- Punkt eins\n", encoding="utf-8")
    (VAULT / "Wissen" / "17_Lernpfad_und_Reihenfolge.md").write_text(
        "# Lernpfad\n\nText.\n", encoding="utf-8")

    agent = JarvisAgent(cfg)
    agent.index.sync()
    planer = agent.planner

    # ============================= 1. Verknuepfen ueber Ordnergrenzen hinweg
    plan = WritePlan(absicht="Probe", operationen=[
        Operation(aktion="notizen_verknuepfen", parameter={"von": "Aufgabenliste",
                             "nach": "17_Lernpfad_und_Reihenfolge"}),
    ])
    verworfen = planer.geltungsbereich_pruefen(plan, "Aufgaben")
    check("Die Verknüpfung nach „Wissen“ wird NICHT mehr verworfen",
          not verworfen and len(plan.operationen) == 1,
          str(verworfen))

    # Die VON-Seite muss weiterhin im Geltungsbereich liegen.
    plan = WritePlan(absicht="Probe", operationen=[
        Operation(aktion="notizen_verknuepfen", parameter={"von": "17_Lernpfad_und_Reihenfolge",
                             "nach": "Aufgabenliste"}),
    ])
    verworfen = planer.geltungsbereich_pruefen(plan, "Aufgaben")
    check("Eine Änderung AN einer Notiz außerhalb bleibt verworfen",
          len(verworfen) == 1, str(len(verworfen)))

    # Beidseitig heisst: beide Seiten werden geaendert, beide zaehlen.
    plan = WritePlan(absicht="Probe", operationen=[
        Operation(aktion="notizen_verknuepfen", parameter={"von": "Aufgabenliste",
                             "nach": "17_Lernpfad_und_Reihenfolge",
                             "beidseitig": True}),
    ])
    verworfen = planer.geltungsbereich_pruefen(plan, "Aufgaben")
    check("Beidseitig verlangt beide Seiten im Geltungsbereich",
          len(verworfen) == 1, str(len(verworfen)))

    # Eine ortsgebundene Änderung im Bereich bleibt erlaubt.
    plan = WritePlan(absicht="Probe", operationen=[
        Operation(aktion="tags_setzen", parameter={"name": "Aufgabenliste", "tags": "probe"}),
    ])
    verworfen = planer.geltungsbereich_pruefen(plan, "Aufgaben")
    check("Tags an einer Notiz IM Bereich gehen weiterhin durch",
          not verworfen, str(verworfen))

    # ================== 2. Schritt 2 kennt, was Schritt 1 erst anlegen wird
    plan = WritePlan(absicht="Probe", operationen=[
        Operation(aktion="notiz_erstellen", parameter={"titel": "Werkzeug Online Surfen",
                             "inhalt": "Beschreibung des Werkzeugs."}),
        Operation(aktion="tags_setzen", parameter={"name": "Werkzeug_Online_Surfen", "tags": "werkzeug"}),
    ])
    probleme = planer.namen_aufloesen(plan)
    check("Der zweite Schritt überlebt die Namensauflösung",
          len(plan.operationen) == 2, f"{len(plan.operationen)} von 2")
    check("Und es wird kein Fehler gemeldet", not probleme, str(probleme))

    # Ein wirklich erfundener Name muss weiterhin auffallen.
    plan = WritePlan(absicht="Probe", operationen=[
        Operation(aktion="tags_setzen", parameter={"name": "Gibt Es Nicht 12345", "tags": "x"}),
    ])
    probleme = planer.namen_aufloesen(plan)
    check("Ein erfundener Name fällt weiterhin auf",
          len(probleme) == 1 and not plan.operationen, str(probleme)[:70])

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
