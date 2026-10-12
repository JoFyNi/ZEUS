"""Mehrzeilige Eingabe: eingefügter Text wird als EIN Auftrag gelesen.

Das Problem, das dahintersteckt: `input()` liefert genau eine Zeile. Fügt man
einen zwanzigzeiligen Prompt ins Terminal ein, sieht die Schleife zwanzig
Eingaben - und der Agent beantwortet jede Zeile einzeln. Für ein
Anweisungsblatt ist das nicht bloß unschön, es ist unbrauchbar: Die erste
Zeile wird ohne die Regeln beantwortet, die in Zeile 5 stehen.

Drei Wege, alle hier:

1. AUTOMATISCH - Einfügen wird erkannt. Ein Hintergrund-Thread liest stdin in
   eine Warteschlange. Nach der ersten Zeile wird kurz gewartet: Kommen weitere
   Zeilen innerhalb weniger Hundertstelsekunden, gehören sie zum selben
   Einfügevorgang. Getippt wird nie so schnell.

   Warum ein Thread und nicht select()? Python puffert stdin intern. Nach einem
   readline() können die restlichen eingefügten Zeilen in Pythons Puffer liegen
   statt im Betriebssystem - select() meldet dann "nichts da", obwohl der Text
   längst angekommen ist. Der Thread liest stur weiter und umgeht das.

2. AUSDRÜCKLICH - eine Zeile mit ``\"\"\"`` beginnt einen Block, eine zweite
   beendet ihn. Für den Fall, dass die Erkennung mal danebenliegt oder man
   von Hand tippen will.

3. AUS DATEI - `/prompt <pfad>` liest eine Datei als eine einzige Eingabe.
   Der sicherste Weg für lange Anweisungsblätter.
"""
from __future__ import annotations

import queue
import sys
import threading
from typing import List, Optional

# Wie lange nach einer Zeile auf weitere gewartet wird. Einfügen liefert alle
# Zeilen praktisch gleichzeitig; Tippen braucht Zehntelsekunden je Zeichen.
# 120 ms ist großzügig genug für langsame Terminals und weit unter allem,
# was ein Mensch zwischen zwei Zeilen schafft.
EINFUEGE_FENSTER = 0.12

# Obergrenze, damit eine hängende Umleitung nicht endlos sammelt.
MAX_ZEILEN = 2000

BLOCK_MARKEN = ('"""', "'''", "```")


class Eingabeleser:
    """Liest stdin in einem Thread und gibt vollständige Blöcke zurück."""

    def __init__(self, strom=None):
        self.strom = strom or sys.stdin
        self._warteschlange: "queue.Queue[Optional[str]]" = queue.Queue()
        self._ende = False
        self._thread: Optional[threading.Thread] = None
        self.aktiv = False
        try:
            self._thread = threading.Thread(target=self._lesen, daemon=True)
            self._thread.start()
            self.aktiv = True
        except Exception:
            # Ohne Thread bleibt der einfache Weg über input() - lieber
            # zeilenweise als gar nicht.
            self.aktiv = False

    # ------------------------------------------------------------- Hintergrund
    def _lesen(self) -> None:
        while True:
            try:
                zeile = self.strom.readline()
            except Exception:
                self._warteschlange.put(None)
                return
            if zeile == "":                     # EOF (Strg+Z / Strg+D)
                self._warteschlange.put(None)
                return
            self._warteschlange.put(zeile.rstrip("\r\n"))

    # ----------------------------------------------------------------- Abrufen
    def _naechste(self, wartezeit: Optional[float]) -> Optional[str]:
        try:
            return self._warteschlange.get(timeout=wartezeit) if wartezeit is not None \
                else self._warteschlange.get()
        except queue.Empty:
            return ""

    # Rückgabe von block_lesen(), wenn das Zeitlimit ablief, ohne dass etwas
    # kam. Ein eigenes Objekt statt "" oder None: Beides bedeutet hier schon
    # etwas anderes (Leerzeile bzw. EOF), und genau solche Doppeldeutigkeiten
    # führen dazu, dass ein Programm beim Beenden noch eine Runde dreht.
    LEERLAUF = object()

    def block_lesen(self, zeitlimit: Optional[float] = None):
        """Eine Eingabe holen - egal ob eine Zeile oder ein eingefügter Block.

        Rückgabe None heißt: Eingabe beendet (EOF).
        Rückgabe LEERLAUF heißt: `zeitlimit` Sekunden lang kam nichts.
        """
        if zeitlimit is None:
            erste = self._naechste(None)
        else:
            # Nicht über _naechste(): das liefert "" sowohl bei Zeitablauf als
            # auch für eine getippte Leerzeile. Eine Leerzeile bedeutet in der
            # Oberfläche aber "Spracheingabe starten" - beides zu verwechseln,
            # hieße, bei jedem Zeitablauf das Mikrofon einzuschalten.
            try:
                erste = self._warteschlange.get(timeout=zeitlimit)
            except queue.Empty:
                return self.LEERLAUF
        if erste is None:
            return None

        # Ausdrücklicher Block: """ … """
        marke = erste.strip()
        if marke in BLOCK_MARKEN:
            return self._bis_marke(marke)

        zeilen: List[str] = [erste]
        # Alles einsammeln, was unmittelbar hinterherkommt - das ist der
        # Einfügevorgang.
        while len(zeilen) < MAX_ZEILEN:
            weitere = self._naechste(EINFUEGE_FENSTER)
            if weitere is None:                 # EOF mitten im Block
                break
            if weitere == "":                   # nichts mehr gekommen
                break
            zeilen.append(weitere)
        return "\n".join(zeilen).rstrip()

    def _bis_marke(self, marke: str) -> str:
        """Blockmodus: sammeln, bis die Schlussmarke kommt."""
        zeilen: List[str] = []
        while len(zeilen) < MAX_ZEILEN:
            zeile = self._naechste(None)
            if zeile is None or zeile.strip() == marke:
                break
            zeilen.append(zeile)
        return "\n".join(zeilen).rstrip()


    # ------------------------------------------------------------- Zwischenstand
    def wartet(self) -> int:
        """Wie viele Zeilen schon in der Warteschlange liegen.

        Wird gebraucht, um zu erkennen, ob während einer laufenden Antwort
        getippt wurde. Die Zeichen sind dann längst vom Terminal in die
        Ausgabe hineingeschrieben worden - lesbar wird das erst, wenn man sie
        hinterher sauber noch einmal anzeigt.
        """
        return self._warteschlange.qsize()


class Echosperre:
    """Schaltet das Echo des Terminals ab, solange etwas gerendert wird.

    Das Problem: Während der Agent antwortet, zeichnet die Oberfläche ihre
    Ausgabe fortlaufend neu. Tippt der Benutzer in dieser Zeit, schreibt der
    Terminaltreiber die Zeichen mitten in diese Ausgabe hinein. Herausgekommen
    ist dabei zum Beispiel:

        jarvis_·ok, dann erstelle sietem/Code

    - der getippte Satz und ein Dateipfad ineinander verschränkt. Die Eingabe
    selbst ging nicht verloren (der Lesethread hat sie), nur das Bild war
    zerstört.

    Ausdrücklich BEST EFFORT. Auf Windows kann eine bereits laufende
    Konsolenleseoperation die alte Betriebsart behalten; dann bleibt ein Rest
    Durcheinander. Deshalb zeigt die Oberfläche zusätzlich hinterher an, was
    währenddessen eingegeben wurde. Schlägt das Umschalten fehl, passiert
    einfach nichts - nie eine Ausnahme nach oben.
    """

    _ENABLE_ECHO_INPUT = 0x0004

    def __init__(self, strom=None):
        self.strom = strom or sys.stdin
        self._wieder = None
        self._art = ""

    def __enter__(self) -> "Echosperre":
        try:
            if not self.strom.isatty():
                return self
        except Exception:
            return self
        self._aus()
        return self

    def __exit__(self, *_) -> bool:
        self._an()
        return False

    # -------------------------------------------------------------- intern
    def _aus(self) -> None:
        try:
            import termios                                  # POSIX
            fd = self.strom.fileno()
            alt = termios.tcgetattr(fd)
            neu = list(alt)
            neu[3] = neu[3] & ~termios.ECHO                 # lflag
            termios.tcsetattr(fd, termios.TCSADRAIN, neu)
            self._wieder, self._art = (fd, alt), "posix"
            return
        except Exception:
            pass
        try:
            import ctypes                                   # Windows
            kernel32 = ctypes.windll.kernel32               # type: ignore[attr-defined]
            griff = kernel32.GetStdHandle(-10)              # STD_INPUT_HANDLE
            modus = ctypes.c_uint()
            if not kernel32.GetConsoleMode(griff, ctypes.byref(modus)):
                return
            # Nur das Echo abschalten. ENABLE_LINE_INPUT bleibt stehen, sonst
            # liefert readline() keine ganzen Zeilen mehr.
            kernel32.SetConsoleMode(griff, modus.value & ~self._ENABLE_ECHO_INPUT)
            self._wieder, self._art = (griff, modus.value), "windows"
        except Exception:
            pass

    def _an(self) -> None:
        if not self._wieder:
            return
        try:
            if self._art == "posix":
                import termios
                fd, alt = self._wieder
                termios.tcsetattr(fd, termios.TCSADRAIN, alt)
            else:
                import ctypes
                griff, modus = self._wieder
                ctypes.windll.kernel32.SetConsoleMode(griff, modus)  # type: ignore[attr-defined]
        except Exception:
            pass
        finally:
            self._wieder, self._art = None, ""


def datei_lesen(pfad, grenze: int = 200_000) -> str:
    """Inhalt einer Datei als eine einzige Eingabe - für lange Anweisungen."""
    from pathlib import Path

    ziel = Path(str(pfad).strip().strip('"').strip("'")).expanduser()
    if not ziel.is_file():
        raise FileNotFoundError(f"'{ziel}' gibt es nicht.")
    text = ziel.read_text(encoding="utf-8", errors="replace")
    if len(text) > grenze:
        raise ValueError(f"Datei ist zu groß ({len(text)} Zeichen, Grenze {grenze}).")
    return text.strip()
