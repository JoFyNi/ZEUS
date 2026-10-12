"""Der Aufgabenvertrag - und ob er wirklich haelt.

Drei Fragen, und die dritte ist die unbequeme:

    1. Steht vor der Ausfuehrung fest, was "fertig" heisst? (§11)
    2. Engt der Vertrag den Werkzeugkasten wirklich ein - oder steht das
       nur in einem Bericht?
    3. Gilt er nach einem NEUSTART noch?

Die dritte ist der Punkt, an dem solche Rahmen in der Praxis scheitern. Ein
Vertrag, der nur im Arbeitsspeicher lebt, verschwindet beim Absturz - und
zwar genau dann, wenn der Zustand ohnehin unsicher ist und eine Schranke am
noetigsten waere.

Und die Grundregel, die dabei nie kippen darf: Ein Vertrag ENGT EIN. Er
darf nie etwas erlauben, was die Rechtestufe verbietet. Sonst waere er ein
zweiter Weg an der Rechteverwaltung vorbei - und er entsteht zum Teil aus
einer Modellantwort.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_vertrag_")).resolve()
VAULT = TMP / "Zeus"
for _o in ("00_Inbox", "01_Wissen", "99_Papierkorb"):
    (VAULT / _o).mkdir(parents=True, exist_ok=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vs")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.kern.aufgabenspeicher import (                  # noqa: E402
    ART_SCHRITT, ART_ZIEL, BEREIT, FERTIG, Aufgabenspeicher)
from jarvis.kern.ausfuehrung import Aufgabenlaeufer         # noqa: E402
from jarvis.kern.bewertung import Aufgabenbewerter          # noqa: E402
from jarvis.kern.budget import Budget, BudgetErschoepft     # noqa: E402
from jarvis.kern.planung import Zerleger                    # noqa: E402
from jarvis.kern.rechte import Rechteverwaltung             # noqa: E402
from jarvis.kern.vertrag import (                           # noqa: E402
    DATEI_DA, NICHT_PRUEFBAR, Vertrag, Vertragswerk)
from jarvis.kern.warteschlange import Arbeiterstube         # noqa: E402
from jarvis.kern.werkzeugtor import Werkzeugtor             # noqa: E402

from test_agentenschleife import Konf, Probewerkzeuge       # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


def bauen(name: str, rechtestufe: int = 2):
    ordner = TMP / name
    ordner.mkdir(exist_ok=True)
    cfg = Konf()
    speicher = Aufgabenspeicher(ordner / "aufgaben.sqlite3")
    tools = Probewerkzeuge(VAULT, cfg)
    rechte = Rechteverwaltung(stufe=rechtestufe, ablage=ordner / "rechte.json")
    tor = Werkzeugtor(tools, cfg, rechte=rechte, speicher=speicher)
    zerleger = Zerleger(
        llm=None, agenten=False,
        werkzeugnamen={"notizen_suchen", "notiz_lesen", "notiz_erstellen",
                       "notiz_ergaenzen", "notiz_loeschen"},
        parameternamen={"notizen_suchen": ["frage"], "notiz_lesen": ["name"],
                        "notiz_erstellen": ["titel", "inhalt", "ordner"],
                        "notiz_ergaenzen": ["name", "inhalt"],
                        "notiz_loeschen": ["name"]})
    laeufer = Aufgabenlaeufer(speicher, tor, zerleger=zerleger, cfg=cfg,
                              bewerter=Aufgabenbewerter(llm=None))
    stube = Arbeiterstube(speicher, laeufer, arbeiter=1)
    return speicher, tools, rechte, tor, laeufer, stube, ordner


# ══════════════════════════════════════════════════════ die Testfaelle
def fall_erfolg_steht_vorher_fest() -> None:
    """§11: Was 'fertig' heisst, steht VOR dem ersten Schritt."""
    bewerter = Aufgabenbewerter(llm=None)
    werk = Vertragswerk()
    vertrag = werk.schliessen(
        bewerter.bewerten("Erstelle eine Notiz zum Thema Sandkisten"),
        ergebnis="Die Notiz liegt in 01_Wissen")
    print("      " + vertrag.bericht().replace("\n", "\n      ")[:520])
    check("Es gibt Erfolgskriterien", len(vertrag.erfolg) >= 2,
          f"{len(vertrag.erfolg)}")
    check("Mindestens eines ist mechanisch pruefbar",
          any(k.mechanisch for k in vertrag.erfolg))
    check("Noch ist keines erfuellt - es wurde ja nichts getan",
          all(k.erfuellt is None for k in vertrag.erfolg))
    check("Der Vertrag gilt noch nicht als erfuellt", not vertrag.erfuellt)

    check("Was sich NICHT mechanisch pruefen laesst, sagt das auch",
          any(not k.mechanisch for k in vertrag.erfolg),
          str([k.text[:30] for k in vertrag.unbelegt]))

    vertrag.vermerken(DATEI_DA, True, "01_Wissen/Sandkisten.md")
    check("Nach dem Beleg gilt er als erfuellt", vertrag.erfuellt)
    check("Die unbelegten Punkte bleiben trotzdem sichtbar",
          len(vertrag.unbelegt) >= 1,
          str([k.text[:30] for k in vertrag.unbelegt]))


def fall_engt_ein() -> None:
    """Der Fehler vom 21.09. in Vertragsform: Schreiben darf nicht loeschen."""
    bewerter = Aufgabenbewerter(llm=None)
    werk = Vertragswerk()

    schreiben = werk.schliessen(bewerter.bewerten(
        "Erstelle eine Notiz zum Thema Sandkisten"))
    ok, grund = schreiben.werkzeug_erlaubt("notiz_loeschen")
    check("Ein Schreibauftrag darf NICHT loeschen", not ok, grund[:70])
    check("Er darf aber schreiben", schreiben.werkzeug_erlaubt("notiz_erstellen")[0])

    loeschen = werk.schliessen(bewerter.bewerten("Lösche die Notiz Alt"))
    check("Ein Loeschauftrag darf loeschen",
          loeschen.werkzeug_erlaubt("notiz_loeschen")[0])

    zaehlen = werk.schliessen(bewerter.bewerten("Wie viele Notizen habe ich?"))
    check("Eine Zaehlfrage darf weder schreiben noch loeschen",
          not zaehlen.werkzeug_erlaubt("notiz_erstellen")[0]
          and not zaehlen.werkzeug_erlaubt("notiz_loeschen")[0])
    check("Und sie bekommt kein Budget fuer das grosse Modell",
          zaehlen.budget.grosse_modellaufrufe == 0,
          f"{zaehlen.budget.grosse_modellaufrufe}")

    for vertrag in (schreiben, loeschen, zaehlen):
        check(f"'rueckgaengig' ist nie im Rahmen ({vertrag.ziel[:22]}…)",
              not vertrag.werkzeug_erlaubt("rueckgaengig")[0])


def fall_tor_setzt_durch() -> None:
    """Steht das nur im Bericht - oder haelt das Werkzeugtor es wirklich?"""
    speicher, tools, _, tor, _, _, _ = bauen("durchsetzen")
    bewerter = Aufgabenbewerter(llm=None)
    vertrag = Vertragswerk().schliessen(
        bewerter.bewerten("Erstelle eine Notiz zum Thema Sandkisten"))
    (VAULT / "00_Inbox" / "Opfer.md").write_text("# Opfer\n\nDa.\n",
                                                 encoding="utf-8")
    aufgabe = speicher.anlegen("Schreiben")

    verboten = tor.ausfuehren("notiz_loeschen", {"name": "Opfer"},
                              aufgabe_id=aufgabe.id, vertrag=vertrag)
    check("Das Tor weist das vertragsfremde Werkzeug ab", not verboten.ok,
          verboten.meldung[:70])
    check("Die Notiz liegt unangetastet da",
          (VAULT / "00_Inbox" / "Opfer.md").exists())
    check("Das Werkzeug wurde gar nicht erst gerufen",
          not any(a[0] == "notiz_loeschen" for a in tools.aufrufe),
          str([a[0] for a in tools.aufrufe]))
    check("Der Grund nennt den Vertrag",
          verboten.daten.get("vertrag") == "ausserhalb",
          str(verboten.daten))

    erlaubt = tor.ausfuehren("notiz_erstellen",
                             {"titel": "Sandkisten", "ordner": "00_Inbox",
                              "inhalt": "Ein hinreichend langer Satz dazu."},
                             aufgabe_id=aufgabe.id, vertrag=vertrag)
    check("Das vertragsgemaesse Werkzeug laeuft", erlaubt.ok,
          erlaubt.meldung[:60])

    # Und ohne Vertrag bleibt alles wie vorher - kein stiller Umbau.
    ohne = tor.ausfuehren("notiz_loeschen", {"name": "Opfer"},
                          aufgabe_id=aufgabe.id)
    check("Ohne Vertrag verhaelt sich das Tor wie bisher", ohne.ok,
          ohne.meldung[:60])


def fall_vertrag_engt_nur_ein() -> None:
    """Die Grundregel: nie mehr erlauben, immer nur weniger."""
    speicher, tools, rechte, tor, _, _, _ = bauen("nur_enger", rechtestufe=0)
    bewerter = Aufgabenbewerter(llm=None)
    # Ein Vertrag, der notiz_erstellen ausdruecklich erlaubt …
    vertrag = Vertragswerk().schliessen(
        bewerter.bewerten("Erstelle eine Notiz zum Thema X"))
    check("Der Vertrag erlaubt das Schreiben",
          vertrag.werkzeug_erlaubt("notiz_erstellen")[0])
    aufgabe = speicher.anlegen("Auf Lesestufe")
    # … aendert auf Rechtestufe 0 trotzdem nichts.
    ergebnis = tor.ausfuehren("notiz_erstellen",
                              {"titel": "Verbotene", "ordner": "00_Inbox",
                               "inhalt": "Sollte nicht entstehen."},
                              aufgabe_id=aufgabe.id, vertrag=vertrag)
    check("Die Rechtestufe schlaegt den Vertrag", not ergebnis.ok,
          ergebnis.meldung[:70])
    check("Die Begruendung nennt die Rechtestufe",
          "Rechtestufe" in ergebnis.meldung, ergebnis.meldung[:80])
    check("Nichts wurde geschrieben",
          not (VAULT / "00_Inbox" / "Verbotene.md").exists())


def fall_ueberlebt_neustart() -> None:
    """Der Punkt, an dem solche Rahmen in der Praxis scheitern."""
    speicher, tools, _, _, laeufer, stube, ordner = bauen("neustart")
    ziel, wurzel = laeufer.ziel_aufnehmen(
        "Erstelle eine Notiz zum Thema Dauerhaftigkeit",
        daten={"schritte": [
            {"beschreibung": "Notiz anlegen", "werkzeug": "notiz_erstellen",
             "parameter": {"titel": "Dauerhaftigkeit", "ordner": "00_Inbox",
                           "inhalt": "Ein Vertrag muss einen Neustart ueberleben."}}]})
    gespeichert = (speicher.ziel(ziel.id).daten or {}).get("vertrag")
    check("Der Vertrag wurde mitgespeichert", bool(gespeichert),
          str(sorted((gespeichert or {}).keys()))[:90])
    check("Mit seinen Verboten",
          "notiz_loeschen" in (gespeichert or {}).get("verboten", []),
          str((gespeichert or {}).get("verboten"))[:80])
    speicher.schliessen()

    # --- Neustart: alles neu, nur die Datei bleibt ---------------------
    speicher = Aufgabenspeicher(ordner / "aufgaben.sqlite3")
    cfg = Konf()
    tools2 = Probewerkzeuge(VAULT, cfg)
    tor2 = Werkzeugtor(tools2, cfg, rechte=Rechteverwaltung(stufe=2),
                       speicher=speicher)
    laeufer2 = Aufgabenlaeufer(speicher, tor2, cfg=cfg,
                               bewerter=Aufgabenbewerter(llm=None),
                               zerleger=Zerleger(llm=None, agenten=False,
                                                 werkzeugnamen={"notiz_erstellen"}))
    wieder = laeufer2._vertrag(speicher.kinder(wurzel.id)[0]
                               if speicher.kinder(wurzel.id)
                               else speicher.holen(wurzel.id))
    check("Nach dem Neustart ist der Vertrag wieder da", wieder is not None)
    if wieder is not None:
        check("Und er verbietet weiterhin das Loeschen",
              not wieder.werkzeug_erlaubt("notiz_loeschen")[0])
        check("Die Erfolgskriterien sind auch wieder da",
              len(wieder.erfolg) >= 1, f"{len(wieder.erfolg)}")
    speicher.schliessen()


def fall_budget_nach_groesse() -> None:
    """Eine Zaehlfrage bekommt nicht dasselbe Kontingent wie eine Analyse."""
    bewerter = Aufgabenbewerter(llm=None)
    werk = Vertragswerk()
    klein = werk.schliessen(bewerter.bewerten("Wie viele Notizen habe ich?"))
    gross = werk.schliessen(bewerter.bewerten(
        "Vergleiche zehn Notizen zur KI, erkenne Widersprüche und "
        "aktualisiere danach meine Übersicht"))
    print(f"      Zaehlfrage: {klein.budget.bericht()}")
    print(f"      Analyse:    {gross.budget.bericht()}")
    check("Die schwere Aufgabe bekommt mehr Laufzeit",
          gross.budget.laufzeit > klein.budget.laufzeit,
          f"{klein.budget.laufzeit:.0f}s vs {gross.budget.laufzeit:.0f}s")
    check("Und mehr Werkzeugaufrufe",
          gross.budget.werkzeugaufrufe > klein.budget.werkzeugaufrufe)
    check("Die Zaehlfrage bekommt KEIN grosses Modell",
          klein.budget.grosse_modellaufrufe == 0)

    # Und das haelt auch: Null heisst null.
    b = klein.budget
    gestoppt = False
    try:
        b.pruefen("grosse_modellaufrufe")
    except BudgetErschoepft:
        gestoppt = True
    check("Ein grosser Modellaufruf wird dort wirklich gestoppt", gestoppt)


def fall_getrennte_budgets() -> None:
    """§25: kleine und grosse Modellaufrufe getrennt - und die Summe gilt."""
    b = Budget(kleine_modellaufrufe=3, grosse_modellaufrufe=2, modellaufrufe=4)
    for _ in range(3):
        b.pruefen("kleine_modellaufrufe")
        b.buchen("kleine_modellaufrufe")
    check("Drei kleine Aufrufe sind gebucht",
          b.stand("kleine_modellaufrufe") == 3)
    check("Und zaehlen auf die Summe", b.stand("modellaufrufe") == 3,
          f"{b.stand('modellaufrufe')}")
    b.pruefen("grosse_modellaufrufe")
    b.buchen("grosse_modellaufrufe")
    gestoppt = False
    try:
        b.pruefen("grosse_modellaufrufe")
    except BudgetErschoepft as exc:
        gestoppt = "modellaufrufe" in str(exc)
    check("Die Summe bremst, auch wenn der eigene Topf noch Luft haette",
          gestoppt, b.bericht())


def main() -> int:
    print("=== Der Aufgabenvertrag ===\n")
    for name, fall in (
        ("Erfolg steht vorher fest", fall_erfolg_steht_vorher_fest),
        ("Der Vertrag engt ein", fall_engt_ein),
        ("Das Tor setzt ihn durch", fall_tor_setzt_durch),
        ("Er engt NUR ein", fall_vertrag_engt_nur_ein),
        ("Er ueberlebt den Neustart", fall_ueberlebt_neustart),
        ("Budget nach Aufgabengroesse", fall_budget_nach_groesse),
        ("Getrennte Modellbudgets", fall_getrennte_budgets),
    ):
        print(f"\n--- {name} ---")
        fall()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"❌ {len(FAILURES)} Pruefung(en) rot:")
        for f in FAILURES:
            print(f"   - {f}")
        return 1
    print("✅ Der Vertrag steht vorher fest, engt ein und ueberlebt den Neustart.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
