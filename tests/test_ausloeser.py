"""Was Arbeit ausloest, ohne dass jemand tippt - Ereignis, Termin, Ablauf.

Die drei Wege aus §44, §45 und §43 des Auftrags, und alle drei enden an
derselben Stelle: beim persistenten Ziel. Genau das ist der Punkt, der hier
geprueft wird - nicht, DASS ein Ereignis ankommt (das konnte der Bus schon),
sondern dass daraus ECHTE Arbeit wird, die der Aufgabenspeicher kennt und
ein Arbeiter abarbeitet.

    FILE_CHANGED -> Bus -> Regel  -> Ziel -> Aufgabe -> Arbeiter -> Datei da
    08:00        -> Zeitplan      -> Ziel -> Aufgabe -> Arbeiter -> Datei da
    Ablauf       -> Werkzeug, Bedingung, Freigabe, Sprung -> Ende

Dazu die Schranken, ohne die jeder der drei Wege zur Falle wird: keine
Rueckkopplung, keine Lawine bei 200 Ereignissen, keine Endlosschleife im
Ablauf, kein verpasster Termin nach dem Neustart.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

TMP = Path(tempfile.mkdtemp(prefix="jarvis_ausloeser_")).resolve()
VAULT = TMP / "Zeus"
(VAULT / "00_Inbox").mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["VAULT_GIT"] = "false"
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

import stubs  # noqa: E402,F401

from jarvis.kern.ablauf import Ablauffehler, Ablaufwerk, pruefen  # noqa: E402
from jarvis.kern.aufgabenspeicher import (                        # noqa: E402
    ABGEBROCHEN, FERTIG, GESCHEITERT, LAEUFT, PAUSIERT,
    WARTET_AUF_FREIGABE, ZIEL_ERREICHT, Aufgabenspeicher)
from jarvis.kern.ausfuehrung import Aufgabenlaeufer                # noqa: E402
from jarvis.kern.bus import Bus, Ereignis                          # noqa: E402
from jarvis.kern.planung import Zerleger                           # noqa: E402
from jarvis.kern.rechte import Rechteverwaltung                    # noqa: E402
from jarvis.kern.regelwerk import Regel, Regelwerk                 # noqa: E402
from jarvis.kern.warteschlange import Arbeiterstube                # noqa: E402
from jarvis.kern.werkzeugtor import Werkzeugtor                    # noqa: E402
from jarvis.kern.zeitplan import Zeitgeber                         # noqa: E402

from test_agentenschleife import Konf, Probewerkzeuge              # noqa: E402

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
    bus = Bus()
    tor = Werkzeugtor(tools, cfg, rechte=rechte, speicher=speicher, bus=bus)
    zerleger = Zerleger(
        llm=None, agenten=False,
        werkzeugnamen={"notizen_suchen", "notiz_lesen", "notiz_erstellen",
                       "notiz_ergaenzen", "notiz_loeschen"},
        parameternamen={"notizen_suchen": ["frage"], "notiz_lesen": ["name"],
                        "notiz_erstellen": ["titel", "inhalt", "ordner"],
                        "notiz_ergaenzen": ["name", "inhalt"],
                        "notiz_loeschen": ["name"]})
    laeufer = Aufgabenlaeufer(speicher, tor, zerleger=zerleger, cfg=cfg, bus=bus)
    stube = Arbeiterstube(speicher, laeufer, arbeiter=1, bus=bus)
    return speicher, tools, bus, rechte, tor, laeufer, stube


# ============================================================ Ereignisse
def fall_ereignis() -> None:
    """§44: FILE_CHANGED -> Bus -> Regel -> Ziel -> Aufgabe -> Agentenschleife."""
    speicher, tools, bus, rechte, _, laeufer, stube = bauen("ereignis")
    werk = Regelwerk(bus, laeufer, rechte=rechte)
    werk.aufnehmen(Regel(
        name="neue_datei",
        ereignis="datei.geaendert",
        bedingung={"pfad": {"muster": "*.md"}},
        ziel="Die geaenderte Datei {pfad} einordnen",
        schritte=[{"beschreibung": "Eintrag anlegen", "werkzeug": "notiz_erstellen",
                   "parameter": {"titel": "Eingang {name}", "ordner": "00_Inbox",
                                 "inhalt": "kam aus {pfad}"}}],
        sperrzeit=0))
    werk.horchen()

    # `daten=` statt Schluesselwoertern: 'name' ist in melden() schon
    # vergeben. Genau dafuer gibt es den Weg.
    bus.melden("datei.geaendert", quelle="test",
               daten={"pfad": "Projekte/Neu.md", "name": "Neu"})
    ziele = speicher.ziele(nur_offene=True)
    check("Das Ereignis hat ein Ziel erzeugt", len(ziele) == 1,
          ziele[0].beschreibung if ziele else "keins")
    check("Die Ereignisdaten stehen im Ziel",
          bool(ziele) and "Projekte/Neu.md" in ziele[0].beschreibung,
          ziele[0].beschreibung if ziele else "-")

    stube.abarbeiten(max_runden=15, frist=20)
    check("Daraus wurde echte Arbeit",
          (VAULT / "00_Inbox" / "Eingang Neu.md").exists())
    check("Und das Ziel gilt als erreicht",
          speicher.ziel(ziele[0].id).zustand == ZIEL_ERREICHT,
          speicher.ziel(ziele[0].id).zustand)

    # Bedingung greift: eine .txt-Datei loest NICHTS aus.
    vorher = len(speicher.ziele())
    bus.melden("datei.geaendert", quelle="test",
               daten={"pfad": "Sonstiges/Notiz.txt", "name": "Notiz"})
    check("Die Bedingung filtert wirklich", len(speicher.ziele()) == vorher,
          f"{len(speicher.ziele())} statt {vorher}")
    speicher.schliessen()


def fall_keine_lawine() -> None:
    """Die Sperrzeit: 50 Ereignisse ergeben nicht 50 Ziele."""
    speicher, _, bus, rechte, _, laeufer, _ = bauen("lawine")
    werk = Regelwerk(bus, laeufer, rechte=rechte)
    werk.aufnehmen(Regel(name="viel", ereignis="datei.geaendert",
                         ziel="Etwas tun mit {pfad}", sperrzeit=60))
    werk.horchen()
    for n in range(50):
        bus.melden("datei.geaendert", quelle="test", pfad=f"x{n}.md")
    check("Aus 50 Ereignissen wurde EIN Ziel", len(speicher.ziele()) == 1,
          f"{len(speicher.ziele())} Ziele")
    check("Die Drosselung ist sichtbar", len(werk.verworfen) >= 45,
          f"{len(werk.verworfen)} verworfen")
    speicher.schliessen()


def fall_keine_rueckkopplung() -> None:
    """Eine Regel auf die Ereignisse der Arbeit selbst wird abgelehnt."""
    speicher, _, bus, rechte, _, laeufer, _ = bauen("rueckkopplung")
    werk = Regelwerk(bus, laeufer, rechte=rechte)
    abgelehnt = ""
    try:
        werk.aufnehmen(Regel(name="schlimm", ereignis="ziel.aufgenommen",
                             ziel="noch ein Ziel"))
    except ValueError as exc:
        abgelehnt = str(exc)
    check("Eine rueckkoppelnde Regel wird abgelehnt", bool(abgelehnt),
          abgelehnt[:80])
    check("Die Begruendung nennt den Grund",
          "Rueckkopplung" in abgelehnt, abgelehnt[:60])

    # Auch wenn jemand sie an der Pruefung vorbei einsetzt, feuert sie nicht.
    werk.regeln["schleichweg"] = Regel(name="schleichweg",
                                       ereignis="ziel.aufgenommen",
                                       ziel="noch ein Ziel", sperrzeit=0)
    werk.horchen()
    laeufer.ziel_aufnehmen("Ein ganz normales Ziel")
    check("Auch eingeschmuggelt zuendet sie nicht",
          len(speicher.ziele()) == 1, f"{len(speicher.ziele())} Ziele")
    speicher.schliessen()


def fall_rechte_bremsen_regeln() -> None:
    """Ohne Stufe 2 erzeugt kein Ereignis unbeaufsichtigte Arbeit."""
    speicher, _, bus, rechte, _, laeufer, _ = bauen("regelrechte", rechtestufe=1)
    werk = Regelwerk(bus, laeufer, rechte=rechte)
    werk.aufnehmen(Regel(name="x", ereignis="datei.geaendert",
                         ziel="Etwas tun", sperrzeit=0))
    werk.horchen()
    bus.melden("datei.geaendert", quelle="test", pfad="a.md")
    check("Kein Ziel ohne Rechtestufe 2", not speicher.ziele(),
          f"{len(speicher.ziele())} Ziele")
    check("Der Grund steht in der Ablage",
          any(v["grund"] == "Rechtestufe" for v in werk.verworfen))
    speicher.schliessen()


# ============================================================== Zeitplaene
def fall_zeitplan() -> None:
    """§45: Termin -> persistente Aufgabe -> Arbeiter -> Abschluss."""
    speicher, tools, bus, rechte, _, laeufer, stube = bauen("zeitplan")
    uhr = Zeitgeber(speicher, laeufer, bus=bus, rechte=rechte)
    uhr.eintragen("wochenbericht", "Lege den Wochenbericht an",
                  in_sekunden=0, alle_sekunden=0,
                  schritte=[{"beschreibung": "Bericht schreiben",
                             "werkzeug": "notiz_erstellen",
                             "parameter": {"titel": "Wochenbericht",
                                           "ordner": "00_Inbox"}}])
    check("Noch keine Arbeit vor dem Termin", not speicher.ziele())

    entstanden = uhr.runde()
    check("Der faellige Termin hat ein Ziel erzeugt", len(entstanden) == 1,
          str(entstanden))
    check("Das Ziel ist persistent", len(speicher.ziele()) == 1)

    stube.abarbeiten(max_runden=15, frist=20)
    check("Der Arbeiter hat es ausgefuehrt",
          (VAULT / "00_Inbox" / "Wochenbericht.md").exists())
    check("Der einmalige Termin ist danach still",
          not speicher.zeitplaene(nur_aktive=True),
          str([z["name"] for z in speicher.zeitplaene(nur_aktive=True)]))
    check("Er ist aber nicht verschwunden",
          len(speicher.zeitplaene(nur_aktive=False)) == 1)
    check("Und der Lauf ist gezaehlt",
          speicher.zeitplaene(nur_aktive=False)[0]["laeufe"] == 1)
    speicher.schliessen()


def fall_zeitplan_ueberlebt_neustart() -> None:
    """Der Punkt, an dem der alte Auftragsplaner scheitert."""
    ordner = TMP / "zeitplan_neustart"
    ordner.mkdir(exist_ok=True)
    datenbank = ordner / "aufgaben.sqlite3"
    speicher = Aufgabenspeicher(datenbank)
    cfg = Konf()
    tools = Probewerkzeuge(VAULT, cfg)
    tor = Werkzeugtor(tools, cfg, rechte=Rechteverwaltung(stufe=2),
                      speicher=speicher)
    laeufer = Aufgabenlaeufer(speicher, tor, zerleger=Zerleger(
        llm=None, agenten=False, werkzeugnamen={"notiz_erstellen"},
        parameternamen={"notiz_erstellen": ["titel", "ordner"]}), cfg=cfg)
    uhr = Zeitgeber(speicher, laeufer)
    uhr.eintragen("taeglich", "Tagesnotiz anlegen", alle_sekunden=86400,
                  in_sekunden=1)
    speicher.schliessen()

    # "Neustart": alles neu aufbauen, nur die Datei bleibt.
    speicher = Aufgabenspeicher(datenbank)
    check("Der Termin hat den Neustart ueberlebt",
          len(speicher.zeitplaene()) == 1,
          str([z["name"] for z in speicher.zeitplaene()]))
    eintrag = speicher.zeitplaene()[0]
    check("Mit Takt und Termin", eintrag["alle_sekunden"] == 86400
          and eintrag["faellig_ab"] > 0, str(eintrag["alle_sekunden"]))

    # Verpasster Termin: eine Woche spaeter darf es nicht sieben Ziele geben.
    laeufer2 = Aufgabenlaeufer(speicher, tor, zerleger=Zerleger(
        llm=None, agenten=False, werkzeugnamen={"notiz_erstellen"}), cfg=cfg)
    uhr2 = Zeitgeber(speicher, laeufer2)
    spaeter = time.time() + 7 * 86400
    entstanden = uhr2.runde(spaeter)
    check("Ein verpasster Termin wird EINMAL nachgeholt", len(entstanden) == 1,
          f"{len(entstanden)} Ziele")
    check("Und der naechste liegt in der Zukunft",
          speicher.zeitplaene()[0]["faellig_ab"] > spaeter,
          f"{speicher.zeitplaene()[0]['faellig_ab'] - spaeter:.0f}s spaeter")
    speicher.schliessen()


# ================================================================ Ablaeufe
def fall_ablauf() -> None:
    """§43: Ein gespeicherter Ablauf laeuft wirklich durch."""
    speicher, tools, bus, rechte, tor, laeufer, _ = bauen("ablauf")
    werk = Ablaufwerk(speicher, tor, laeufer, bus=bus)
    werk.sichern("eingang_ordnen", [
        {"art": "werkzeug", "werkzeug": "notiz_erstellen",
         "parameter": {"titel": "Ablaufnotiz {stichwort}", "ordner": "00_Inbox",
                       "inhalt": "aus dem Ablauf"},
         "merke": "angelegt"},
        {"art": "bedingung", "feld": "angelegt", "enthaelt": "angelegt",
         "sonst_nach": 4},
        {"art": "werkzeug", "werkzeug": "notizen_suchen",
         "parameter": {"frage": "{stichwort}"}},
        {"art": "ende", "befund": "durch"},
    ], beschreibung="Legt eine Notiz an und prueft den Bestand")

    check("Der Ablauf ist gespeichert",
          speicher.ablauf("eingang_ordnen") is not None)
    lauf = werk.starten("eingang_ordnen", {"stichwort": "Alpha"})
    ergebnis = werk.durchlaufen(lauf["id"])

    check("Der Ablauf ist fertig", ergebnis["zustand"] == FERTIG,
          ergebnis["zustand"] + " " + str(ergebnis.get("fehler", ""))[:60])
    check("Die Notiz wurde wirklich angelegt",
          (VAULT / "00_Inbox" / "Ablaufnotiz Alpha.md").exists())
    check("Die Werkzeuge liefen in der richtigen Reihenfolge",
          [a[0] for a in tools.aufrufe] == ["notiz_erstellen", "notizen_suchen"],
          str([a[0] for a in tools.aufrufe]))
    check("Der Verlauf steht im Lauf", len(ergebnis["protokoll"]) >= 3,
          str(ergebnis["protokoll"])[:120])
    speicher.schliessen()


def fall_ablauf_freigabe() -> None:
    """Ein Ablauf haelt an, bis ein Mensch zustimmt."""
    speicher, tools, bus, rechte, tor, laeufer, _ = bauen("ablauf_freigabe")
    werk = Ablaufwerk(speicher, tor, laeufer, bus=bus)
    werk.sichern("mit_freigabe", [
        {"art": "freigabe", "frage": "Darf die Notiz '{name}' entstehen?"},
        {"art": "werkzeug", "werkzeug": "notiz_erstellen",
         "parameter": {"titel": "{name}", "ordner": "00_Inbox"}},
    ])
    lauf = werk.starten("mit_freigabe", {"name": "Freigegeben"})
    zustand = werk.durchlaufen(lauf["id"])["zustand"]
    check("Der Ablauf wartet", zustand == WARTET_AUF_FREIGABE, zustand)
    check("Die Notiz gibt es noch nicht",
          not (VAULT / "00_Inbox" / "Freigegeben.md").exists())

    offen = speicher.offene_freigaben()
    check("Die Frage steht in der Ablage", len(offen) == 1,
          offen[0]["frage"] if offen else "keine")
    speicher.freigabe_beantworten(offen[0]["id"], True, "ja")
    werk.fortsetzen_nach_freigabe()
    ergebnis = werk.durchlaufen(lauf["id"])
    check("Danach laeuft er durch", ergebnis["zustand"] == FERTIG,
          ergebnis["zustand"])
    check("Und die Notiz ist da",
          (VAULT / "00_Inbox" / "Freigegeben.md").exists())
    speicher.schliessen()


def fall_ablauf_schranken() -> None:
    """Ein kaputter Ablauf faellt beim SPEICHERN auf, nicht im Lauf."""
    speicher, _, bus, _, tor, laeufer, _ = bauen("ablauf_schranken")
    werk = Ablaufwerk(speicher, tor, laeufer, bus=bus)

    for beschreibung, schritte in (
        ("unbekannte Schrittart", [{"art": "zaubern"}]),
        ("erfundenes Werkzeug", [{"art": "werkzeug", "werkzeug": "gibtsnicht"}]),
        ("Sprung ins Leere", [{"art": "sprung", "nach": 99}]),
        ("gar keine Schritte", []),
    ):
        gefangen = ""
        try:
            werk.sichern(f"kaputt_{len(beschreibung)}", schritte)
        except (Ablauffehler, ValueError) as exc:
            gefangen = str(exc)
        check(f"Abgelehnt: {beschreibung}", bool(gefangen), gefangen[:70])

    # Endlosschleife: Der Ablauf springt auf sich selbst zurueck.
    werk.sichern("endlos", [
        {"art": "werkzeug", "werkzeug": "notizen_suchen",
         "parameter": {"frage": "x"}},
        {"art": "sprung", "nach": 1},
    ])
    lauf = werk.starten("endlos")
    ergebnis = werk.durchlaufen(lauf["id"], max_schritte=500)
    check("Eine Endlosschleife laeuft gegen das Schrittkonto",
          ergebnis["zustand"] == GESCHEITERT, ergebnis["zustand"])
    check("Der Grund sagt, was los war",
          "Schrittkonto" in (ergebnis.get("fehler") or ""),
          (ergebnis.get("fehler") or "")[:80])
    speicher.schliessen()


def fall_ablauf_ueberlebt_neustart() -> None:
    """Der Zeiger eines Ablaufs liegt in der Datenbank, nicht im Speicher."""
    ordner = TMP / "ablauf_neustart"
    ordner.mkdir(exist_ok=True)
    datenbank = ordner / "aufgaben.sqlite3"
    cfg = Konf()
    tools = Probewerkzeuge(VAULT, cfg)

    speicher = Aufgabenspeicher(datenbank)
    tor = Werkzeugtor(tools, cfg, rechte=Rechteverwaltung(stufe=2),
                      speicher=speicher)
    werk = Ablaufwerk(speicher, tor)
    werk.sichern("dreiteilig", [
        {"art": "werkzeug", "werkzeug": "notiz_erstellen",
         "parameter": {"titel": "AblaufEins", "ordner": "00_Inbox"}},
        {"art": "werkzeug", "werkzeug": "notiz_erstellen",
         "parameter": {"titel": "AblaufZwei", "ordner": "00_Inbox"}},
        {"art": "werkzeug", "werkzeug": "notiz_erstellen",
         "parameter": {"titel": "AblaufDrei", "ordner": "00_Inbox"}},
    ])
    lauf = werk.starten("dreiteilig")
    werk.schritt(lauf["id"])
    werk.schritt(lauf["id"])
    speicher.schliessen()
    check("Zwei Schritte sind gelaufen",
          (VAULT / "00_Inbox" / "AblaufZwei.md").exists()
          and not (VAULT / "00_Inbox" / "AblaufDrei.md").exists())

    # Neustart
    speicher = Aufgabenspeicher(datenbank)
    tor = Werkzeugtor(tools, cfg, rechte=Rechteverwaltung(stufe=2),
                      speicher=speicher)
    werk = Ablaufwerk(speicher, tor)
    stand = speicher.ablauflauf(lauf["id"])
    check("Der Zeiger hat den Neustart ueberlebt", stand["zeiger"] == 2,
          f"zeiger={stand['zeiger']}")
    ergebnis = werk.durchlaufen(lauf["id"])
    check("Der Ablauf laeuft zu Ende", ergebnis["zustand"] == FERTIG,
          ergebnis["zustand"])
    check("Der dritte Schritt ist jetzt gelaufen",
          (VAULT / "00_Inbox" / "AblaufDrei.md").exists())
    check("Und die ersten beiden wurden NICHT wiederholt",
          [a[0] for a in tools.aufrufe].count("notiz_erstellen") == 3,
          str(len(tools.aufrufe)))
    speicher.schliessen()


def main() -> int:
    print("=== Was Arbeit ausloest: Ereignis, Termin, Ablauf ===\n")
    for name, fall in (
        ("Ereignis erzeugt echte Arbeit", fall_ereignis),
        ("Keine Lawine bei vielen Ereignissen", fall_keine_lawine),
        ("Keine Rueckkopplung", fall_keine_rueckkopplung),
        ("Rechte bremsen auch Regeln", fall_rechte_bremsen_regeln),
        ("Termin erzeugt persistente Aufgabe", fall_zeitplan),
        ("Termine ueberleben den Neustart", fall_zeitplan_ueberlebt_neustart),
        ("Ablauf laeuft durch", fall_ablauf),
        ("Ablauf wartet auf Freigabe", fall_ablauf_freigabe),
        ("Ablauf-Schranken greifen", fall_ablauf_schranken),
        ("Ablauf ueberlebt den Neustart", fall_ablauf_ueberlebt_neustart),
    ):
        print(f"\n--- {name} ---")
        fall()

    print("\n" + "=" * 62)
    if FAILURES:
        print(f"❌ {len(FAILURES)} Pruefung(en) rot:")
        for f in FAILURES:
            print(f"   - {f}")
        return 1
    print("✅ Ereignis, Termin und Ablauf erzeugen echte, persistente Arbeit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
