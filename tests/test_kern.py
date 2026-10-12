"""Tests für den Betriebssystem-Kern.

Was hier geprüft wird, sind genau die Zusagen, die der Kern macht - und jede
davon ist eine, deren Bruch man im Betrieb NICHT sofort merkt:

* Ein gestörtes Modul hält den Kern nicht auf, wird aber auch nicht als
  laufend ausgegeben.
* Ein Modul startet nie vor dem, von dem es abhängt.
* Es schreibt nie mehr als ein Auftrag gleichzeitig in die Vault. Das ist die
  wichtigste Prüfung der Datei: Ohne sie liest ein zweiter Auftrag denselben
  Stand und schreibt die Änderung des ersten weg - beide melden Erfolg.
* Wiederholt wird nur, was GEWORFEN hat, nicht was 'hat nicht geklappt'
  zurückgibt.
* Ein Zuhörer, der wirft, reißt nichts mit.
* Rechte kann sich der Kern nicht selbst geben.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.kern import (Auftrag, Auftragszustand, Bus, Modul,  # noqa: E402
                         Modulzustand, Rechteverwaltung, Zeus)
from jarvis.kern.auftrag import HINTERGRUND, SOFORT  # noqa: E402
from jarvis.kern.rechte import AUTONOM, LESEN, SCHREIBEN  # noqa: E402

FAILURES = []


def check(name: str, bedingung: bool, detail: str = "") -> None:
    print(f"{'✅' if bedingung else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not bedingung:
        FAILURES.append(name)


# --------------------------------------------------------------- Testmodule
class Gedaechtnis(Modul):
    name = "gedaechtnis"
    beschreibung = "Vault und Index"
    wesentlich = True

    def __init__(self):
        super().__init__()
        self.gestartet = 0

    def _starten(self, kern):
        self.gestartet += 1


class Chat(Modul):
    name = "chat"
    beschreibung = "Gespräch"
    abhaengig_von = ("gedaechtnis",)

    def __init__(self):
        super().__init__()
        self.gehoert = []

    def _starten(self, kern):
        self.horchen("auftrag.*", lambda e: self.gehoert.append(e.name))


class Stimme(Modul):
    name = "stimme"
    beschreibung = "Sprachausgabe"

    def _starten(self, kern):
        raise RuntimeError("keine passende Stimme installiert")


class Waise(Modul):
    """Hängt an einem Modul, das gestört ist - darf nicht scheinbar laufen."""
    name = "waise"
    beschreibung = "hängt an der Stimme"
    abhaengig_von = ("stimme",)


def bus_pruefen() -> None:
    print("\n--- Ereignisbus ---")
    bus = Bus()
    gesehen = []
    ab = bus.abonnieren("auftrag.*", lambda e: gesehen.append(e.name), "test")
    bus.abonnieren("*", lambda e: gesehen.append("ALLE:" + e.name), "alles")

    bus.melden("auftrag.fertig", quelle="x", auftrag=1)
    bus.melden("modul.gestartet", quelle="y")
    check("Muster mit Stern trifft nur passende Ereignisse",
          gesehen.count("auftrag.fertig") == 1 and "modul.gestartet" not in gesehen,
          str(gesehen))
    check("Stern allein trifft alles",
          gesehen.count("ALLE:modul.gestartet") == 1, str(gesehen))

    ab()
    bus.melden("auftrag.fertig", quelle="x")
    check("Abmelden wirkt", gesehen.count("auftrag.fertig") == 1, str(gesehen))

    # Ein Zuhörer, der wirft, darf den Melder nicht mitreißen
    fehler = []
    bus.fehlermelder = fehler.append
    bus.abonnieren("kaputt", lambda e: 1 / 0, "wirft immer")
    laeuft_weiter = []
    bus.abonnieren("kaputt", lambda e: laeuft_weiter.append(1), "danach")
    for _ in range(6):
        bus.melden("kaputt")
    check("Ein werfender Zuhörer reißt nichts mit", len(laeuft_weiter) == 6,
          str(len(laeuft_weiter)))
    check("Der Fehler wird gemeldet, nicht verschluckt", bool(fehler), str(fehler[:1]))
    check("Ein dauernd werfender Zuhörer wird abgehängt",
          any(z[3] for z in bus.zuhoerer() if z[1] == "wirft immer"),
          str(bus.zuhoerer()))
    check("Der Verlauf ist da", len(bus.verlauf(50)) >= 8, str(len(bus.verlauf(50))))


def module_pruefen() -> None:
    print("\n--- Module und Startreihenfolge ---")
    kern = Zeus()
    chat, gedaechtnis = Chat(), Gedaechtnis()
    # Absichtlich in der FALSCHEN Reihenfolge anmelden.
    kern.anmelden(chat)
    kern.anmelden(gedaechtnis)
    kern.anmelden(Stimme())
    kern.anmelden(Waise())

    alles_gut = kern.starten()
    check("Der Kern startet, obwohl ein Modul gestört ist", kern.laeuft)
    check("Wesentliche Module laufen -> Rückgabe True", alles_gut,
          str(kern.startprobleme))
    check("Das Gedächtnis läuft vor dem Chat",
          kern._reihenfolge.index("gedaechtnis") < kern._reihenfolge.index("chat"),
          str(kern._reihenfolge))
    check("Das gestörte Modul ist als gestört ausgewiesen",
          kern.modul("stimme").zustand is Modulzustand.GESTOERT)
    check("Der Grund steht dabei",
          "Stimme" in kern.modul("stimme").stoerung, kern.modul("stimme").stoerung)
    check("Ein Modul ohne seine Grundlage wird NICHT gestartet",
          kern.modul("waise").zustand is Modulzustand.GESTOERT
          and "stimme" in kern.modul("waise").stoerung,
          kern.modul("waise").stoerung)

    kern.beauftragen("test", "ein Auftrag", lambda a: 1)
    kern.planer.ruhig(5)
    check("Das Chatmodul hat die Auftragsereignisse mitbekommen",
          "auftrag.fertig" in chat.gehoert, str(chat.gehoert[:4]))

    bericht = kern.bericht()
    check("Der Bericht nennt gestörte Module beim Namen",
          "stimme" in bericht and "✖" in bericht)

    kern.anhalten()
    check("Nach dem Anhalten steht der Kern", not kern.laeuft)
    check("Module sind angehalten",
          kern.modul("chat").zustand is Modulzustand.ANGEHALTEN)
    check("Abgemeldete Zuhörer hören nicht weiter",
          len(kern.modul("chat")._abmelder) == 0)

    # Ein wesentliches Modul, das nicht hochkommt -> Rückgabe False
    kern2 = Zeus()

    class WesentlichKaputt(Modul):
        name = "kern_kaputt"
        wesentlich = True

        def _starten(self, kern):
            raise OSError("Vault nicht erreichbar")

    kern2.anmelden(WesentlichKaputt())
    check("Ein gestörtes WESENTLICHES Modul liefert False", not kern2.starten())
    check("Der Kern läuft trotzdem", kern2.laeuft)
    kern2.anhalten()


def planer_pruefen() -> None:
    print("\n--- Auftragsplaner ---")
    kern = Zeus(arbeiter=4)
    kern.starten()
    planer = kern.planer

    # ---------------------------------------- 1. Die Vault-Sperre
    # DIE zentrale Zusage: nie zwei Schreiber gleichzeitig.
    gleichzeitig = {"jetzt": 0, "hoechstens": 0}
    zaehlsperre = threading.Lock()

    def schreiben(auftrag):
        with zaehlsperre:
            gleichzeitig["jetzt"] += 1
            gleichzeitig["hoechstens"] = max(gleichzeitig["hoechstens"],
                                             gleichzeitig["jetzt"])
        time.sleep(0.05)
        with zaehlsperre:
            gleichzeitig["jetzt"] -= 1
        return "geschrieben"

    for i in range(8):
        planer.auftrag("schreiben", f"Notiz {i}", schreiben, braucht_vault=True)
    planer.ruhig(20)
    check("Nie mehr als EIN schreibender Auftrag gleichzeitig",
          gleichzeitig["hoechstens"] == 1, f"gemessen: {gleichzeitig['hoechstens']}")

    # ---------------------------------------- 2. Lesende laufen parallel
    lesend = {"jetzt": 0, "hoechstens": 0}

    def lesen(auftrag):
        with zaehlsperre:
            lesend["jetzt"] += 1
            lesend["hoechstens"] = max(lesend["hoechstens"], lesend["jetzt"])
        time.sleep(0.08)
        with zaehlsperre:
            lesend["jetzt"] -= 1

    for i in range(6):
        planer.auftrag("lesen", f"Suche {i}", lesen)
    planer.ruhig(20)
    check("Lesende Aufträge laufen wirklich parallel",
          lesend["hoechstens"] > 1, f"gemessen: {lesend['hoechstens']}")

    # ---------------------------------------- 3. Priorität
    reihenfolge = []
    tor = threading.Event()

    def blocker(auftrag):
        tor.wait(5)

    # Alle Arbeiter belegen, damit sich eine echte Warteschlange bildet.
    for _ in range(4):
        planer.auftrag("block", "blockiert", blocker, prioritaet=SOFORT)
    time.sleep(0.2)
    planer.auftrag("spaet", "Hintergrund", lambda a: reihenfolge.append("hintergrund"),
                   prioritaet=HINTERGRUND)
    planer.auftrag("frueh", "Sofort", lambda a: reihenfolge.append("sofort"),
                   prioritaet=SOFORT)
    time.sleep(0.1)
    tor.set()
    planer.ruhig(20)
    check("Der dringende Auftrag läuft vor dem Hintergrundauftrag",
          reihenfolge == ["sofort", "hintergrund"], str(reihenfolge))

    # ---------------------------------------- 4. Wiederholung nur bei Ausnahme
    versuche = {"n": 0}

    def flackernd(auftrag):
        versuche["n"] += 1
        if versuche["n"] < 3:
            raise ConnectionError("Ollama antwortet nicht")
        return "endlich"

    auftrag = planer.auftrag("netz", "flackernde Verbindung", flackernd, max_versuche=4)
    auftrag.warten(30)
    check("Ein geworfener Fehler wird wiederholt", auftrag.zustand is Auftragszustand.FERTIG
          and versuche["n"] == 3, f"{auftrag.zustand}, {versuche['n']} Versuche")

    ruhige = {"n": 0}

    def sauber_gescheitert(auftrag):
        ruhige["n"] += 1
        return {"ok": False, "meldung": "Notiz gibt es nicht"}

    auftrag = planer.auftrag("sauber", "meldet Fehlschlag", sauber_gescheitert,
                             max_versuche=4)
    auftrag.warten(10)
    check("Ein sauber gemeldeter Fehlschlag wird NICHT wiederholt",
          ruhige["n"] == 1 and auftrag.zustand is Auftragszustand.FERTIG,
          f"{ruhige['n']} Läufe")

    hoffnungslos = {"n": 0}

    def immer_kaputt(auftrag):
        hoffnungslos["n"] += 1
        raise ValueError("geht nie")

    auftrag = planer.auftrag("kaputt", "scheitert immer", immer_kaputt, max_versuche=2)
    auftrag.warten(30)
    check("Nach den Versuchen ist Schluss",
          auftrag.zustand is Auftragszustand.GESCHEITERT and hoffnungslos["n"] == 2,
          f"{auftrag.zustand}, {hoffnungslos['n']} Versuche")
    check("Der Grund steht im Auftrag", "geht nie" in auftrag.fehler, auftrag.fehler)

    # ---------------------------------------- 5. Abbruch
    angefangen = threading.Event()

    def lang(auftrag):
        angefangen.set()
        for _ in range(200):
            auftrag.haltepunkt()
            time.sleep(0.01)
        return "durchgelaufen"

    auftrag = planer.auftrag("lang", "läuft lange", lang)
    angefangen.wait(5)
    planer.abbrechen(auftrag.id)
    auftrag.warten(10)
    check("Ein laufender Auftrag lässt sich abbrechen",
          auftrag.zustand is Auftragszustand.ABGEBROCHEN, str(auftrag.zustand))

    # ---------------------------------------- 6. Zeitpunkt
    gelaufen = []
    auftrag = planer.auftrag("spaeter", "in 0,3 s", lambda a: gelaufen.append(time.time()),
                             faellig_ab=time.time() + 0.3)
    check("Ein geplanter Auftrag wartet zunächst",
          auftrag.zustand is Auftragszustand.GEPLANT, str(auftrag.zustand))
    check("Er läuft nicht sofort", not gelaufen)
    auftrag.warten(10)
    check("Er läuft, wenn er fällig wird", auftrag.zustand is Auftragszustand.FERTIG
          and len(gelaufen) == 1, str(auftrag.zustand))

    # ---------------------------------------- 7. Wiederkehrend
    takte = []
    planer.auftrag("takt", "alle 0,2 s", lambda a: takte.append(1),
                   wiederholen_alle=0.2)
    time.sleep(0.75)
    check("Ein wiederkehrender Auftrag läuft mehrfach", len(takte) >= 3, str(len(takte)))

    stand = planer.zustand()
    check("Der Planer gibt über sich Auskunft",
          stand["zaehler"]["fertig"] > 10 and stand["zaehler"]["gescheitert"] >= 1,
          str(stand["zaehler"]))

    kern.anhalten(frist=10)
    check("Nach dem Anhalten läuft der Planer nicht mehr", not planer.laeuft)
    vorher = len(takte)
    time.sleep(0.5)
    check("Wiederkehrende Aufträge hören auf", len(takte) == vorher,
          f"{vorher} -> {len(takte)}")


def rechte_pruefen(tmp: Path) -> None:
    print("\n--- Rechte ---")
    ablage = tmp / "rechte.json"
    rechte = Rechteverwaltung(stufe=SCHREIBEN, ablage=ablage)

    check("Lesen ist auf Stufe 1 erlaubt", rechte.darf("lesen"))
    check("Vault-Schreiben ist auf Stufe 1 erlaubt", rechte.darf("vault_schreiben"))
    check("Unbeaufsichtigtes Arbeiten ist auf Stufe 1 NICHT erlaubt",
          not rechte.darf("unbeaufsichtigt_arbeiten"))
    check("Code ändern ist auf Stufe 1 NICHT erlaubt",
          not rechte.darf("code_uebernehmen"))
    check("Code VORSCHLAGEN ist immer erlaubt", rechte.darf("code_vorschlagen"))

    rechte.verlangen("unbeaufsichtigt_arbeiten", "nächtliche Wartung")
    check("Eine Ablehnung wird festgehalten", len(rechte.verweigert) == 1,
          str(rechte.verweigert))
    check("Der Grund nennt die nötige Stufe und den Weg dorthin",
          "RECHTE_STUFE" in rechte.grund("unbeaufsichtigt_arbeiten"))

    check("Ohne Bewährung kein Antrag", rechte.antrag() is None)
    for _ in range(19):
        rechte.vermerken(True)
    check("19 Läufe reichen noch nicht", rechte.antrag() is None)
    rechte.vermerken(True)
    antrag = rechte.antrag()
    check("Nach 20 geglückten Läufen gibt es einen Antrag", antrag is not None)
    check("Der Antrag nennt Zahlen, keine Beteuerungen",
          antrag and antrag["laeufe"] == 20 and antrag["quote"] == 1.0, str(antrag))
    check("Der Antrag sagt, wie Josh ihn erteilt",
          "RECHTE_STUFE=2" in (antrag or {}).get("erteilen_mit", ""))
    check("Der Antragstext sagt, dass Zeus sich nicht selbst befördern kann",
          "nicht selbst geben" in rechte.antrag_als_text())

    # Eine schlechte Quote kippt den Antrag wieder
    for _ in range(5):
        rechte.vermerken(False)
    check("Fehlschläge nehmen den Antrag zurück", rechte.antrag() is None,
          str(rechte.bilanz[SCHREIBEN].quote))

    # Der entscheidende Punkt: die Datei darf die Stufe NICHT anheben.
    ablage.write_text('{"stufe": 3, "bilanz": {}}', encoding="utf-8")
    neu = Rechteverwaltung(stufe=SCHREIBEN, ablage=ablage)
    check("Die Ablagedatei kann die Rechtestufe nicht anheben",
          neu.stufe == SCHREIBEN, str(neu.stufe))

    # Und über den Kern: unbeaufsichtigte Arbeit ohne Stufe 2 wird abgelehnt
    kern = Zeus(rechte_stufe=SCHREIBEN)
    kern.starten()
    verweigert = False
    try:
        kern.beauftragen("wartung", "nächtlich", lambda a: None, wiederholen_alle=60)
    except PermissionError:
        verweigert = True
    check("Der Kern lehnt unbeaufsichtigte Arbeit ohne Stufe 2 ab", verweigert)

    kern.rechte.setzen(AUTONOM, durch="test")
    auftrag = kern.beauftragen("wartung", "nächtlich", lambda a: None,
                               faellig_ab=time.time() + 60)
    check("Mit Stufe 2 geht sie durch", auftrag.zustand is Auftragszustand.GEPLANT,
          str(auftrag.zustand))
    kern.planer.abbrechen(auftrag.id)
    kern.anhalten(frist=5)


def main() -> int:
    import shutil
    import tempfile
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_kern_"))
    try:
        bus_pruefen()
        module_pruefen()
        planer_pruefen()
        rechte_pruefen(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
