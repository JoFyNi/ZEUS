"""Das Betriebsprotokoll - was wirklich passiert ist, nicht was behauptet wurde.

Drei Fragen, auf die es bisher keine belastbare Antwort gab:

1. **Was hat er geändert?** Das Journal kennt Schreibvorgänge, aber nur die.
   Ein fehlgeschlagenes Werkzeug, ein abgelehnter Name, ein übergangener
   Schritt - nichts davon stand irgendwo zusammen.
2. **Was ist schiefgegangen?** Fehler landeten in `.jarvis/fehler.log`, in der
   Antwort, in der Konsole - je nachdem, wo sie auftraten.
3. **Was geht ins Netz?** Darauf gab es GAR KEINE Antwort. Zeus ist lokal, aber
   "lokal" ist eine Behauptung, solange niemand nachsehen kann. Jede
   HTTP-Anfrage läuft ab jetzt durch `ProtokollSession` und steht mit Ziel,
   Größe und Dauer im Protokoll.

Bewusst ohne Abhängigkeit und ohne Hintergrunddienst: ein Ringpuffer im
Speicher, eine JSONL-Datei auf der Platte, und Zuhörer, die neue Einträge
sofort bekommen. Das Protokoll darf niemals einen Auftrag zum Scheitern
bringen - jeder Fehler beim Schreiben wird geschluckt und gezählt, nicht
geworfen.

Die Ebenen entsprechen den Fragen oben:

    info      gewöhnlicher Betriebsvorgang
    erfolg    ein Werkzeug hat etwas geschafft
    warnung   etwas ist auffällig, aber nicht gescheitert
    fehler    etwas ist gescheitert
    aenderung eine Datei wurde angelegt, geändert, verschoben oder gelöscht
    netz_raus eine Anfrage hat den Rechner verlassen
    netz_rein eine Antwort ist zurückgekommen
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Deque, Dict, List, Optional

EBENEN = ("info", "erfolg", "warnung", "fehler", "aenderung", "netz_raus", "netz_rein")

# Wie viele Einträge im Speicher gehalten werden. Die Datei bekommt alles.
RINGGROESSE = 3000

DATEINAME = "protokoll.jsonl"


@dataclass
class Eintrag:
    zeit: float = field(default_factory=time.time)
    ebene: str = "info"
    text: str = ""
    quelle: str = ""                       # Werkzeug, Modul oder Befehl
    daten: Dict[str, Any] = field(default_factory=dict)
    nummer: int = 0

    def als_dict(self) -> dict:
        return asdict(self)

    def zeile(self) -> str:
        """Eine Zeile fürs Terminal - kompakt, aber vollständig."""
        uhr = time.strftime("%H:%M:%S", time.localtime(self.zeit))
        zeichen = {"info": "·", "erfolg": "✔", "warnung": "⚠", "fehler": "✖",
                   "aenderung": "✎", "netz_raus": "→", "netz_rein": "←"}
        rest = ""
        if self.daten:
            try:
                rest = " " + json.dumps(self.daten, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                rest = f" {self.daten}"
        quelle = f"[{self.quelle}] " if self.quelle else ""
        return f"{uhr} {zeichen.get(self.ebene, '·')} {quelle}{self.text}{rest}"


class Protokoll:
    """Ringpuffer im Speicher, JSONL auf der Platte, Zuhörer live."""

    def __init__(self, cfg=None, pfad: Optional[Path] = None):
        self.cfg = cfg
        self._pfad = pfad
        self._eintraege: Deque[Eintrag] = deque(maxlen=RINGGROESSE)
        self._schloss = threading.Lock()
        self._zuhoerer: List[Callable[[Eintrag], None]] = []
        self._nummer = 0
        # Wie oft das Schreiben misslungen ist. Wird gemeldet, nicht verschwiegen.
        self.schreibfehler = 0

    # ------------------------------------------------------------- Schreiben
    @property
    def pfad(self) -> Optional[Path]:
        if self._pfad is not None:
            return self._pfad
        if self.cfg is None:
            return None
        try:
            return self.cfg.work_dir / DATEINAME
        except Exception:                                # noqa: BLE001
            return None

    def melden(self, ebene: str, text: str, quelle: str = "", **daten) -> Eintrag:
        """Einen Eintrag ablegen. Gibt ihn zurück, wirft nie."""
        if ebene not in EBENEN:
            ebene = "info"
        with self._schloss:
            self._nummer += 1
            eintrag = Eintrag(ebene=ebene, text=str(text), quelle=str(quelle),
                              daten=dict(daten), nummer=self._nummer)
            self._eintraege.append(eintrag)
            zuhoerer = list(self._zuhoerer)
        self._auf_platte(eintrag)
        for hoerer in zuhoerer:
            try:
                hoerer(eintrag)
            except Exception:                            # noqa: BLE001
                pass                                     # ein tauber Zuhörer
        return eintrag

    def _auf_platte(self, eintrag: Eintrag) -> None:
        ziel = self.pfad
        if ziel is None:
            return
        try:
            ziel.parent.mkdir(parents=True, exist_ok=True)
            with ziel.open("a", encoding="utf-8") as datei:
                datei.write(json.dumps(eintrag.als_dict(), ensure_ascii=False,
                                       default=str) + "\n")
        except OSError:
            self.schreibfehler += 1

    # -------------------------------------------------------------- Abrufen
    def lesen(self, anzahl: int = 300, ebene: str = "",
              ab_nummer: int = 0) -> List[dict]:
        """Die letzten Einträge, optional nach Ebene gefiltert.

        `ab_nummer` liefert nur Neueres - so holt die Oberfläche beim
        Nachladen nicht jedes Mal alles.
        """
        with self._schloss:
            alle = list(self._eintraege)
        if ebene and ebene != "alle":
            alle = [e for e in alle if e.ebene == ebene]
        if ab_nummer:
            alle = [e for e in alle if e.nummer > ab_nummer]
        return [e.als_dict() for e in alle[-anzahl:]]

    def zahlen(self) -> Dict[str, int]:
        with self._schloss:
            alle = list(self._eintraege)
        gezaehlt = {e: 0 for e in EBENEN}
        for eintrag in alle:
            gezaehlt[eintrag.ebene] = gezaehlt.get(eintrag.ebene, 0) + 1
        gezaehlt["gesamt"] = len(alle)
        gezaehlt["schreibfehler"] = self.schreibfehler
        return gezaehlt

    def leeren(self) -> int:
        """Nur die Anzeige leeren. Die Datei bleibt - sie ist der Nachweis."""
        with self._schloss:
            anzahl = len(self._eintraege)
            self._eintraege.clear()
        return anzahl

    # -------------------------------------------------------------- Zuhörer
    def zuhoeren(self, hoerer: Callable[[Eintrag], None]) -> Callable[[], None]:
        """Meldet einen Zuhörer an. Der Rückgabewert meldet ihn wieder ab."""
        with self._schloss:
            self._zuhoerer.append(hoerer)

        def abmelden() -> None:
            with self._schloss:
                if hoerer in self._zuhoerer:
                    self._zuhoerer.remove(hoerer)
        return abmelden

    # ------------------------------------------------- bequeme Kurzformen
    def info(self, text, quelle="", **d): return self.melden("info", text, quelle, **d)
    def erfolg(self, text, quelle="", **d): return self.melden("erfolg", text, quelle, **d)
    def warnung(self, text, quelle="", **d): return self.melden("warnung", text, quelle, **d)
    def fehler(self, text, quelle="", **d): return self.melden("fehler", text, quelle, **d)
    def aenderung(self, text, quelle="", **d): return self.melden("aenderung", text, quelle, **d)


# Ein Protokoll für den ganzen Prozess. Ein zweites hätte keine Chance, das
# Gesamtbild zu zeigen - und genau darum geht es hier.
_protokoll: Optional[Protokoll] = None


def protokoll(cfg=None) -> Protokoll:
    global _protokoll
    if _protokoll is None:
        from .config import settings
        _protokoll = Protokoll(cfg or settings)
    elif cfg is not None and _protokoll.cfg is None:
        _protokoll.cfg = cfg
    return _protokoll


# ===================================================== Netzverkehr mitschreiben
def _kurz(wert: Any, grenze: int = 220) -> str:
    text = str(wert)
    return text if len(text) <= grenze else text[:grenze] + "…"


class ProtokollSession:
    """Eine requests.Session, die jede Anfrage ins Protokoll schreibt.

    Der Grund ist Nachweisbarkeit, nicht Misstrauen: Zeus ist lokal, aber
    "lokal" ist eine Behauptung, solange man nicht nachsehen kann. Hier steht
    für jede Anfrage: wohin, wie lang, wie viele Bytes hin und zurück.

    Bewusst als Hülle statt als Unterklasse: `requests` ist optional (die
    Testreihen laufen mit einem Stub), und eine Unterklasse würde beim Import
    scheitern, wenn das Paket fehlt.
    """

    def __init__(self, session=None, log: Optional[Protokoll] = None,
                 quelle: str = "netz"):
        if session is None:
            import requests
            session = requests.Session()
        self._session = session
        self._log = log
        self.quelle = quelle

    @property
    def log(self) -> Protokoll:
        return self._log or protokoll()

    def __getattr__(self, name):                         # alles Übrige durchreichen
        return getattr(self._session, name)

    # -------------------------------------------------------- die Methoden
    def get(self, url, **kw):
        return self._mit_protokoll("GET", url, **kw)

    def post(self, url, **kw):
        return self._mit_protokoll("POST", url, **kw)

    def request(self, methode, url, **kw):
        return self._mit_protokoll(methode, url, **kw)

    def _mit_protokoll(self, methode: str, url: str, **kw):
        beginn = time.time()
        hinaus = 0
        for feld in ("json", "data"):
            if kw.get(feld) is not None:
                try:
                    hinaus = len(json.dumps(kw[feld], default=str)) \
                        if feld == "json" else len(kw[feld])
                except Exception:                        # noqa: BLE001
                    hinaus = -1
        modell = ""
        if isinstance(kw.get("json"), dict):
            modell = str(kw["json"].get("model") or "")

        self.log.melden("netz_raus", f"{methode} {url}", self.quelle,
                        bytes=hinaus, modell=modell,
                        strom=bool(kw.get("stream")))
        try:
            antwort = getattr(self._session, methode.lower())(url, **kw)
        except Exception as exc:                         # noqa: BLE001
            self.log.melden("fehler", f"{methode} {url} gescheitert: {_kurz(exc)}",
                            self.quelle, dauer=round(time.time() - beginn, 2))
            raise
        dauer = round(time.time() - beginn, 2)
        groesse = -1
        if not kw.get("stream"):
            try:
                groesse = len(antwort.content or b"")
            except Exception:                            # noqa: BLE001
                groesse = -1
        self.log.melden("netz_rein",
                        f"{getattr(antwort, 'status_code', '?')} von {url}",
                        self.quelle, bytes=groesse, dauer=dauer,
                        strom=bool(kw.get("stream")))
        return antwort


def netzsession(quelle: str = "netz"):
    """Eine protokollierende Session - oder eine gewöhnliche, wenn etwas fehlt."""
    try:
        return ProtokollSession(quelle=quelle)
    except Exception:                                    # noqa: BLE001
        import requests
        return requests.Session()
