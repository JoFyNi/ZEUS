"""Welche Modelle es WIRKLICH gibt - auf welchem Server, mit welcher Rolle.

Der Anlass ist eine Messung vom 22.09., und sie ist unangenehm:

    HAUPT  qwen3.8:27b  (LLM_MODEL)   ok in 10,0 s
    HAUPT  qwen2.5:14b  (FAST_MODEL)  HTTP 404 - liegt dort nicht
    LOKAL  qwen2.5:3b                 ok in  5,5 s
    LOKAL  qwen2.5:14b                ok in 17,1 s

`FAST_MODEL` zeigte auf ein Modell, das auf dem aktiven Server nicht liegt.
Jeder Aufruf mit `schnell=True` lief ins Leere. Das ist derselbe Fehlerfall,
den `gespraech/arbeitsplan.py` seit dem 18.09. im Modulkopf beschreibt - er
war zurueck, weil die `.env` inzwischen auf ZWEI Server zeigt und
`llm._modelle_anpassen()` erst nach einem Hostwechsel nachsieht.

Solange das so ist, kann "kleines Modell vor grossem" nicht funktionieren:
Das kleine ist dort, wo gerechnet wird, gar nicht vorhanden.

Was dieses Modul tut
--------------------
Es fragt die Server, statt zu glauben. `/api/tags` liefert alles Noetige
selbst - Groesse in Parametern, Familie und die Faehigkeiten (`tools`,
`thinking`, `vision`, `embedding`). Nichts davon muss geraten werden.

Daraus entsteht ein Register, das drei Fragen beantwortet:

    Gibt es hier ein kleines Modell?      -> Routing
    Kann dieses Modell Werkzeuge?          -> Routing
    Wie lange hat es zuletzt gebraucht?    -> Routing

Die dritte Zahl wird GEMESSEN, nicht geschaetzt. Ein Router, der Latenz
beruecksichtigt, ohne sie zu kennen, entscheidet nach Vermutung.

Was es NICHT tut
----------------
Es aendert die Konfiguration nicht. `pruefen()` meldet, was nicht
zusammenpasst; was daraus folgt, entscheidet der Mensch oder der Router -
nicht dieses Modul. Eine Komponente, die still die `.env`-Absicht
ueberschreibt, erzeugt ein System, dessen Verhalten niemand mehr aus seiner
Konfiguration ablesen kann.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional

# Rollen. Grob, weil die Uebergaenge fliessend sind - und eine feine
# Staffelung taeuscht eine Genauigkeit vor, die es nicht gibt.
WINZIG = "winzig"          # bis 4 Mrd. Parameter: Klassifikation, Routing
KLEIN = "klein"            # bis 16 Mrd.: Extraktion, einfache Zusammenfassung
GROSS = "gross"            # darueber: Planung, Synthese, schwierige Analyse
EINBETTUNG = "einbettung"  # Vektoren, nie fuer Text
CODE = "code"              # auf Quelltext abgerichtet

ROLLEN = (WINZIG, KLEIN, GROSS, EINBETTUNG, CODE)

# Wie lange ein Server hoechstens brauchen darf, um seine Modellliste zu
# nennen. Wer laenger braucht, ist fuer eine Routingentscheidung ohnehin
# zu langsam.
FRIST = 4.0

# Gewicht des neuen Messwerts in der gleitenden Mittelung. 0.3 heisst: Ein
# einzelner Ausreisser verschiebt das Bild, kippt es aber nicht.
GLAETTUNG = 0.3


@dataclass
class Modell:
    """Ein Modell, wie es der Server meldet - plus was wir gemessen haben."""
    name: str
    host: str
    parameter: float = 0.0          # Milliarden
    groesse_gb: float = 0.0
    familie: str = ""
    faehigkeiten: List[str] = field(default_factory=list)
    kontext: int = 0
    rolle: str = KLEIN

    # Gemessen, nicht konfiguriert
    latenz: float = 0.0
    laeufe: int = 0
    fehler: int = 0
    zuletzt: float = 0.0

    @property
    def kann_werkzeuge(self) -> bool:
        return "tools" in self.faehigkeiten

    @property
    def denkt(self) -> bool:
        """Reasoning-Modelle brauchen laenger und mehr Ausgabelaenge.

        Das ist keine Feinheit: qwen3.8:27b hat bei `num_predict=8` eine
        LEERE Antwort geliefert, weil die acht Token schon fuers Denken
        draufgingen. Wer ein solches Modell knapp begrenzt, bekommt nichts.
        """
        return "thinking" in self.faehigkeiten

    @property
    def quote(self) -> float:
        return (self.laeufe - self.fehler) / self.laeufe if self.laeufe else 1.0

    def zeile(self) -> str:
        marken = []
        if self.kann_werkzeuge:
            marken.append("Werkzeuge")
        if self.denkt:
            marken.append("denkt")
        gemessen = (f"{self.latenz:.1f}s" if self.laeufe
                    else "noch nicht gemessen")
        return (f"{self.name:<22} {self.rolle:<11} {self.parameter:>6.1f} Mrd  "
                f"{gemessen:>18}  {', '.join(marken)}")


def rolle_bestimmen(name: str, parameter: float,
                    faehigkeiten: Optional[List[str]] = None,
                    familie: str = "") -> str:
    """Welche Rolle ein Modell hat. Aus den Angaben des Servers, nicht geraten."""
    faehigkeiten = faehigkeiten or []
    if "embedding" in faehigkeiten or familie in ("bert", "nomic-bert") \
            or "embed" in name.lower() or name.lower().startswith("bge"):
        return EINBETTUNG
    if "coder" in name.lower() or "code" in name.lower():
        return CODE
    if parameter <= 4.0:
        return WINZIG
    if parameter <= 16.0:
        return KLEIN
    return GROSS


def _parameter_lesen(text: str) -> float:
    """'27.3B' -> 27.3, '566.70M' -> 0.567. Unbekanntes ergibt 0."""
    treffer = re.match(r"\s*([\d.]+)\s*([BMK]?)", str(text or ""), re.IGNORECASE)
    if not treffer:
        return 0.0
    try:
        zahl = float(treffer.group(1))
    except ValueError:
        return 0.0
    einheit = (treffer.group(2) or "B").upper()
    return {"B": zahl, "M": zahl / 1000.0, "K": zahl / 1_000_000.0}.get(einheit, zahl)


class Modellregister:
    """Was auf welchem Server liegt - und wie schnell es war."""

    def __init__(self, cfg=None, melden=None) -> None:
        self.cfg = cfg
        self.melden = melden
        self.modelle: Dict[str, Modell] = {}      # "host|name" -> Modell
        self.hosts: Dict[str, bool] = {}          # host -> erreichbar
        self.erfasst_um: float = 0.0
        self._sperre = threading.RLock()

    # ------------------------------------------------------------- Erfassen
    def erfassen(self, hosts: Optional[List[str]] = None) -> "Modellregister":
        """Fragt die Server. Wirft nie - ein toter Server ist ein Befund."""
        hosts = [h for h in (hosts or self._hosts_aus_config()) if h]
        with self._sperre:
            self.modelle.clear()
            self.hosts.clear()
            for host in hosts:
                eintraege = self._tags(host)
                self.hosts[host] = eintraege is not None
                for roh in (eintraege or []):
                    modell = self._aus_eintrag(host, roh)
                    self.modelle[f"{host}|{modell.name}"] = modell
            self.erfasst_um = time.time()
        return self

    def _hosts_aus_config(self) -> List[str]:
        if self.cfg is None:
            return []
        raus = []
        for name in ("host", "host_alternative"):
            wert = getattr(self.cfg, name, "")
            if wert and wert not in raus:
                raus.append(wert)
        return raus

    def _tags(self, host: str) -> Optional[list]:
        try:
            with urllib.request.urlopen(f"{host.rstrip('/')}/api/tags",
                                        timeout=FRIST) as antwort:
                return json.load(antwort).get("models") or []
        except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
            self._sagen(f"{host} antwortet nicht: {type(exc).__name__}")
            return None

    @staticmethod
    def _aus_eintrag(host: str, roh: dict) -> Modell:
        einzel = roh.get("details") or {}
        parameter = _parameter_lesen(einzel.get("parameter_size"))
        name = roh.get("name") or roh.get("model") or "?"
        faehigkeiten = list(roh.get("capabilities") or [])
        return Modell(
            name=name, host=host, parameter=parameter,
            groesse_gb=round((roh.get("size") or 0) / 1e9, 2),
            familie=str(einzel.get("family") or ""),
            faehigkeiten=faehigkeiten,
            kontext=int(einzel.get("context_length") or 0),
            rolle=rolle_bestimmen(name, parameter, faehigkeiten,
                                  str(einzel.get("family") or "")))

    # --------------------------------------------------------------- Waehlen
    def waehlen(self, rolle: str, *, host: Optional[str] = None,
                braucht_werkzeuge: bool = False) -> Optional[Modell]:
        """Das beste Modell dieser Rolle - auf DIESEM Server.

        `host` ist der entscheidende Parameter. Ein Register, das ein Modell
        auf Server B meldet, waehrend gerechnet wird auf Server A, erzeugt
        genau den 404, der diesen Modulkopf fuellt.
        """
        with self._sperre:
            kandidaten = [m for m in self.modelle.values()
                          if m.rolle == rolle
                          and (host is None or m.host == host)
                          and (not braucht_werkzeuge or m.kann_werkzeuge)]
        if not kandidaten:
            return None
        # Bestes zuerst: wenige Fehler, dann schnell, dann klein. Ein Modell
        # ohne Messwerte gilt als mittelschnell - sonst gewaenne das
        # ungetestete immer gegen das bewaehrte.
        kandidaten.sort(key=lambda m: (round(1 - m.quote, 2),
                                       m.latenz if m.laeufe else 5.0,
                                       m.parameter))
        return kandidaten[0]

    @staticmethod
    def _passt(gesucht: str, vorhanden: str) -> bool:
        """Meint 'bge-m3' dasselbe wie 'bge-m3:latest'? Ja. Und 'qwen2.5'
        dasselbe wie 'qwen2.5:14b'? Auch ja - aber 'qwen2.5:3b' NICHT.

        Die Regel steht so schon in `llm._modell_vorhanden`, mit einer
        Begruendung, die dort nachzulesen ist. Sie wird von dort geholt und
        hier NICHT nachgebaut: Zwei Fassungen derselben Regel laufen
        auseinander, und dann meldet die eine ein Modell als vorhanden, das
        die andere nicht findet.

        Die erste Fassung dieses Registers hatte eine eigene, strengere
        Regel - und meldete prompt, `EMBEDDING_MODEL='bge-m3'` liege
        nirgends, obwohl `bge-m3:latest` auf beiden Servern lag. Ein
        Warnsystem, das falsch warnt, liest nach dem zweiten Mal niemand
        mehr.
        """
        try:
            from ..llm import OllamaClient
            return OllamaClient._modell_vorhanden(gesucht, [vorhanden])
        except Exception:
            # Ohne den Client wenigstens die Marke beachten.
            if gesucht == vorhanden:
                return True
            if ":" in gesucht:
                basis, marke = gesucht.split(":", 1)
                return marke == "latest" and vorhanden == basis
            return vorhanden.split(":")[0] == gesucht

    def kennt(self, name: str, host: Optional[str] = None) -> bool:
        return self.modell(name, host) is not None

    def modell(self, name: str, host: Optional[str] = None) -> Optional[Modell]:
        with self._sperre:
            kandidaten = [m for m in self.modelle.values()
                          if (host is None or m.host == host)]
        # Der exakte Name gewinnt vor der Marken-Auslegung: Sonst waehlte
        # 'qwen2.5' unter mehreren Groessen die erstbeste statt der genannten.
        for m in kandidaten:
            if m.name == name:
                return m
        for m in kandidaten:
            if self._passt(name, m.name):
                return m
        return None

    # ---------------------------------------------------------------- Messen
    def vermerken(self, name: str, dauer: float, ok: bool = True,
                  host: Optional[str] = None) -> None:
        """Was ein Aufruf gekostet hat. Gleitend gemittelt, siehe GLAETTUNG."""
        with self._sperre:
            modell = self.modell(name, host)
            if modell is None:
                return
            modell.laeufe += 1
            modell.zuletzt = time.time()
            if not ok:
                modell.fehler += 1
                return
            modell.latenz = (dauer if modell.latenz <= 0 else
                             (1 - GLAETTUNG) * modell.latenz + GLAETTUNG * dauer)

    # --------------------------------------------------------------- Pruefen
    def pruefen(self) -> List[str]:
        """Was in der Konfiguration nicht zur Wirklichkeit passt.

        Gibt Klartext zurueck, aendert aber NICHTS - siehe Modulkopf. Die
        Meldungen gehen in `/kern` und ins Protokoll, damit der Fehler dort
        auffaellt, wo man ihn beheben kann: in der `.env`.
        """
        if self.cfg is None:
            return []
        befunde: List[str] = []
        aktiv = getattr(self.cfg, "host", "")

        for host, erreichbar in self.hosts.items():
            if not erreichbar:
                befunde.append(f"{host} antwortet nicht.")

        if aktiv and self.hosts.get(aktiv):
            for schalter, zweck in (("llm_model", "LLM_MODEL"),
                                    ("fast_model", "FAST_MODEL"),
                                    ("embedding_model", "EMBEDDING_MODEL")):
                name = getattr(self.cfg, schalter, "")
                if not name:
                    continue
                if not self.kennt(name, aktiv):
                    anderswo = [m.host for m in self.modelle.values()
                                if m.name == name]
                    wo = (f" Es liegt auf {', '.join(anderswo)}."
                          if anderswo else " Nirgends gefunden.")
                    befunde.append(
                        f"{zweck}='{name}' liegt NICHT auf dem aktiven Server "
                        f"{aktiv}.{wo} Jeder Aufruf darauf endet im 404.")

            if not self.waehlen(WINZIG, host=aktiv) and not self.waehlen(KLEIN, host=aktiv):
                befunde.append(
                    f"Auf {aktiv} liegt kein kleines Modell. Einfache Schritte "
                    f"muessen deshalb auf dem grossen laufen - das kostet Zeit, "
                    f"ohne die Qualitaet zu verbessern.")
        return befunde

    # -------------------------------------------------------------- Auskunft
    def uebersicht(self) -> str:
        with self._sperre:
            if not self.modelle:
                return ("Kein Modell erfasst. `erfassen()` wurde nicht gerufen "
                        "oder kein Server hat geantwortet.")
            zeilen = []
            for host in sorted(self.hosts):
                marke = "erreichbar" if self.hosts[host] else "antwortet nicht"
                zeilen.append(f"{host}  ({marke})")
                eigene = sorted((m for m in self.modelle.values() if m.host == host),
                                key=lambda m: (ROLLEN.index(m.rolle)
                                               if m.rolle in ROLLEN else 9,
                                               m.parameter))
                if not eigene:
                    zeilen.append("    (keine Modelle)")
                for modell in eigene:
                    zeilen.append("    " + modell.zeile())
        befunde = self.pruefen()
        if befunde:
            zeilen.append("")
            zeilen.append("Passt nicht zusammen:")
            zeilen += [f"  ! {b}" for b in befunde]
        return "\n".join(zeilen)

    def als_dict(self) -> dict:
        with self._sperre:
            return {
                "hosts": dict(self.hosts),
                "erfasst_um": self.erfasst_um,
                "modelle": [
                    {"name": m.name, "host": m.host, "rolle": m.rolle,
                     "parameter": m.parameter, "werkzeuge": m.kann_werkzeuge,
                     "denkt": m.denkt, "latenz": round(m.latenz, 2),
                     "laeufe": m.laeufe, "fehler": m.fehler}
                    for m in self.modelle.values()],
            }

    def _sagen(self, text: str) -> None:
        if callable(self.melden):
            try:
                self.melden(text)
            except Exception:
                pass

    def __repr__(self) -> str:
        return (f"<Modellregister {len(self.modelle)} Modelle auf "
                f"{len(self.hosts)} Host(s)>")
