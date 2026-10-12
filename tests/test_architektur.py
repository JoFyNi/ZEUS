"""Tests für die Architektur- und Fähigkeitsprüfung.

Die Defektprüfung (`codereview.py`) sucht Muster in einzelnen Zeilen. Sie
findet echte Fehler und ist trotzdem blind für die drei Fragen, an denen sich
entscheidet, ob ein System weiterentwickelt werden kann:

  * Trägt der AUFBAU? Ein Modul mit 2400 Zeilen hat kein Defektmuster.
  * Ist das, was da ist, GEPRÜFT?
  * Was FEHLT? Fehlendes hat keine Zeile, auf die man zeigen kann.

Hier wird geprüft, dass die Messwerte stimmen - und vor allem, dass die
Heuristiken nicht behaupten, was sie nicht wissen können. Eine Prüfung, die
zehn von vierzehn Meldungen falsch abgibt, wird nach dem zweiten Bericht
ignoriert; dann ist sie schlimmer als keine.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_arch_"))
VAULT = TMP / "Zeus"
CODE = TMP / "code"
CODE.mkdir(parents=True)
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["CODE_ROOTS"] = str(CODE)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis import architektur as A  # noqa: E402
from jarvis import nutzung  # noqa: E402
from jarvis.codereview import ist_testdatei  # noqa: E402
from jarvis.config import Settings  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


# --------------------------------------------------------------------- Proben
# Eine flache elif-Kette. Im Syntaxbaum ist jedes 'elif' ein If im orelse des
# vorherigen - naiv gezählt ergibt das Verschachtelungstiefe 6 statt 1.
FLACH = '''
def verteilen(befehl):
    if befehl == "a":
        return 1
    elif befehl == "b":
        return 2
    elif befehl == "c":
        return 3
    elif befehl == "d":
        return 4
    elif befehl == "e":
        return 5
    return 0
'''

# Echte Verschachtelung: sechs Ebenen ineinander.
TIEF = '''
def graben(daten):
    for a in daten:
        if a:
            for b in a:
                while b:
                    with open("x", encoding="utf-8") as fh:
                        if fh:
                            return b
    return None
'''

# Ein Besucher nach dem Muster von ast.NodeVisitor: Die visit_*-Methoden
# werden über getattr gerufen und stehen nirgends im Quelltext.
BESUCHER = '''
import ast


class Zaehler(ast.NodeVisitor):
    def visit_Call(self, node):
        self.generic_visit(node)

    def visit_Name(self, node):
        self.generic_visit(node)


def echt_ungenutzt(x):
    return x + 1


def benutzt(x):
    return x * 2


ERGEBNIS = benutzt(2)
'''

# Zwei Module, die sich gegenseitig importieren.
ZYKLUS_A = "from . import zyklus_b\n\n\ndef a():\n    return zyklus_b.b()\n"
ZYKLUS_B = "from . import zyklus_a\n\n\ndef b():\n    return 1\n"

# getattr-Verteilung: Der Name steht nur als Zeichenkette da.
GETATTR_MODUL = '''
def notiz_erstellen(titel):
    return titel


def ausfuehren(name):
    return globals().get(name)


AUFRUF = ausfuehren("notiz_erstellen")
'''


def schreiben(name: str, inhalt: str) -> tuple:
    pfad = CODE / name
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_text(inhalt, encoding="utf-8")
    return (pfad, name)


def main() -> int:
    cfg = Settings()

    # ============================================ 1. Verschachtelung und Pfade
    flach = A.modul_vermessen(*schreiben("flach.py", FLACH))[0]
    tief = A.modul_vermessen(*schreiben("tief.py", TIEF))[0]
    verteilen = flach.masse[0]
    graben = tief.masse[0]

    check("elif-Kette gilt nicht als Verschachtelung",
          verteilen.tiefe <= 1, f"Tiefe {verteilen.tiefe}")
    check("elif-Kette zählt aber als Entscheidungspfade",
          verteilen.komplexitaet >= 6, f"Komplexität {verteilen.komplexitaet}")
    check("echte Verschachtelung wird erkannt",
          graben.tiefe >= 5, f"Tiefe {graben.tiefe}")
    check("verschachtelte Funktion fällt auf",
          bool(graben.auffaellig), str(graben.auffaellig))
    check("flache Fallunterscheidung fällt nicht wegen Tiefe auf",
          not any("Verschachtelung" in g for g in verteilen.auffaellig),
          str(verteilen.auffaellig))

    # ================================================ 2. Vermutlich ungenutzt
    besucher = schreiben("besucher.py", BESUCHER)
    gefunden = schreiben("verteilt.py", GETATTR_MODUL)
    bericht = A.architektur_pruefen([besucher, gefunden], [], cfg)
    tote = {name for _modul, name, _z in bericht.ungenutzt}

    check("visit_*-Methoden gelten NICHT als ungenutzt",
          "visit_Call" not in tote and "visit_Name" not in tote, str(sorted(tote)))
    check("über getattr verteilter Name gilt als benutzt",
          "notiz_erstellen" not in tote, str(sorted(tote)))
    check("wirklich ungenutzte Funktion auf Modulebene wird gemeldet",
          "echt_ungenutzt" in tote, str(sorted(tote)))
    check("benutzte Funktion wird nicht gemeldet",
          "benutzt" not in tote, str(sorted(tote)))

    # ========================================================== 3. Importzyklen
    a = schreiben("zyklus_a.py", ZYKLUS_A)
    b = schreiben("zyklus_b.py", ZYKLUS_B)
    zyklisch = A.architektur_pruefen([a, b], [], cfg)
    check("Importzyklus wird gefunden", bool(zyklisch.zyklen), str(zyklisch.zyklen))
    check("Zyklus nennt beide Module",
          bool(zyklisch.zyklen) and {"zyklus_a.py", "zyklus_b.py"} <= set(zyklisch.zyklen[0]),
          str(zyklisch.zyklen))

    ohne = A.architektur_pruefen([besucher, gefunden], [], cfg)
    check("ohne Zyklus wird keiner erfunden", not ohne.zyklen, str(ohne.zyklen))

    # ======================================================== 4. Syntaxfehler
    kaputt = schreiben("kaputt.py", "def x(:\n    pass\n")
    mit_kaputt = A.architektur_pruefen([kaputt, gefunden], [], cfg)
    check("Syntaxfehler bricht die Prüfung nicht ab",
          bool(mit_kaputt.nicht_lesbar) and bool(mit_kaputt.module),
          str(mit_kaputt.nicht_lesbar))

    # ================================================== 5. Fähigkeiten: Tests
    from jarvis.tools import TOOL_NAMES

    # Frueher stand hier "ohne Testdateien gilt jedes Werkzeug als ungeprüft".
    # Das war die falsche Erwartung: Ohne eingelesene Tests weiss die Pruefung
    # gar nichts ueber die Abdeckung und darf deshalb auch nichts behaupten.
    ohne_tests = A.architektur_pruefen([gefunden], [], cfg)
    check("ohne Testdateien wird die Liste NICHT mit Falschmeldungen gefüllt",
          ohne_tests.werkzeuge_ohne_test == [],
          f"{len(ohne_tests.werkzeuge_ohne_test)} von {len(TOOL_NAMES)}")

    # Eine Testdatei, die drei Werkzeuge namentlich nennt
    testdatei = schreiben("test_probe.py",
                          'def test_x():\n'
                          '    for name in ("notiz_erstellen", "notiz_lesen"):\n'
                          '        assert name\n')
    mit_test = A.architektur_pruefen([gefunden], [testdatei], cfg)
    check("in Tests genannte Werkzeuge gelten als geprüft",
          "notiz_erstellen" not in mit_test.werkzeuge_ohne_test
          and "notiz_lesen" not in mit_test.werkzeuge_ohne_test,
          str(mit_test.werkzeuge_ohne_test[:5]))

    # ---- Der Fall aus Joshs Lauf: gar keine Testdatei im Suchpfad
    # Gemeldet wurde "34/34 Werkzeuge ohne Test". Das las sich wie ein Befund
    # über den Code, war aber einer über CODE_ROOTS - die Tests lagen dort
    # schlicht nicht. Eine Prüfung, die den Unterschied nicht macht, behauptet
    # etwas, das sie nicht wissen kann.
    ohne_jede = A.architektur_pruefen([gefunden], [], cfg)
    check("Ohne Testdateien wird KEINE Testabdeckung behauptet",
          ohne_jede.testdateien == 0 and "34/34" not in ohne_jede.kurzfassung(),
          ohne_jede.kurzfassung())
    check("Stattdessen steht dort 'nicht prüfbar'",
          "nicht prüfbar" in ohne_jede.kurzfassung(), ohne_jede.kurzfassung())
    md = A.als_markdown(ohne_jede)
    check("Der Bericht nennt CODE_ROOTS als Ursache",
          "CODE_ROOTS" in md and "Suchpfad" in md)
    check("Und macht daraus den ersten Handlungsschritt",
          any("tests/" in schritt for _w, schritt, _g
              in A.naechste_schritte(ohne_jede)),
          str(A.naechste_schritte(ohne_jede)[:1]))
    check("Es entstehen nicht dreizehn gleichlautende 'kein Test'-Lücken",
          not [l for l in ohne_jede.luecken if l["art"] == "kein Test"],
          str(len(ohne_jede.luecken)))
    term = "\n".join(A.terminalfassung(ohne_jede))
    check("Auch im Terminal steht die Einschränkung",
          "nicht prüfbar" in term, term[:200])

    # Mit Testdateien gilt die Aussage wieder
    check("Mit Testdateien wird die Abdeckung wieder ausgewiesen",
          mit_test.testdateien == 1 and "ohne Test" in mit_test.kurzfassung(),
          mit_test.kurzfassung())

    check("Testdateien werden als solche erkannt",
          ist_testdatei("tests/test_tools.py") and ist_testdatei("test_probe.py")
          and not ist_testdatei("jarvis/tools.py"))

    # =============================================== 6. Werkzeuglücken benannt
    voll = A.architektur_pruefen([gefunden], [testdatei], cfg)
    felder = {l["feld"] for l in voll.luecken}
    check("jedes Aufgabenfeld ohne Test taucht als Lücke auf",
          len(felder) > 0, f"{len(felder)} Felder")
    check("Lücken nennen entweder ein fehlendes Werkzeug oder fehlenden Test",
          all(l["fehlend"] or l["vorhanden"] for l in voll.luecken))

    # Ein Feld, dessen Werkzeuge es alle gibt UND die getestet sind, darf
    # nicht als Lücke erscheinen - sonst meldet die Prüfung Dauerfehler.
    echte_namen = sorted(TOOL_NAMES)[:2]
    test2 = schreiben("test_alles.py",
                      "NAMEN = " + json.dumps(echte_namen) + "\n")
    A.architektur_pruefen([gefunden], [test2], cfg)

    # ========================================== 7. Betriebsdaten: keine Lügen
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    leer = A.architektur_pruefen([gefunden], [testdatei], cfg)
    check("ohne Strichliste wird NICHT behauptet, Werkzeuge seien nie benutzt",
          leer.nutzung_aufrufe == 0 and not leer.werkzeuge_nie_benutzt,
          f"{len(leer.werkzeuge_nie_benutzt)} gemeldet")
    text = A.als_markdown(leer)
    check("stattdessen steht im Bericht, dass noch nichts gezählt wurde",
          "noch keine Aufrufe gezählt" in text)

    # Strichliste füllen: ein zuverlässiges, ein unzuverlässiges Werkzeug
    for _ in range(5):
        nutzung.vermerken("notiz_lesen", True, cfg)
    for i in range(6):
        nutzung.vermerken("auto_verlinken", i < 2, cfg)

    gezaehlt = A.architektur_pruefen([gefunden], [testdatei], cfg)
    check("Strichliste wird gelesen", gezaehlt.nutzung_aufrufe == 11,
          str(gezaehlt.nutzung_aufrufe))
    check("jetzt darf 'nie benutzt' behauptet werden",
          bool(gezaehlt.werkzeuge_nie_benutzt)
          and "notiz_lesen" not in gezaehlt.werkzeuge_nie_benutzt)
    unzuverlaessig = {w for w, _f, _a in gezaehlt.werkzeuge_unzuverlaessig}
    check("Werkzeug mit hoher Fehlerquote wird benannt",
          "auto_verlinken" in unzuverlaessig, str(unzuverlaessig))
    check("zuverlässiges Werkzeug wird NICHT benannt",
          "notiz_lesen" not in unzuverlaessig, str(unzuverlaessig))

    # Das Änderungsjournal als Altbestand
    (cfg.work_dir / "journal.jsonl").write_text(
        json.dumps({"zeit": "x", "aktion": "notiz_erstellen",
                    "beschreibung": "a", "dateien": []}) + "\n",
        encoding="utf-8")
    mit_journal = A.architektur_pruefen([gefunden], [testdatei], cfg)
    check("das Änderungsjournal zählt als Beleg für Benutzung",
          "notiz_erstellen" not in mit_journal.werkzeuge_nie_benutzt)

    # Offene Fehlschläge
    (cfg.work_dir / "aufgaben.jsonl").write_text(
        json.dumps({"schluessel": "k", "werkzeug": "notiz_verschieben",
                    "ziel": "z", "beschreibung": "b", "status": "offen",
                    "versuche": 4}) + "\n"
        + json.dumps({"schluessel": "k2", "werkzeug": "notiz_lesen",
                      "ziel": "z", "beschreibung": "b", "status": "erledigt",
                      "versuche": 9}) + "\n",
        encoding="utf-8")
    mit_aufgaben = A.architektur_pruefen([gefunden], [testdatei], cfg)
    gescheitert = dict(mit_aufgaben.werkzeuge_oft_gescheitert)
    check("offene Fehlschläge werden gezählt",
          gescheitert.get("notiz_verschieben") == 4, str(gescheitert))
    check("erledigte Punkte zählen NICHT als Scheitern",
          "notiz_lesen" not in gescheitert, str(gescheitert))

    # ========================================= 8. Strichliste ist robust
    kaputte_liste = nutzung.pfad(cfg)
    kaputte_liste.write_text("{kein json", encoding="utf-8")
    check("kaputte Strichliste liefert leer statt Absturz", nutzung.lesen(cfg) == {})
    check("nach einer kaputten Datei kann wieder geschrieben werden",
          nutzung.vermerken("notiz_lesen", True, cfg)
          and nutzung.lesen(cfg).get("notiz_lesen", {}).get("aufrufe") == 1)
    check("leerer Werkzeugname wird abgelehnt", not nutzung.vermerken("", True, cfg))

    # ================================================ 9. Womit anfangen
    schritte = A.naechste_schritte(mit_aufgaben)
    check("es wird eine Reihenfolge vorgeschlagen", bool(schritte), str(len(schritte)))
    check("jeder Schritt hat eine Begründung",
          all(len(grund) > 20 for _w, _s, grund in schritte))
    dringlichkeiten = {w for w, _s, _g in schritte}
    check("Dringlichkeiten sind aus dem festen Vokabular",
          dringlichkeiten <= {"jetzt", "bald", "danach"}, str(dringlichkeiten))

    # ============================================== 10. Berichte sind erzeugbar
    md = A.als_markdown(mit_aufgaben)
    term = A.terminalfassung(mit_aufgaben)
    check("Markdown-Bericht enthält die Handlungsempfehlung ganz vorn",
          "### Womit anfangen" in md
          and md.index("### Womit anfangen") < md.index("### Fähigkeiten"))
    check("Terminalfassung ist nicht leer", len(term) > 3, f"{len(term)} Zeilen")
    check("Bericht behauptet keine Zahl ohne Deckung",
          "0/0" not in md and "None" not in md)

    # ============================================ 11. Einbau in die Codeprüfung
    from jarvis.codereview import CodePruefer, Pruefbericht

    class FakeLLM:
        def chat(self, *a, **k):
            return '{"befunde": []}'

    class FakeAgent:
        def __init__(self):
            self.llm = FakeLLM()

    pruefer = CodePruefer(FakeAgent(), cfg)
    voll_bericht = pruefer.pruefen(mit_modell=False)
    check("die Codeprüfung liefert jetzt auch einen Architekturteil",
          voll_bericht.architektur is not None)
    check("die Kurzfassung nennt beides",
          "Befunde" in voll_bericht.kurzfassung()
          and "Module" in voll_bericht.kurzfassung(),
          voll_bericht.kurzfassung().replace("\n", " | "))
    check("Testdateien werden nicht auf Defektmuster geprüft",
          any("Testdateien" in e for e in voll_bericht.nicht_geprueft),
          str(voll_bericht.nicht_geprueft[:2]))
    ausgabe = "\n".join(voll_bericht.terminalfassung())
    check("Terminalausgabe enthält den Aufbauteil", "Aufbau und Fähigkeiten" in ausgabe)
    check("Markdown der Codeprüfung enthält den Aufbauteil",
          "## Aufbau und Fähigkeiten" in voll_bericht.als_markdown())

    # Abschaltbar - der schnelle Lauf soll schnell bleiben
    ohne_arch = pruefer.pruefen(mit_modell=False, mit_architektur=False)
    check("Architekturteil lässt sich abschalten", ohne_arch.architektur is None)

    # Ein Bericht ohne Architektur darf trotzdem gerendert werden
    leerer = Pruefbericht()
    check("leerer Bericht rendert ohne Architektur",
          isinstance(leerer.als_markdown(), str)
          and "Aufbau und Fähigkeiten" not in leerer.als_markdown())

    # ==================================== 12. Selbsttest am echten Quellcode
    wurzel = Path(__file__).resolve().parents[1]
    echte = [(p, p.relative_to(wurzel).as_posix())
             for p in sorted(wurzel.rglob("*.py"))
             if "__pycache__" not in str(p)]
    quellen = [x for x in echte if not ist_testdatei(x[1])]
    tests = [x for x in echte if ist_testdatei(x[1])]
    eigen = A.architektur_pruefen(quellen, tests, cfg)
    check("der eigene Code lässt sich vollständig vermessen",
          not eigen.nicht_lesbar and len(eigen.module) > 10,
          f"{len(eigen.module)} Module, nicht lesbar: {eigen.nicht_lesbar}")
    check("der eigene Code hat keinen Importzyklus", not eigen.zyklen, str(eigen.zyklen))
    check("die Rubrik 'ungenutzt' ist nicht mit Fehlalarm geflutet",
          len(eigen.ungenutzt) < 6, f"{len(eigen.ungenutzt)}: {eigen.ungenutzt[:6]}")

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
