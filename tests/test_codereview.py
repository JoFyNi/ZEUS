"""Tests für die Selbstprüfung des Quellcodes.

Anlass: Der Versuch, den Agenten per Prompt durch eine mehrstufige Prüfung zu
führen, endete nach dem ERSTEN Werkzeug. Zeus rief code_dateien() auf, zählte
15 Dateien auf und hielt das für die Antwort - quellcode_lesen() und
code_suchen() kamen nie. Die Schrittfolge liegt deshalb jetzt in Python.

Geprüft wird hier:
  * die statischen Muster (kein Modell nötig, müssen exakt treffen),
  * die Einstufung: verschluckter SCHREIBfehler wiegt schwerer als ein
    verschluckter optionaler Schritt,
  * und der Kern der Kontrolle: ein Modellbefund mit erfundenem Zitat wird
    verworfen, statt als Tatsache im Bericht zu landen.
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

TMP = Path(tempfile.mkdtemp(prefix="jarvis_review_"))
VAULT = TMP / "Zeus"
CODE = TMP / "code"
CODE.mkdir(parents=True)
VAULT.mkdir(parents=True)
os.environ["OBSIDIAN_VAULT"] = str(VAULT)
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["CODE_ROOTS"] = str(CODE)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis.codereview import CodePruefer, Pruefbericht, statisch_pruefen  # noqa: E402
from jarvis.config import Settings  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


PROBE = '''"""Testmodul mit bekannten Schwachstellen."""
import subprocess
from pathlib import Path


def speichern(ziel, inhalt, puffer=[]):
    puffer.append(inhalt)
    try:
        Path(ziel).write_text(inhalt)
    except Exception:
        pass
    return True


def optional_laden(pfad):
    try:
        import irgendwas_optionales
    except Exception:
        pass
    return None


def lesen(pfad):
    with open(pfad) as datei:
        return datei.read()


def binaer_lesen(pfad):
    with open(pfad, "rb") as datei:
        return datei.read()


def aufraeumen(pfad):
    try:
        Path(pfad).unlink()
    except OSError:
        # Absicht: Zwischendatei, folgenlos wenn sie liegen bleibt.
        pass


def fremdes_programm():
    # Genau der Fehler, an dem /tempo starb: wmic gibt es in Windows 11 nicht
    # mehr, FileNotFoundError ist ein OSError und KEIN SubprocessError.
    try:
        return subprocess.run(["wmic", "x"], capture_output=True, timeout=10).stdout
    except subprocess.SubprocessError:
        return ""


def richtig_abgesichert():
    try:
        return subprocess.run(["wmic", "x"], capture_output=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def starten(befehl):
    return subprocess.run(befehl, capture_output=True)


def pruefen(wert):
    if wert == None:
        return False
    assert wert > 0
    return True


def melden(pfad):
    try:
        Path(pfad).unlink()
    except OSError as exc:
        return False
    return True
'''


def probe_schreiben() -> Path:
    pfad = CODE / "probe.py"
    pfad.write_text(PROBE, encoding="utf-8")
    return pfad


class FakeLLM:
    """Antwortet mit vorgegebenem JSON - einmal ehrlich, einmal erfunden."""

    def __init__(self, antworten):
        self.antworten = list(antworten)
        self.aufrufe = 0

    def chat(self, messages, **kwargs):
        self.aufrufe += 1
        if not self.antworten:
            return '{"befunde": []}'
        return self.antworten.pop(0)


class FakeAgent:
    def __init__(self, llm=None):
        self.llm = llm


def main() -> int:
    pfad = probe_schreiben()
    cfg = Settings()
    cfg.code_roots = str(CODE)

    # ------------------------------------------------------------ statisch
    befunde, grund = statisch_pruefen(pfad, "probe.py")
    check("Probemodul ist lesbar", grund is None, grund or "")
    gefunden = [b.problem for b in befunde]

    check("Veränderlicher Vorgabewert wird gefunden",
          any("Veränderlicher Vorgabewert" in p for p in gefunden), ", ".join(gefunden[:3]))
    check("open() ohne encoding wird gefunden",
          sum("open() ohne encoding" in p for p in gefunden) == 1,
          f"{sum('open() ohne encoding' in p for p in gefunden)}×")
    check("open(..., 'rb') wird NICHT bemängelt – Binärmodus kennt kein encoding",
          sum("open() ohne encoding" in p for p in gefunden) == 1)
    check("Begründetes except wird anerkannt",
          not any("Aufräum" in p for p in gefunden), ", ".join(
              p for p in gefunden if "Aufräum" in p))
    check("subprocess ohne timeout wird gefunden",
          any("ohne timeout" in p for p in gefunden))

    # Der Befund, den der Prüfer beim /tempo-Absturz übersehen hat
    ungesichert = [b for b in befunde if "fehlendes Programm" in b.problem]
    check("subprocess ohne Absicherung gegen ein fehlendes Programm wird gefunden",
          len(ungesichert) == 2, f"{len(ungesichert)} Treffer")
    check("Das falsch abgesicherte except SubprocessError zählt als ungesichert",
          any(b.zeile == next(i for i, z in enumerate(
              (CODE / "probe.py").read_text(encoding="utf-8").splitlines(), 1)
              if 'subprocess.run(["wmic"' in z) for b in ungesichert),
          ", ".join(str(b.zeile) for b in ungesichert))
    check("Der Befund ist als 'hoch' eingestuft",
          all(b.schwere == "hoch" for b in ungesichert),
          ", ".join(b.schwere for b in ungesichert))
    check("except (OSError, …) gilt als abgesichert",
          len(ungesichert) == 2, "sonst wären es 3")
    check("== None wird gefunden", any("== None" in p for p in gefunden))
    check("assert wird gefunden", any("assert" in p for p in gefunden))

    schreibend = [b for b in befunde if "scheitert stillschweigend" in b.problem]
    optional = [b for b in befunde if "ohne Vermerk übergangen" in b.problem]
    check("Verschluckter Schreibfehler wird als 'hoch' eingestuft",
          len(schreibend) == 1 and schreibend[0].schwere == "hoch",
          schreibend[0].problem if schreibend else "keiner")
    check("Verschluckter optionaler Schritt bleibt 'gering'",
          len(optional) == 1 and optional[0].schwere == "gering",
          optional[0].problem if optional else "keiner")
    check("Der Schreibbefund nennt den verantwortlichen Aufruf",
          bool(schreibend) and "write_text" in schreibend[0].problem,
          schreibend[0].problem if schreibend else "")

    # Jedes Zitat muss wirklich in der Datei stehen
    zeilen = pfad.read_text(encoding="utf-8").splitlines()
    stimmig = all(b.zitat and b.zitat == zeilen[b.zeile - 1].strip() for b in befunde)
    check("Jeder statische Befund zitiert die echte Zeile", stimmig)

    # ---------------------------------------------- Modell: Zitat wird geprüft
    echte_zeile = next(i for i, z in enumerate(zeilen, 1) if "puffer.append" in z)
    ehrlich = json.dumps({"befunde": [{
        "zeile": echte_zeile, "zitat": "puffer.append(inhalt)",
        "problem": "Anhängen an einen geteilten Vorgabewert",
        "auswirkung": "Werte früherer Aufrufe bleiben erhalten.",
        "vorschlag": "Vorgabewert None verwenden.", "schwere": "hoch"}]})
    erfunden = json.dumps({"befunde": [{
        "zeile": 5, "zitat": "self.datenbank.commit()",
        "problem": "Transaktion ohne Rollback",
        "auswirkung": "Halber Schreibvorgang bleibt stehen.",
        "vorschlag": "try/except mit rollback.", "schwere": "hoch"}]})

    pruefer = CodePruefer(FakeAgent(FakeLLM([ehrlich])), cfg)
    angenommen, verworfen, grund = pruefer._abschnitt_pruefen(pfad, "probe.py", 1, 40)
    check("Belegter Modellbefund wird übernommen",
          len(angenommen) == 1 and not verworfen and grund is None,
          angenommen[0].problem if angenommen else str(verworfen))
    check("Die Zeilennummer wird auf die echte Fundstelle korrigiert",
          bool(angenommen) and angenommen[0].zeile == echte_zeile,
          str(angenommen[0].zeile) if angenommen else "")

    pruefer = CodePruefer(FakeAgent(FakeLLM([erfunden])), cfg)
    angenommen, verworfen, _ = pruefer._abschnitt_pruefen(pfad, "probe.py", 1, 40)
    check("Erfundener Modellbefund wird verworfen",
          not angenommen and len(verworfen) == 1, str(verworfen))
    check("Die Verwerfung nennt den Grund",
          bool(verworfen) and "steht so nicht im Quelltext" in verworfen[0],
          verworfen[0] if verworfen else "")

    # Kaputtes JSON darf nicht als Befund durchgehen
    pruefer = CodePruefer(FakeAgent(FakeLLM(["Ich habe da mal geschaut, sieht gut aus."])), cfg)
    angenommen, _, grund = pruefer._abschnitt_pruefen(pfad, "probe.py", 1, 40)
    check("Prosa statt JSON ergibt keinen Befund",
          not angenommen and grund is not None, str(grund))

    # ---------------------------------------------------- ganzer Durchlauf
    pruefer = CodePruefer(FakeAgent(FakeLLM([])), cfg)
    bericht = pruefer.pruefen(mit_modell=False)
    check("Durchlauf ohne Modell liefert Befunde",
          bericht.dateien == 1 and bericht.befunde, bericht.kurzfassung())
    check("Das Modell wurde dabei nicht befragt", pruefer.agent.llm.aufrufe == 0)
    check("Ungeprüftes wird ausgewiesen",
          any("nicht angefordert" in e for e in bericht.nicht_geprueft))

    markdown = bericht.als_markdown("Code-Prüfung Test")
    check("Bericht trennt Wichtiges von Kleinkram",
          "Was zuerst angesehen werden sollte" in markdown and "Kleinkram" in markdown)
    check("Bericht enthält den Abschnitt 'Nicht geprüft'", "## Nicht geprüft" in markdown)
    check("Bericht zitiert Code in einem Block", "```python" in markdown)

    # Modelldurchlauf: Abschnittsbudget wird eingehalten
    llm = FakeLLM(['{"befunde": []}'] * 10)
    pruefer = CodePruefer(FakeAgent(llm), cfg)
    bericht = pruefer.pruefen(mit_modell=True, abschnitte=3)
    check("Abschnittsbudget wird eingehalten",
          llm.aufrufe <= 3 and bericht.modell_abschnitte <= 3,
          f"{llm.aufrufe} Aufrufe, {bericht.modell_abschnitte} Abschnitte")

    # ------------------------- Aus dem echten Lauf auf Joshs Rechner gelernt
    # (a) Alle sechs Abschnitte gingen nach tools.py; 13 Module blieben
    #     inhaltlich ungesehen.
    # Unterschiedlicher Inhalt je Datei: inhaltsgleiche Kopien werden seit
    # dem Doppel-Fund bewusst zusammengefasst und zaehlen nur einmal.
    for nummer, name in enumerate(("gross.py", "mittel.py", "klein.py"), 1):
        (CODE / name).write_text(
            PROBE.replace("speichern", f"speichern_{nummer}")
            + f"\n\n# Fueller {nummer}\n" * (200 - nummer * 20), encoding="utf-8")
    llm = FakeLLM(['{"befunde": []}'] * 20)
    pruefer = CodePruefer(FakeAgent(llm), cfg)
    bericht = pruefer.pruefen(mit_modell=True, abschnitte=6)
    dateien = [a for _, a, _, _ in pruefer._abschnitte_waehlen(
        pruefer.quelldateien(), bericht, 6)]
    check("Modellabschnitte verteilen sich über mehrere Dateien",
          len(set(dateien)) >= 3, ", ".join(dateien))
    check("Keine Datei bekommt mehr als zwei Abschnitte",
          all(dateien.count(d) <= 2 for d in set(dateien)),
          ", ".join(dateien))

    # (b) Vier Modellbefunde aus dem echten Lauf, die alle falsch waren.
    zeilen = (CODE / "probe.py").read_text(encoding="utf-8").splitlines()
    def_zeile = next(i for i, z in enumerate(zeilen, 1) if z.startswith("def speichern"))
    unsinn = json.dumps({"befunde": [
        {"zeile": def_zeile, "zitat": zeilen[def_zeile - 1],
         "problem": "Die Methode wird ohne Rückgabewertprüfung aufgerufen.",
         "auswirkung": "Der Fehlerfall bleibt unbemerkt.",
         "vorschlag": "Rückgabewert auswerten.", "schwere": "hoch"},
    ]})
    pruefer = CodePruefer(FakeAgent(FakeLLM([unsinn])), cfg)
    angenommen, verworfen, _ = pruefer._abschnitt_pruefen(
        CODE / "probe.py", "probe.py", 1, 40)
    check("Befund auf einer def-Zeile wird verworfen",
          not angenommen and verworfen
          and "keinen Laufzeitfehler" in verworfen[0], str(verworfen))

    echte = next(i for i, z in enumerate(zeilen, 1) if "puffer.append" in z)
    vermutung = json.dumps({"befunde": [
        {"zeile": echte, "zitat": "puffer.append(inhalt)",
         "problem": "Es ist nicht klar, ob puffer wirklich eine Liste ist.",
         "auswirkung": "Könnte fehlschlagen.", "vorschlag": "Sicherstellen.",
         "schwere": "hoch"}]})
    pruefer = CodePruefer(FakeAgent(FakeLLM([vermutung])), cfg)
    angenommen, verworfen, _ = pruefer._abschnitt_pruefen(
        CODE / "probe.py", "probe.py", 1, 40)
    zuweisung = json.dumps({"befunde": [
        {"zeile": next(i for i, z in enumerate(zeilen, 1) if "puffer.append" in z),
         "zitat": "puffer.append(inhalt)",
         "problem": "Der Fehler wird hier ohne Prüfung verschluckt.",
         "auswirkung": "Der Aufrufer sieht Erfolg.",
         "vorschlag": "Prüfen.", "schwere": "hoch"}]})
    pruefer = CodePruefer(FakeAgent(FakeLLM([zuweisung])), cfg)
    angenommen_z, verworfen_z, _ = pruefer._abschnitt_pruefen(
        CODE / "probe.py", "probe.py", 1, 60)
    check("Fehlerbehauptung auf einer Zuweisungszeile wird verworfen",
          not angenommen_z and verworfen_z and "Zuweisung" in verworfen_z[0],
          str(verworfen_z))

    check("Vermutung statt Befund wird verworfen",
          not angenommen and verworfen and "Vermutung" in verworfen[0], str(verworfen))

    # (c) Ein Modellbefund darf nie über einem vom Parser belegten stehen.
    guter = json.dumps({"befunde": [
        {"zeile": echte, "zitat": "puffer.append(inhalt)",
         "problem": "Anhängen an einen geteilten Vorgabewert über Aufrufe hinweg.",
         "auswirkung": "Alte Werte bleiben erhalten.",
         "vorschlag": "Vorgabewert None verwenden.", "schwere": "hoch"}]})
    pruefer = CodePruefer(FakeAgent(FakeLLM([guter])), cfg)
    angenommen, _, _ = pruefer._abschnitt_pruefen(CODE / "probe.py", "probe.py", 1, 40)
    check("Modellbefund wird auf höchstens 'mittel' begrenzt",
          bool(angenommen) and angenommen[0].schwere == "mittel",
          angenommen[0].schwere if angenommen else "")

    # Inhaltsgleiche Kopie wird erkannt und nur einmal geprueft
    (CODE / "kopie_von_probe.py").write_text(
        (CODE / "probe.py").read_text(encoding="utf-8"), encoding="utf-8")
    pruefer = CodePruefer(FakeAgent(FakeLLM([])), cfg)
    doppelbericht = pruefer.pruefen(mit_modell=False)
    check("Inhaltsgleiche Kopie wird nur einmal geprueft",
          any("inhaltsgleiche Kopien" in e for e in doppelbericht.nicht_geprueft),
          "; ".join(doppelbericht.nicht_geprueft[:1]))
    namen = [a for _, a in pruefer.quelldateien()]
    check("Von zwei inhaltsgleichen Dateien bleibt genau eine",
          sum(1 for n in namen if n in ("probe.py", "kopie_von_probe.py")) == 1,
          ", ".join(namen))
    (CODE / "kopie_von_probe.py").unlink()

    for name in ("gross.py", "mittel.py", "klein.py"):
        (CODE / name).unlink()

    # Syntaxfehler darf den Lauf nicht abbrechen
    (CODE / "kaputt.py").write_text("def f(:\n    pass\n", encoding="utf-8")
    pruefer = CodePruefer(FakeAgent(FakeLLM([])), cfg)
    bericht = pruefer.pruefen(mit_modell=False)
    check("Syntaxfehler bricht den Lauf nicht ab",
          bericht.dateien == 1 and any("Syntaxfehler" in e for e in bericht.nicht_geprueft),
          "; ".join(bericht.nicht_geprueft[:2]))

    # ------------------ Absturz vom echten Lauf: Werkzeug ohne vorheriges sync()
    # AttributeError: 'NoneType' object has no attribute 'canonical_name'
    # --codereview baut keinen Index auf, will den Bericht aber ablegen.
    from jarvis.agent import JarvisAgent  # noqa: E402

    (VAULT / "Bestand.md").write_text("# Bestand\n\nText.\n", encoding="utf-8")
    frisch = JarvisAgent(index=__import__("jarvis.index", fromlist=["KnowledgeIndex"])
                         .KnowledgeIndex(embeddings=stubs.StubEmbedding()))
    check("Struktur ist vor dem ersten sync() wirklich None",
          frisch.index.structure is None)

    ergebnis = frisch.tools.notiz_erstellen(
        titel="Code-Prüfung Testlauf", inhalt="# Bericht\n\nInhalt.\n",
        ordner="_System/Code/Pruefungen")
    check("Notiz anlegen ohne sync() stürzt nicht ab", ergebnis.ok, ergebnis.meldung)
    check("Die Notiz liegt wirklich da",
          bool(list(VAULT.glob("**/Code-Prüfung*.md"))
               or list(VAULT.glob("**/Code-*Testlauf*.md"))),
          ", ".join(p.name for p in VAULT.glob("**/*.md")))
    check("Die Struktur wurde dabei nachgezogen", frisch.index.structure is not None)

    # ------------- Der Bericht landete auf Joshs Rechner in _Fertigkeiten/
    # Ursache: match_folder(threshold=0.6) auf den GANZEN Pfad. Irgendein
    # vorhandener Ordner lag über der Schwelle und gewann gegen den
    # ausdrücklich angeforderten Unterordner.
    for name in ("_Fertigkeiten", "_Lernen", "Deep Learning", "_System/Code"):
        (VAULT / name).mkdir(parents=True, exist_ok=True)
    (VAULT / "_Fertigkeiten" / "f.md").write_text(
        "---\ntags:\n  - fertigkeit\n  - selbstgebaut\n---\n# F\n", encoding="utf-8")
    (VAULT / "Deep Learning" / "d.md").write_text("# D\n", encoding="utf-8")
    frisch.sync()
    waehlen = frisch.tools._zielordner_waehlen

    check("Mehrstufiger Pfad wird wörtlich genommen",
          waehlen("_System/Code/Pruefungen") == "_System/Code/Pruefungen",
          waehlen("_System/Code/Pruefungen"))
    check("Vorhandener mehrstufiger Pfad wird wiederverwendet",
          waehlen("_System/Code") == "_System/Code", waehlen("_System/Code"))
    check("Tippfehler in einem einstufigen Namen wird noch korrigiert",
          waehlen("Depp Learning") == "Deep Learning", waehlen("Depp Learning"))
    check("Groß-/Kleinschreibung trifft den vorhandenen Ordner",
          waehlen("deep learning") == "Deep Learning", waehlen("deep learning"))
    check("Ein wirklich neuer Ordnername wird nicht auf einen fremden gebogen",
          waehlen("Pruefberichte") == "Pruefberichte", waehlen("Pruefberichte"))

    ergebnis = frisch.tools.notiz_erstellen(
        titel="Code-Prüfung Ablageort", inhalt="# B\n\nText.\n",
        ordner="_System/Code/Pruefungen")
    check("Der Bericht landet im angeforderten Unterordner",
          ergebnis.ok and str(ergebnis.daten.get("pfad", "")).startswith(
              "_System/Code/Pruefungen/"),
          str(ergebnis.daten.get("pfad")))

    # Zweiter Lauf am selben Tag ersetzt, statt _2.md danebenzulegen
    titel = "Code-Prüfung Ablageort"
    vorher = len(list(VAULT.glob("**/Code-Prüfung*Ablageort*.md")))
    if frisch.tools.resolve_note(titel) is not None:
        zweit = frisch.tools.notiz_ueberschreiben(name=titel, inhalt="# B\n\nZweiter Lauf.\n")
    else:
        zweit = frisch.tools.notiz_erstellen(titel=titel, inhalt="# B\n\nZweiter.\n",
                                             ordner="_System/Code/Pruefungen")
    nachher = list(VAULT.glob("**/Code-Prüfung*Ablageort*.md"))
    check("Zweiter Lauf legt keine zweite Notiz an",
          zweit.ok and len(nachher) == vorher,
          ", ".join(p.name for p in nachher))
    check("Der Inhalt ist der neue",
          bool(nachher) and "Zweiter Lauf" in nachher[0].read_text(encoding="utf-8"))

    # Terminalausgabe darf bei lauter 'gering' nicht leer wirken
    pruefer = CodePruefer(FakeAgent(FakeLLM([])), cfg)
    nur_klein = pruefer.pruefen(mit_modell=False)
    ausgabe = "\n".join(nur_klein.terminalfassung())
    check("Terminalausgabe nennt auch die geringen Befunde konkret",
          "Kleinkram" in ausgabe and ":" in ausgabe, ausgabe.splitlines()[0] if ausgabe else "")

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
