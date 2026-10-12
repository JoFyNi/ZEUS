"""Tests für die mehrzeilige Eingabe.

Josh konnte einen Anweisungsprompt nicht eingeben: Das Terminal las jede Zeile
einzeln und der Agent beantwortete Zeile 1 ohne die Regeln aus Zeile 5. Für ein
Anweisungsblatt ist das unbrauchbar.

Geprüft wird die Unterscheidung, auf der alles beruht:
  * EINGEFÜGTER Text kommt praktisch gleichzeitig  -> eine Eingabe
  * GETIPPTER Text kommt mit Pausen dazwischen     -> getrennte Eingaben
Dazu die ausdrücklichen Wege (Blockmarke, Datei) für den Fall, dass die
Erkennung danebenliegt.
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from jarvis.eingabe import EINFUEGE_FENSTER, Eingabeleser, datei_lesen  # noqa: E402

FAILURES = []
TMP = Path(tempfile.mkdtemp(prefix="jarvis_eingabe_"))


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


class Tastatur(io.RawIOBase):
    """Ein stdin-Ersatz, der Zeilen mit einstellbarer Verzögerung liefert.

    Damit lässt sich Einfügen (keine Pause) von Tippen (Pause) unterscheiden,
    ohne ein echtes Terminal zu brauchen.
    """

    def __init__(self):
        self._zeilen: list = []
        self._sperre = threading.Condition()
        self._ende = False

    def tippen(self, zeile: str, pause: float = 0.0) -> None:
        if pause:
            time.sleep(pause)
        with self._sperre:
            self._zeilen.append(zeile + "\n")
            self._sperre.notify_all()

    def einfuegen(self, text: str) -> None:
        """Alle Zeilen auf einmal - so verhält sich ein Einfügevorgang."""
        with self._sperre:
            self._zeilen.extend(z + "\n" for z in text.split("\n"))
            self._sperre.notify_all()

    def schliessen(self) -> None:
        with self._sperre:
            self._ende = True
            self._sperre.notify_all()

    def readline(self, *args) -> str:
        with self._sperre:
            while not self._zeilen and not self._ende:
                self._sperre.wait(0.05)
            if self._zeilen:
                return self._zeilen.pop(0)
            return ""


def main() -> int:
    # ------------------------------------------------- 1. Einfügen = EINE Eingabe
    tastatur = Tastatur()
    leser = Eingabeleser(strom=tastatur)
    check("Der Hintergrundleser läuft", leser.aktiv)

    prompt = ("Analysiere deinen Zustand.\n"
              "VERBOTEN:\n"
              "- Keine Dateiliste.\n"
              "- Kein Code-Vorlesen.\n"
              "FORMAT:\n"
              "LAGE\nVORSCHLAG 1\nRANGFOLGE")
    tastatur.einfuegen(prompt)
    ergebnis = leser.block_lesen()
    check("Eingefügter Block kommt als eine Eingabe an", ergebnis == prompt,
          f"{(ergebnis or '').count(chr(10)) + 1} statt {prompt.count(chr(10)) + 1} Zeilen")
    check("Die letzte Zeile fehlt nicht",
          (ergebnis or "").endswith("RANGFOLGE"), (ergebnis or "")[-30:])

    # ------------------------------------------- 2. Tippen bleibt getrennt
    tastatur.tippen("erste Frage")
    erste = leser.block_lesen()
    check("Getippte Zeile kommt einzeln", erste == "erste Frage", str(erste))

    # Pause deutlich über dem Fenster: das ist eine neue Eingabe
    threading.Thread(
        target=lambda: tastatur.tippen("zweite Frage", pause=EINFUEGE_FENSTER * 4),
        daemon=True).start()
    zweite = leser.block_lesen()
    check("Nach einer Pause beginnt eine neue Eingabe", zweite == "zweite Frage",
          str(zweite))

    # --------------------------------------------- 3. Ausdrücklicher Blockmodus
    tastatur.einfuegen('"""')
    time.sleep(EINFUEGE_FENSTER * 2)
    tastatur.tippen("Zeile A", pause=EINFUEGE_FENSTER * 2)
    tastatur.tippen("Zeile B", pause=EINFUEGE_FENSTER * 2)
    tastatur.tippen('"""', pause=EINFUEGE_FENSTER * 2)
    block = leser.block_lesen()
    check("Blockmarke sammelt trotz Tipppausen", block == "Zeile A\nZeile B", repr(block))

    # ------------------------------------------------------ 4. Ende der Eingabe
    tastatur.schliessen()
    check("EOF liefert None", leser.block_lesen() is None)

    # ------------------------------------------------------------ 5. Aus Datei
    datei = TMP / "prompt.txt"
    datei.write_text(prompt + "\n", encoding="utf-8")
    check("Datei wird als eine Eingabe gelesen", datei_lesen(datei) == prompt)

    fehlt = False
    try:
        datei_lesen(TMP / "gibtsnicht.txt")
    except FileNotFoundError:
        fehlt = True
    check("Fehlende Datei meldet sich deutlich", fehlt)

    zu_gross = TMP / "gross.txt"
    zu_gross.write_text("x" * 500, encoding="utf-8")
    gross = False
    try:
        datei_lesen(zu_gross, grenze=100)
    except ValueError:
        gross = True
    check("Zu große Datei wird abgelehnt", gross)

    # ------------------------------------ 6. Leerzeile bleibt leer (= Mikrofon)
    tastatur2 = Tastatur()
    leser2 = Eingabeleser(strom=tastatur2)
    tastatur2.tippen("")
    leer = leser2.block_lesen()
    check("Leere Eingabe bleibt leer – sonst geht das Mikrofon nicht mehr auf",
          leer == "", repr(leer))

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
