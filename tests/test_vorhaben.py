"""Tests für die Aufgabenliste und den Leerlauf.

Der Anspruch aus Joshs Vorgabe, wörtlich: eine Schleife, die bei fehlender
Nutzereingabe `Aufgabenliste.md` parst, alle Zeilen mit Präfix `- [ ]` lädt,
nach dem Attribut `priorität:` sortiert (sonst nach Dokumentenreihenfolge), den
ersten Treffer wählt und den zugehörigen Kontext holt.

Dazu seine zweite Vorgabe, die schwerer wiegt als sie klingt: Wie genau eine
Aufgabe beschrieben sein muss, hängt VON DER AUFGABE AB. „Erstelle eine Notiz"
ist einsichtig und braucht nichts weiter. Eine Codeänderung mit mehreren
Schritten braucht Zieldatei und Abnahmekriterium, sonst wird geraten. Eine
Prüfung, die beides gleich behandelt, gängelt entweder oder verlangt zu wenig -
und wird in beiden Fällen ignoriert.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_vorhaben_"))
VAULT = TMP / "Zeus"
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.config import Settings  # noqa: E402
from jarvis.eingabe import Eingabeleser  # noqa: E402
from jarvis.vorhaben import (EINFACH, ERLEDIGT, KOMPLEX, MITTEL, VORLAGE,  # noqa: E402
                             Aufgabenbuch, Vorhaben, aufgaben_lesen)

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# Joshs eigene Aufgabe, wörtlich - der Maßstab für "genau genug".
LISTE = """---
titel: "Aufgabenliste"
---

# Aufgabenliste

## Offen

- [ ] Lege eine Notiz zu Backpropagation an

- [ ] Idle-Schleife in planner.py ergänzen
    priorität: hoch
    datei: jarvis/planner.py
    fertig-wenn: Bei leerer Eingabe wird die oberste offene Aufgabe geladen
    Ergänze eine while-Loop, die bei fehlender Nutzer-Eingabe die Datei
    Aufgabenliste.md parst. Konkret: Lade alle Zeilen mit Präfix "- [ ]",
    sortiere nach dem Attribut priorität (falls vorhanden) bzw. nach
    Dokumentenreihenfolge. Wähle den ersten Treffer und extrahiere den
    dazugehörigen Kontext.

- [ ] Refactor der Werkzeugschleife
    priorität: hoch

- [ ] Ordner aufräumen
    priorität: niedrig
    datei: _System

- [x] Ausweichserver einbauen
    priorität: hoch

- [!] GraphRAG evaluieren
    priorität: niedrig
"""


def main() -> int:
    cfg = Settings()
    pfad = VAULT / "_System" / "Aufgabenliste.md"
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(LISTE, encoding="utf-8")

    buch = Aufgabenbuch(cfg)
    alle = buch.laden()

    # ==================================================== 1. Parsen
    check("Alle Aufgaben werden gelesen", len(alle) == 6, str(len(alle)))
    check("Erledigte und zurückgestellte werden erkannt",
          sum(1 for v in alle if v.status == ERLEDIGT) == 1
          and len(buch.offene()) == 4,
          f"{len(buch.offene())} offen")

    idle = next(v for v in alle if "Idle-Schleife" in v.titel)
    check("Felder werden gelesen",
          idle.felder.get("datei") == "jarvis/planner.py", str(idle.felder))
    check("Der Umlaut in 'priorität' stört nicht",
          idle.felder.get("prioritaet") == "hoch", str(idle.felder))
    check("'fertig-wenn' wird auf ein Feld abgebildet",
          bool(idle.felder.get("fertig")), str(idle.felder))
    check("Der eingerückte Absatz wird zur Beschreibung",
          "Präfix" in idle.beschreibung and "Kontext" in idle.beschreibung,
          idle.beschreibung[:60])
    check("Die Beschreibung enthält keine Feldzeilen mehr",
          "priorität:" not in idle.beschreibung)

    # Die Vorlage enthält Beispiele in Codeblöcken - die sind keine Aufgaben.
    vorlage_pfad = TMP / "Vorlage.md"
    vorlage_pfad.write_text(VORLAGE, encoding="utf-8")
    aus_vorlage = aufgaben_lesen(vorlage_pfad)
    check("Beispiele in eingerückten Blöcken werden nicht als Aufgaben gelesen",
          len(aus_vorlage) <= 2, f"{len(aus_vorlage)}: "
                                 f"{[v.titel[:30] for v in aus_vorlage]}")

    # ==================================================== 2. Sortierung
    reihenfolge = [v.titel for v in buch.sortiert()]
    check("Hohe Priorität kommt zuerst",
          reihenfolge[0].startswith("Idle-Schleife")
          and reihenfolge[1].startswith("Refactor"), str(reihenfolge))
    check("Bei gleicher Priorität gilt die Dokumentenreihenfolge",
          reihenfolge.index("Idle-Schleife in planner.py ergänzen")
          < reihenfolge.index("Refactor der Werkzeugschleife"))
    check("Ohne Angabe gilt 'mittel' – vor 'niedrig'",
          reihenfolge[2].startswith("Lege eine Notiz")
          and reihenfolge[3].startswith("Ordner"), str(reihenfolge))
    check("Erledigtes taucht in der Arbeitsreihenfolge nicht auf",
          not any("Ausweichserver" in t for t in reihenfolge))

    # =========================== 3. Schärfe richtet sich nach der Komplexität
    einfach = next(v for v in alle if v.titel.startswith("Lege eine Notiz"))
    check("Eine Notiz anzulegen gilt als einfach",
          einfach.komplexitaet == EINFACH, einfach.komplexitaet)
    check("Und braucht deshalb KEINE weiteren Angaben",
          einfach.scharf, str(einfach.maengel))

    check("Eine Codeänderung mit mehreren Schritten gilt als komplex",
          idle.komplexitaet == KOMPLEX, idle.komplexitaet)
    check("Joshs eigene Aufgabe ist vollständig genug",
          idle.scharf, str(idle.maengel))

    vage = next(v for v in alle if v.titel.startswith("Refactor"))
    check("„Refactor der Werkzeugschleife“ gilt als komplex",
          vage.komplexitaet == KOMPLEX, vage.komplexitaet)
    check("Und wird als zu ungenau erkannt", not vage.scharf, str(vage.maengel))
    maengel = " ".join(vage.maengel)
    check("Die Rückmeldung nennt die fehlende Zieldatei", "datei:" in maengel)
    check("Und das fehlende Abnahmekriterium", "fertig-wenn:" in maengel)
    check("Und dass das Vorgehen fehlt", "Vorgehen" in maengel, maengel)

    # Eine mittlere Aufgabe braucht weniger
    mittel = Vorhaben(titel="Verschiebe die Notizen zu Optimierern nach Wissen",
                      felder={"datei": "Wissen"})
    check("Eine mittlere Aufgabe mit Zieldatei genügt",
          mittel.scharf, f"{mittel.komplexitaet}: {mittel.maengel}")

    ohne_alles = Vorhaben(titel="Aufräumen")
    check("Ein Zweiwort-Titel ist immer zu knapp",
          not ohne_alles.scharf, str(ohne_alles.maengel))

    # Die Grenze darf nicht gängeln: eine klare einfache Handlung bleibt einfach
    for titel in ("Erstelle eine Übersichtsnotiz zu Optimierern",
                  "Verknüpfe Adam und Momentum miteinander",
                  "Lösche die Notiz Prüfnotiz Messstand"):
        einzeln = Vorhaben(titel=titel)
        check(f"„{titel[:34]}…“ bleibt einfach",
              einzeln.komplexitaet == EINFACH and einzeln.scharf,
              f"{einzeln.komplexitaet}: {einzeln.maengel}")

    # ==================================================== 4. Stichpunkte
    text = buch.stichpunkte()
    check("Die Ausgabe ist eine Stichpunktliste",
          text.count("\n- ") >= 3, text[:80])
    check("Nach Priorität gegliedert", "**Hoch**" in text and "**Niedrig**" in text)
    check("Ungenaue Aufgaben werden markiert", "zu ungenau" in text, text[:200])
    check("Erledigtes ist standardmäßig nur gezählt",
          "1 erledigt" in text and "Ausweichserver" not in text, text[-120:])
    check("Mit 'alle' erscheint auch Erledigtes",
          "Ausweichserver" in buch.stichpunkte(alle=True))

    # ==================================================== 5. Schreiben
    ok, meldung = buch.anhaengen("Prüfe die Einbettungen auf Dubletten")
    check("Eine Aufgabe lässt sich anhängen", ok, meldung)
    buch.laden()
    check("Und steht danach in der Liste",
          any("Dubletten" in v.titel for v in buch.offene()),
          str([v.titel for v in buch.offene()]))
    check("Die bestehenden Aufgaben bleiben erhalten", len(buch.vorhaben) == 7,
          str(len(buch.vorhaben)))

    ok, meldung = buch.anhaengen("Zerlege tools.py in mehrere Module und "
                                 "sortiere die Funktionen nach Zuständigkeit")
    check("Bei einer komplexen Aufgabe wird auf Fehlendes hingewiesen",
          ok and "auszufüllen" in meldung.lower(), meldung)
    buch.laden()
    zerlegt = next(v for v in buch.vorhaben if v.titel.startswith("Zerlege"))
    check("Und Platzhalter stehen zum Ausfüllen bereit",
          zerlegt.felder.get("datei") == "???"
          and zerlegt.felder.get("fertig") == "???", str(zerlegt.felder))

    ziel = next(v for v in buch.offene() if "Dubletten" in v.titel)
    check("Eine Aufgabe lässt sich abhaken",
          buch.abhaken(ziel, "Notiz angelegt"), "")
    buch.laden()
    check("Das Abhaken steht in der Datei",
          all(v.status == ERLEDIGT for v in buch.vorhaben
              if "Dubletten" in v.titel),
          pfad.read_text(encoding="utf-8")[-200:])
    check("Der Beleg wird mitgeschrieben",
          "Notiz angelegt" in pfad.read_text(encoding="utf-8"))

    # ==================================================== 6. Auftragstext
    auftrag = idle.als_auftrag()
    check("Der Auftrag enthält den Titel", idle.titel in auftrag)
    check("Die Zieldatei steht weit vorn",
          auftrag.index("jarvis/planner.py") < len(auftrag) // 2, auftrag[:120])
    check("Das Abnahmekriterium ist dabei", "Fertig, wenn" in auftrag)
    check("Und die vollständige Beschreibung", "Dokumentenreihenfolge" in auftrag)

    # ==================================================== 7. Leerlauf
    from jarvis.index import KnowledgeIndex
    from jarvis.leerlauf import Leerlauf

    class FakePlanner:
        def __init__(self):
            self.diagnose = []
            self.gesehen = []

        def plan_erstellen(self, auftrag, kontext=""):
            self.gesehen.append(auftrag)
            return None

    class FakeAgent:
        def __init__(self):
            self.index = KnowledgeIndex(cfg, embeddings=stubs.StubEmbedding())
            self.index.sync()
            self.planner = FakePlanner()
            self.cfg = cfg

        def _build_context(self, hits, structural=False, frage=""):
            return "KONTEXT"

    agent = FakeAgent()
    schleife = Leerlauf(agent, cfg)
    vorschlag = schleife.naechste_aufgabe()
    check("Der Leerlauf wählt die oberste scharfe Aufgabe",
          vorschlag.vorhaben is not None
          and vorschlag.vorhaben.titel.startswith("Idle-Schleife"),
          vorschlag.vorhaben.titel if vorschlag.vorhaben else vorschlag.grund)

    vorbereitet = schleife.vorbereiten()
    check("Der Auftrag geht mit vollem Text an den Planer",
          agent.planner.gesehen and "Dokumentenreihenfolge" in agent.planner.gesehen[0],
          str(agent.planner.gesehen)[:80])
    check("Der Kontext aus der Vault wird geholt",
          vorbereitet.kontext == "KONTEXT", vorbereitet.kontext)
    check("Ohne Plan wird nicht ausgeführt", not vorbereitet.brauchbar)
    check("Und der Grund steht dabei", "kein Plan" in vorbereitet.grund,
          vorbereitet.grund[:80])

    _, meldung = schleife.ausfuehren()
    check("Ohne brauchbaren Vorschlag führt der Leerlauf nichts aus",
          "Kein vorbereiteter Vorschlag" in meldung, meldung)

    # Alles Offene zu ungenau: das ist ein Befund, kein stilles Nichtstun
    nur_vage = TMP / "Vage.md"
    nur_vage.write_text("# Aufgaben\n\n- [ ] Refactor der Schleife in agent.py\n"
                        "    priorität: hoch\n", encoding="utf-8")
    cfg_vage = Settings()
    cfg_vage.aufgaben_datei = str(nur_vage.relative_to(VAULT)) \
        if str(nur_vage).startswith(str(VAULT)) else ""
    if not cfg_vage.aufgaben_datei:
        shutil.copy(nur_vage, VAULT / "Vage.md")
        cfg_vage.aufgaben_datei = "Vage.md"
    schleife2 = Leerlauf(FakeAgent(), cfg_vage)
    ergebnis = schleife2.naechste_aufgabe()
    check("Ist alles Offene zu ungenau, wird das gesagt statt geraten",
          ergebnis.vorhaben is None and "bearbeitbar" in ergebnis.grund,
          ergebnis.grund[:100])
    check("Und es steht dabei, was zu ergänzen ist",
          "fertig-wenn" in ergebnis.grund, ergebnis.grund[-120:])

    # ==================================================== 8. Leerlauf-Trigger
    leser = Eingabeleser(strom=None)
    leser.aktiv = False
    check("Zeitablauf ist von einer Leerzeile unterscheidbar",
          Eingabeleser.LEERLAUF is not None
          and Eingabeleser.LEERLAUF != "" and Eingabeleser.LEERLAUF is not None)

    import io

    schnell = Eingabeleser(strom=io.StringIO(""))
    ergebnis_marke = schnell.block_lesen(zeitlimit=0.05)
    check("Ohne Eingabe meldet block_lesen Leerlauf oder EOF",
          ergebnis_marke is Eingabeleser.LEERLAUF or ergebnis_marke is None,
          repr(ergebnis_marke))

    mit_text = Eingabeleser(strom=io.StringIO("Hallo\n"))
    import time as _t
    _t.sleep(0.1)
    gelesen = mit_text.block_lesen(zeitlimit=1.0)
    check("Mit Eingabe kommt der Text, nicht die Leerlaufmarke",
          gelesen == "Hallo", repr(gelesen))

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
