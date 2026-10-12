"""Der Arbeitsplan - der fehlende Planungsschritt zwischen Denken und Handeln.

Anlass ist das Protokoll vom 18.09., 13:50 Uhr. Auftrag: zwei Notizen zu einer
zusammenfuehren. Zeus las beide Notizen ERFOLGREICH und las danach dieselbe
Notiz noch sieben Mal, bis die Schleife abbrach. Jeder einzelne Werkzeugaufruf
war korrekt; was fehlte, war die Buchfuehrung darueber, was schon erledigt ist.

Hier wird genau dieser Ablauf nachgebaut - mit einem Modell, das sich wie im
Protokoll verhaelt und immer wieder dasselbe Werkzeug rufen will.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_plan_"))
VAULT = TMP / "Zeus"
(VAULT / "00_Inbox").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.gespraech.arbeitsplan import (  # noqa: E402
    ERLEDIGT, GESCHEITERT, OFFEN, Arbeitsplan, Planschritt,
    braucht_plan, plan_erzeugen,
)

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


AUFTRAG = ('ich möchte das du dir die beiden Notizen "Aufgabenliste" und '
           '"Aufgaben und offene Punkte" nimmst, und sie zu einer zusammenfügst.')


class FakeLLM:
    """Ein Modell, das einen brauchbaren Plan liefert."""

    def __init__(self, antwort: str):
        self.antwort = antwort
        self.aufrufe = 0

    def chat(self, messages, **kw):
        self.aufrufe += 1
        return self.antwort


def main() -> int:
    # ================================================== 1. Wann lohnt ein Plan
    check("Zwei genannte Notizen verlangen einen Plan",
          braucht_plan(AUFTRAG, False))
    check("Eine reine Wissensfrage verlangt keinen",
          not braucht_plan("was heißt Verbindungsproblem: EOF", False))
    check("Schreibabsicht plus Mehrschrittwort verlangt einen",
          braucht_plan("überarbeite die Notiz und ergänze sie danach", True))
    check("Schreibabsicht allein reicht nicht",
          not braucht_plan("lege eine Notiz zum Thema Regelkreis an", True))

    # ============================================= 2. Zerlegen und Pruefen
    gut = ('{"schritte": ['
           '{"beschreibung": "Notiz Aufgabenliste lesen", "werkzeug": "notiz_lesen"},'
           '{"beschreibung": "Notiz Aufgaben und offene Punkte lesen", "werkzeug": "notiz_lesen"},'
           '{"beschreibung": "Punkte zusammenführen", "werkzeug": "erfundenes_werkzeug"},'
           '{"beschreibung": "Ergebnis schreiben", "werkzeug": "notiz_erstellen"}]}')
    namen = {"notiz_lesen", "notiz_erstellen", "notizen_suchen"}
    plan = plan_erzeugen(AUFTRAG, FakeLLM(gut), namen)
    check("Der Plan entsteht", plan is not None)
    check("Er hat vier Schritte", plan and len(plan.schritte) == 4,
          str(len(plan.schritte) if plan else 0))
    check("Erfundene Werkzeugnamen werden verworfen",
          plan and plan.schritte[2].erwartet == "",
          plan.schritte[2].erwartet if plan else "?")
    check("Echte Werkzeugnamen bleiben stehen",
          plan and plan.schritte[0].erwartet == "notiz_lesen")

    check("Kaputtes JSON ergibt keinen Plan",
          plan_erzeugen(AUFTRAG, FakeLLM("kein json weit und breit"), namen) is None)
    check("Ein einzelner Schritt ist kein Plan",
          plan_erzeugen(AUFTRAG, FakeLLM('{"schritte":[{"beschreibung":"alles machen"}]}'),
                        namen) is None)
    check("Ein Modellfehler bringt die Schleife nicht um",
          plan_erzeugen(AUFTRAG, FakeLLMFehler(), namen) is None)

    # =================================== 3. Buchfuehrung: der eigentliche Punkt
    plan = Arbeitsplan(auftrag=AUFTRAG, schritte=[
        Planschritt(1, "Aufgabenliste lesen", "notiz_lesen"),
        Planschritt(2, "Aufgaben und offene Punkte lesen", "notiz_lesen"),
        Planschritt(3, "Ergebnis schreiben", "notiz_erstellen"),
    ])
    check("Zu Beginn ist Schritt 1 dran", plan.aktueller.nummer == 1)
    check("Der Plan ist nicht fertig", not plan.fertig)

    plan.vermerken("notiz_lesen", True, "Inhalt von 'Aufgabenliste'")
    check("Nach dem ersten Erfolg ist Schritt 2 dran", plan.aktueller.nummer == 2,
          str(plan.aktueller.nummer))
    check("Schritt 1 gilt als erledigt", plan.schritte[0].zustand == ERLEDIGT)
    check("Sein Ergebnis ist vermerkt",
          "Aufgabenliste" in plan.schritte[0].ergebnis)

    # Genau der Fall aus dem Protokoll: Das Modell will Schritt 1 wiederholen.
    anweisung = plan.als_anweisung()
    check("Die Anweisung nennt den NAECHSTEN Schritt, nicht den ersten",
          "Nummer 2" in anweisung, anweisung.splitlines()[-2][:60])
    check("Erledigtes ist als erledigt markiert", "[x] 1." in anweisung)
    check("Die Anweisung verbietet das Wiederholen ausdruecklich",
          "wiederhole sie" in anweisung.lower())

    plan.vermerken("notiz_lesen", True, "Inhalt von 'Aufgaben und offene Punkte'")
    plan.vermerken("notiz_erstellen", True, "Notiz 'Aufgaben gesamt' angelegt")
    check("Nach drei Erfolgen ist der Plan fertig", plan.fertig)
    check("Die Anweisung sagt jetzt: kein Werkzeug mehr",
          "KEIN weiteres" in plan.als_anweisung())
    check("Es gibt nichts zu melden", plan.offene_melden() == "",
          plan.offene_melden())

    # ================================================ 4. Scheitern haengt nicht
    plan = Arbeitsplan(auftrag="x", schritte=[
        Planschritt(1, "Notiz lesen, die es nicht gibt", "notiz_lesen"),
        Planschritt(2, "Ergebnis schreiben", "notiz_erstellen"),
    ])
    plan.vermerken("notiz_lesen", False, "Keine Notiz namens 'Fehlt' gefunden.")
    check("Ein Fehlschlag haelt den Schritt zunaechst fest",
          plan.aktueller.nummer == 1, str(plan.aktueller.nummer))
    plan.vermerken("notiz_lesen", False, "Keine Notiz namens 'Fehlt' gefunden.")
    check("Beim zweiten Fehlschlag wird uebersprungen",
          plan.aktueller.nummer == 2, str(plan.aktueller.nummer))
    check("Der Schritt gilt als gescheitert",
          plan.schritte[0].zustand == GESCHEITERT)
    check("Gescheitertes wird dem Nutzer gemeldet",
          "Nicht geschafft" in plan.offene_melden(), plan.offene_melden()[:60])

    # ============================================= 5. Die Anweisung bleibt klein
    gross = Arbeitsplan(auftrag="x", schritte=[
        Planschritt(i, f"Schritt {i} " + "sehr ausfuehrlich beschrieben " * 6)
        for i in range(1, 7)])
    for _ in range(3):
        gross.vermerken("notiz_lesen", True, "Ergebnis " + "y" * 400)
    check("Die Anweisung bleibt unter 2000 Zeichen",
          len(gross.als_anweisung()) < 2000, f"{len(gross.als_anweisung())} Zeichen")

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


class FakeLLMFehler:
    def chat(self, messages, **kw):
        raise RuntimeError("Server weg")


if __name__ == "__main__":
    raise SystemExit(main())
