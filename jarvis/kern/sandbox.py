"""Die Sandkiste: fremder Code laeuft woanders, nicht hier.

Wo Zeus Code ausfuehrt, ist genau eine Stelle - `kern/selbst.py` startet die
Testreihen eines Patchvorschlags. Die kopierte bereits das Projekt in einen
Temp-Ordner, und das war richtig. Aber die Isolation war eine Eigenheit
dieser einen Funktion: kein Umgebungsschutz, keine Grenze fuer die Ausgabe,
kein Schutz davor, dass ein Vorschlag wieder in die ECHTE Vault schreibt,
waehrend er "in der Kopie" getestet wird.

Diese Datei macht daraus eine benannte Fassung mit vier Schranken. Ehrlich
gesagt, was jede kann und was nicht - das ist wichtiger als die Schranke
selbst, weil eine ueberschaetzte Schranke schlimmer ist als keine:

    ARBEITSORDNER   Ein frischer Temp-Ordner je Lauf, danach geloescht.
                    Wirkt: relative Pfade, `cwd`, Schreiben ins Projekt.
                    Wirkt NICHT gegen absolute Pfade.

    ZEITGRENZE      Harter Abbruch. Wirkt gegen Haenger und Endlosschleifen.
                    Der Kindprozess wird beendet, seine Kinder unter Windows
                    ueber die Prozessgruppe mit.

    UMGEBUNG        Der Kindprozess bekommt eine AUFGERAEUMTE Umgebung:
                    keine API-Schluessel, kein OBSIDIAN_VAULT, dafuer ein
                    umgelenktes TMPDIR und HOME. Wirkt gegen "der Test
                    schreibt in die echte Vault" und gegen das Abfliessen
                    von Zugangsdaten. Das ist die Schranke, die vorher
                    voellig fehlte.

    AUSGABEGRENZE   Abgeschnittene stdout/stderr. Wirkt gegen den Prozess,
                    der Gigabyte druckt und den Arbeitsspeicher fuellt.

WAS NICHT GEHT, und das steht hier, statt es zu verschweigen:

    Dies ist KEINE Sicherheitsgrenze gegen boesartigen Code. Ohne Container,
    Benutzerwechsel oder Jobobjekt kann ein Kindprozess unter Windows auf
    alles zugreifen, worauf der startende Benutzer zugreifen kann. Ein
    absoluter Pfad genuegt. Wer hier echte Isolation braucht, braucht einen
    Container - und dann ist diese Klasse die Stelle, an der er eingesetzt
    wird, nicht zwanzig Aufrufstellen.

    Was sie leistet, ist der Schutz gegen UNFAELLE: der Testlauf, der
    aufraeumt und dabei das Original trifft; der Patch, der die Vault des
    Nutzers beschreibt; der haengende Prozess, den niemand beendet. Genau
    diese Faelle sind in diesem Projekt schon eingetreten.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Sequence

# Umgebungsvariablen, die der Kindprozess NIE sehen darf. Alles, was auf
# Zugangsdaten oder auf die echte Vault zeigt.
_GESPERRT = (
    "OBSIDIAN_VAULT", "VAULT_PATH", "WEB_SUCH_SCHLUESSEL", "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY", "AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID",
    "GITHUB_TOKEN", "HF_TOKEN", "GOOGLE_API_KEY", "AZURE_OPENAI_KEY",
)

# Was durchgelassen wird. Eine Positivliste, keine Negativliste: Wer eine
# neue Geheimnis-Variable einfuehrt und die Negativliste vergisst, hat sie
# im Kindprozess. Umgekehrt faellt ein fehlender Durchlass sofort auf, weil
# etwas nicht startet.
_DURCHLASS = (
    "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "NUMBER_OF_PROCESSORS",
    "PROCESSOR_ARCHITECTURE", "PYTHONHOME", "LANG", "LC_ALL", "TZ",
    "SYSTEMDRIVE", "PROGRAMDATA", "PROGRAMFILES", "COMMONPROGRAMFILES",
)

# Ausgabe, die mehr als das ist, wird abgeschnitten. 200 kB sind fuer eine
# Testreihe grosszuegig und fuer einen Amoklauf zu wenig.
AUSGABEGRENZE = 200_000


@dataclass
class Sandkastenlauf:
    """Was bei einem Lauf herauskam - gemessen, nicht behauptet."""
    befehl: List[str]
    rueckgabe: int = -1
    ausgabe: str = ""
    fehlerausgabe: str = ""
    dauer: float = 0.0
    abgebrochen: bool = False       # Zeitgrenze gerissen
    nicht_startbar: str = ""        # Programm fehlt o. ae.
    abgeschnitten: bool = False

    @property
    def ok(self) -> bool:
        return (self.rueckgabe == 0 and not self.abgebrochen
                and not self.nicht_startbar)

    def bericht(self) -> str:
        if self.nicht_startbar:
            return f"nicht startbar: {self.nicht_startbar}"
        if self.abgebrochen:
            return f"abgebrochen nach {self.dauer:.0f}s (Zeitgrenze)"
        letzte = (self.ausgabe or "").strip().splitlines()
        return (f"Rueckgabe {self.rueckgabe} in {self.dauer:.1f}s"
                + (f" – {letzte[-1][:120]}" if letzte else ""))


class Sandkasten:
    """Ein Arbeitsordner auf Zeit, in dem Befehle laufen duerfen.

    Als Kontextmanager benutzen; beim Verlassen wird aufgeraeumt - auch
    wenn etwas geworfen hat. Ein Temp-Ordner, den nur der Gutfall loescht,
    ist nach einer Woche Betrieb ein volles Laufwerk.
    """

    def __init__(self, name: str = "zeus", *, frist: float = 300.0,
                 ausgabegrenze: int = AUSGABEGRENZE,
                 netz: bool = False) -> None:
        self.name = str(name or "zeus")
        self.frist = max(1.0, float(frist))
        self.ausgabegrenze = int(ausgabegrenze)
        self.netz = bool(netz)
        self.ordner: Optional[Path] = None
        self.laeufe: List[Sandkastenlauf] = []
        self._offen = False

    # ------------------------------------------------------------ Lebenslauf
    def __enter__(self) -> "Sandkasten":
        return self.oeffnen()

    def __exit__(self, *_) -> None:
        self.schliessen()

    def oeffnen(self) -> "Sandkasten":
        if self._offen:
            return self
        # .resolve(), weil Windows hier den 8.3-Kurznamen liefert und
        # relative_to() zwischen Kurz- und Langform wirft. Dieselbe Falle
        # wie in tests/alle.py.
        self.ordner = Path(tempfile.mkdtemp(prefix=f"sand_{self.name}_")).resolve()
        (self.ordner / "heim").mkdir(exist_ok=True)
        (self.ordner / "temp").mkdir(exist_ok=True)
        self._offen = True
        return self

    def schliessen(self) -> None:
        if self.ordner is not None:
            shutil.rmtree(self.ordner, ignore_errors=True)
        self.ordner = None
        self._offen = False

    # ----------------------------------------------------------- Einrichten
    @property
    def arbeit(self) -> Path:
        """Der Ordner, in dem gearbeitet wird."""
        if self.ordner is None:
            raise RuntimeError("Der Sandkasten ist nicht geoeffnet.")
        ziel = self.ordner / "arbeit"
        ziel.mkdir(exist_ok=True)
        return ziel

    def hineinkopieren(self, quelle: Path,
                       ausnehmen: Sequence[str] = ()) -> Path:
        """Kopiert einen Baum in den Sandkasten und gibt die Kopie zurueck."""
        quelle = Path(quelle)
        ziel = self.arbeit
        muster = list(ausnehmen) or [".git", "__pycache__", "vector_store",
                                     ".jarvis", "*.pyc", ".venv", "node_modules"]
        if ziel.exists():
            shutil.rmtree(ziel, ignore_errors=True)
        shutil.copytree(quelle, ziel,
                        ignore=shutil.ignore_patterns(*muster))
        return ziel

    def datei_legen(self, relativ: str, inhalt: str) -> Path:
        pfad = self.arbeit / str(relativ)
        pfad.parent.mkdir(parents=True, exist_ok=True)
        pfad.write_text(inhalt, encoding="utf-8")
        return pfad

    # -------------------------------------------------------------- Umgebung
    def umgebung(self) -> dict:
        """Die aufgeraeumte Umgebung des Kindprozesses.

        Positivliste - siehe Modulkopf. `TMPDIR`/`TEMP`/`TMP` und `HOME`
        zeigen in den Sandkasten, damit ein Programm, das "irgendwo"
        ablegt, innerhalb bleibt.
        """
        raus = {}
        for schluessel in _DURCHLASS:
            wert = os.environ.get(schluessel)
            if wert:
                raus[schluessel] = wert
        heim = str((self.ordner or Path(".")) / "heim")
        temp = str((self.ordner or Path(".")) / "temp")
        raus.update({
            "HOME": heim, "USERPROFILE": heim, "APPDATA": heim,
            "LOCALAPPDATA": heim,
            "TMPDIR": temp, "TEMP": temp, "TMP": temp,
            "PYTHONIOENCODING": "utf-8",
            # Sagt jedem Kindprozess, dass er in der Sandkiste sitzt. Wer das
            # abfragt, kann sich anders verhalten - etwa keine echten
            # Netzaufrufe machen. Eine Bitte, keine Schranke.
            "ZEUS_SANDKASTEN": "1",
        })
        if not self.netz:
            # Kein echter Netzschutz - aber jede gaengige Bibliothek (requests,
            # urllib, pip) beachtet diese Variablen und laeuft dann in einen
            # toten Proxy statt ins Internet. Was das NICHT abhaelt, steht im
            # Modulkopf.
            raus.update({"http_proxy": "http://127.0.0.1:9", "HTTP_PROXY":
                         "http://127.0.0.1:9", "https_proxy":
                         "http://127.0.0.1:9", "HTTPS_PROXY":
                         "http://127.0.0.1:9", "no_proxy": "",
                         "ZEUS_KEIN_NETZ": "1"})
        for schluessel in _GESPERRT:
            raus.pop(schluessel, None)
        return raus

    # ------------------------------------------------------------ Ausfuehren
    def laufen(self, befehl: Sequence[str], *, frist: Optional[float] = None,
               arbeitsordner: Optional[Path] = None,
               eingabe: str = "") -> Sandkastenlauf:
        """Fuehrt einen Befehl aus. Wirft nie - der Befund steht im Ergebnis."""
        if self.ordner is None:
            raise RuntimeError("Der Sandkasten ist nicht geoeffnet.")
        befehl = [str(t) for t in befehl]
        lauf = Sandkastenlauf(befehl=list(befehl))
        begonnen = time.time()
        grenze = max(1.0, float(frist if frist is not None else self.frist))
        zusatz = {}
        if os.name == "nt":
            # Ohne eigene Prozessgruppe ueberlebt ein Enkelprozess das
            # Beenden des Kindes und haengt weiter im Speicher.
            zusatz["creationflags"] = getattr(subprocess,
                                              "CREATE_NEW_PROCESS_GROUP", 0)
        else:
            zusatz["start_new_session"] = True
        try:
            fertig = subprocess.run(
                befehl,
                cwd=str(arbeitsordner or self.arbeit),
                env=self.umgebung(),
                input=eingabe if eingabe else None,
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=grenze, **zusatz)
            lauf.rueckgabe = int(fertig.returncode)
            lauf.ausgabe, kurz1 = self._kappen(fertig.stdout or "")
            lauf.fehlerausgabe, kurz2 = self._kappen(fertig.stderr or "")
            lauf.abgeschnitten = kurz1 or kurz2
        except subprocess.TimeoutExpired as exc:
            lauf.abgebrochen = True
            lauf.ausgabe, _ = self._kappen(_text(exc.stdout))
            lauf.fehlerausgabe, _ = self._kappen(_text(exc.stderr))
        except OSError as exc:
            # Ein fehlendes Programm wirft OSError, NICHT SubprocessError -
            # genau diese Verwechslung hat /tempo schon einmal gekostet
            # (ENTWICKLUNG.md 2.6).
            lauf.nicht_startbar = f"{type(exc).__name__}: {exc}"
        lauf.dauer = time.time() - begonnen
        self.laeufe.append(lauf)
        return lauf

    def python(self, quelltext: str, *, frist: Optional[float] = None,
               dateiname: str = "lauf.py") -> Sandkastenlauf:
        """Schreibt Python in den Sandkasten und fuehrt es dort aus."""
        pfad = self.datei_legen(dateiname, quelltext)
        return self.laufen([sys.executable, str(pfad)], frist=frist)

    def testreihen(self, ordner: Optional[Path] = None, *,
                   gesamtfrist: float = 600.0,
                   haltepunkt: Optional[Callable[[], None]] = None
                   ) -> List[Sandkastenlauf]:
        """Faehrt alle `test_*.py` im Sandkasten - mit Gesamtfrist.

        Die Gesamtfrist ist der Unterschied zu einer Schleife mit
        Einzelfristen: 40 Reihen zu je 300 s sind dreieinhalb Stunden, und
        so lange wartet niemand auf einen Patchvorschlag.
        """
        wurzel = Path(ordner) if ordner else (self.arbeit / "tests")
        reihen = sorted(wurzel.glob("test_*.py")) if wurzel.is_dir() else []
        ende = time.time() + max(1.0, float(gesamtfrist))
        raus: List[Sandkastenlauf] = []
        for reihe in reihen:
            if haltepunkt:
                haltepunkt()
            rest = ende - time.time()
            if rest <= 0:
                abbruch = Sandkastenlauf(befehl=[str(reihe)], abgebrochen=True)
                abbruch.fehlerausgabe = "Gesamtfrist ueberschritten"
                raus.append(abbruch)
                break
            raus.append(self.laufen([sys.executable, str(reihe)],
                                    frist=min(rest, self.frist),
                                    arbeitsordner=reihe.parent.parent))
        return raus

    # ----------------------------------------------------------------- Innen
    def _kappen(self, text: str):
        text = str(text or "")
        if len(text) <= self.ausgabegrenze:
            return text, False
        haelfte = self.ausgabegrenze // 2
        return (text[:haelfte] + f"\n… [{len(text) - self.ausgabegrenze} Zeichen "
                f"ausgelassen] …\n" + text[-haelfte:]), True

    def __repr__(self) -> str:
        return (f"<Sandkasten {self.name} "
                f"{'offen' if self._offen else 'geschlossen'} "
                f"{len(self.laeufe)} Laeufe>")


def _text(wert) -> str:
    if wert is None:
        return ""
    if isinstance(wert, bytes):
        return wert.decode("utf-8", "replace")
    return str(wert)
