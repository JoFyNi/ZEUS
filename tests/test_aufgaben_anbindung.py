"""Die Anbindung: schreibt der Ausfuehrungsweg wirklich in den Speicher?

ADR-001 entscheidet, dass am ARBEITSPLAN persistiert wird, nicht an ask().
Der Aufgabenspeicher war bis zum 21.09. gebaut und getestet, aber ohne
Anschluss - nach der eigenen Qualitaetsregel damit nicht fertig:

    Code + Integration + Fehlerbehandlung + Protokoll + Test + bestanden

Hier wird die INTEGRATION geprueft, mit einem Modell, das sich wie das echte
verhaelt. Getestet werden die Szenarien aus §45:

    A   einfache Aufgabe -> Plan -> Werkzeug -> verifiziert -> fertig
    E   Replanning: offene Schritte fuehren nicht zu stillem Erfolg
    F   Neustart mitten in der Arbeit, Zustand ueberlebt
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_anbindung_")).resolve()
VAULT = TMP / "Zeus"
(VAULT / "00_Inbox").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.config import settings as cfg  # noqa: E402
from jarvis.agent import JarvisAgent  # noqa: E402
from jarvis.gespraech.arbeitsplan import Arbeitsplan, Planschritt  # noqa: E402
from jarvis.kern.aufgabenspeicher import (  # noqa: E402
    FERTIG, LAEUFT, NEUPLANUNG, Aufgabenspeicher,
)

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


AUFTRAG = ('nimm die Notizen "Alpha" und "Beta" und füge sie zusammen')


def plan_bauen() -> Arbeitsplan:
    return Arbeitsplan(auftrag=AUFTRAG, schritte=[
        Planschritt(1, "Notiz Alpha lesen", "notiz_lesen"),
        Planschritt(2, "Notiz Beta lesen", "notiz_lesen"),
        Planschritt(3, "Ergebnis schreiben", "notiz_erstellen"),
    ])


def main() -> int:
    (VAULT / "00_Inbox" / "Alpha.md").write_text(
        "# Alpha\n\nDer erste Text mit genuegend Laenge fuer die Pruefung.\n",
        encoding="utf-8")
    (VAULT / "00_Inbox" / "Beta.md").write_text(
        "# Beta\n\nDer zweite Text, ebenfalls lang genug fuer die Pruefung.\n",
        encoding="utf-8")

    agent = JarvisAgent(cfg)
    agent.index.sync()

    # ================================= 1. Der Speicher haengt ueberhaupt
    speicher = agent.speicher
    check("Der Agent hat einen Aufgabenspeicher", speicher is not None,
          agent._speicher_fehler or "")
    check("Er liegt im Arbeitsordner, nicht in der Vault",
          speicher is not None and ".jarvis" in str(speicher.pfad),
          str(speicher.pfad) if speicher else "")

    # ============================ 2. Ein Arbeitsplan erzeugt eine Aufgabe
    plan = plan_bauen()
    agent._aufgabe_anlegen(plan, AUFTRAG)
    check("Der Plan bekommt eine Aufgabenkennung", bool(plan.aufgabe_id),
          plan.aufgabe_id)
    aufgabe = speicher.holen(plan.aufgabe_id)
    check("Die Aufgabe steht im Speicher", aufgabe is not None)
    check("Und sie laeuft", aufgabe.zustand == LAEUFT, aufgabe.zustand)
    check("Der Auftragstext ist erhalten", AUFTRAG[:30] in aufgabe.auftrag)

    kinder = speicher.kinder(plan.aufgabe_id)
    check("Je Planschritt ein Kindsatz", len(kinder) == 3, f"{len(kinder)} von 3")
    check("Mit dem vorgesehenen Werkzeug",
          kinder[0].daten.get("werkzeug") == "notiz_lesen",
          str(kinder[0].daten))

    # =========================== 3. Erledigter Plan -> Aufgabe wird fertig
    for _ in range(3):
        plan.vermerken("notiz_lesen", True, "ok")
    check("Der Plan gilt als fertig", plan.fertig)
    agent._aufgabe_abschliessen(plan)
    check("Die Aufgabe wird auf 'fertig' gebucht",
          speicher.holen(plan.aufgabe_id).zustand == FERTIG,
          speicher.holen(plan.aufgabe_id).zustand)
    verlauf = speicher.verlauf(plan.aufgabe_id)
    check("Der Weg ueber 'pruefung' steht im Verlauf",
          any(e["nach"] == "pruefung" for e in verlauf),
          str([e["nach"] for e in verlauf]))

    # ============ 4. §45 E: Offene Schritte werden NICHT als Erfolg gebucht
    halb = plan_bauen()
    agent._aufgabe_anlegen(halb, AUFTRAG)
    halb.vermerken("notiz_lesen", True, "ok")          # nur Schritt 1
    check("Der Plan ist noch nicht fertig", not halb.fertig)
    agent._aufgabe_abschliessen(halb)
    stand = speicher.holen(halb.aufgabe_id)
    check("Eine halbe Aufgabe landet auf Neuplanung, nicht auf fertig",
          stand.zustand == NEUPLANUNG, stand.zustand)
    check("Und der Grund nennt die offenen Schritte",
          "Beta" in stand.fehler or "schreiben" in stand.fehler.lower(),
          stand.fehler[:70])

    # ====================== 5. §45 F: Neustart mitten in der Arbeit
    laufend = plan_bauen()
    agent._aufgabe_anlegen(laufend, AUFTRAG)
    laufend.vermerken("notiz_lesen", True, "Alpha gelesen")
    kennung = laufend.aufgabe_id
    pfad = speicher.pfad
    speicher.schliessen()
    agent._speicher = None                    # „Prozess weg"

    # --- Neustart: frischer Agent, dieselbe Datei --------------------------
    neu = JarvisAgent(cfg)
    wieder = neu.speicher
    check("Nach dem Neustart ist es dieselbe Ablage", wieder.pfad == pfad)
    stand = wieder.holen(kennung)
    check("Die laufende Aufgabe hat den Neustart ueberlebt", stand is not None)
    check("Sie steht weiterhin auf 'laeuft'", stand.zustand == LAEUFT, stand.zustand)
    check("Ihre Schritte sind noch da", len(wieder.kinder(kennung)) == 3)

    aufgenommen = wieder.wiederaufnehmen()
    check("Die Wiederaufnahme greift sie auf",
          any(a.id == kennung for a in aufgenommen),
          f"{len(aufgenommen)} aufgenommen")
    check("Und setzt sie auf Neuplanung statt blind fortzusetzen",
          wieder.holen(kennung).zustand == NEUPLANUNG,
          wieder.holen(kennung).zustand)

    # ========================= 6. Ein kaputter Speicher bricht nichts ab
    kaputt = JarvisAgent(cfg)
    kaputt._speicher = False                  # simuliert einen Fehlschlag
    frei = plan_bauen()
    kaputt._aufgabe_anlegen(frei, AUFTRAG)    # darf nicht werfen
    check("Ohne Speicher laeuft der Plan trotzdem weiter",
          frei.aufgabe_id == "", f"'{frei.aufgabe_id}'")
    kaputt._aufgabe_abschliessen(frei)        # darf ebenfalls nicht werfen
    check("Und der Abschluss wirft auch nicht", True)

    wieder.schliessen()
    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
