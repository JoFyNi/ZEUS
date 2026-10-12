"""Episodengedaechtnis: was in diesem und frueheren Gespraechen WOERTLICH gesagt wurde.

Die Luecke (Audit vom 12.10.2026)
---------------------------------
Zeus hatte zwei Gedaechtnisse, und zwischen ihnen ein Loch:

* `agent.history` - ein Ringpuffer ueber HISTORY_TURNS Zuege (Vorgabe 6).
  Was davor gesagt wurde, ist fuer das Modell weg.
* `sitzung.py` - verdichtet den Verlauf alle SESSION_SUMMARY_EVERY Zuege
  (Vorgabe 10) per Modellaufruf zu Fakten. Was das Modell dabei nicht
  herauszieht, ist ebenfalls weg; und bis zur Verdichtung steht es nirgends.

Zwischen Zug 7 und Zug 10 einer Sitzung war eine Vorgabe aus Zug 1 also
nirgends abrufbar - weder im Verlauf noch im Sitzungsgedaechtnis. Genau dann
fragt man "mach es so wie am Anfang gesagt".

Was dieses Modul anders macht
-----------------------------
* **Woertlich, nicht verdichtet.** Jeder Zug wird so abgelegt, wie er war
  (gekuerzt auf TEXT_GRENZE). Verdichten bleibt Aufgabe von sitzung.py; hier
  geht es darum, dass nichts zwischen den Stuehlen verschwindet.
* **Herkunft ist Teil des Datensatzes.** Was der Nutzer sagte, was das
  Modell antwortete und welche Werkzeuge mit welchem Ausgang liefen, steht
  getrennt da. Eine Modellantwort wird nie zur Tatsache befoerdert - als
  Vorgabe oder Aussage gilt nur, was der NUTZER gesagt hat.
* **Abruf ohne Modell und ohne Netz.** Stichwortabgleich mit IDF-Gewichtung,
  Bonus fuer Vorgaben und benannte Bezuege, Abschlag fuer Alter. Kein
  Einbettungsaufruf - das Gedaechtnis funktioniert auch, wenn Ollama steht.
  Der Preis: Synonyme ohne gemeinsames Wort findet es nicht. Dafuer gibt es
  den Vektorindex ueber die verdichteten Sitzungsnotizen.
* **Nur Hinzufuegen.** Eine Ersetzung ("ab jetzt MySQL statt PostgreSQL")
  ueberschreibt nichts; sie wird als eigener Datensatz angehaengt. Die alte
  Aussage bleibt nachlesbar und wird beim Abruf als ersetzt markiert.

Ablage: `<Vault>/.jarvis/episoden.jsonl`, eine JSON-Zeile je Datensatz. Der
Ordner beginnt mit einem Punkt - Obsidian und der Index sehen ihn nicht.
Offensichtliche Geheimnisse (Passwort: ..., API-Schluessel) werden VOR dem
Schreiben geschwaerzt; das ist ein Muster, keine Garantie. Wer gar nichts
abgelegt haben will: EPISODEN_GEDAECHTNIS=false.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .aussagen import saetze, zerlegen
from .config import settings
from .index import tokens
from .sprache import ist_cjk_wort

VERSION = 1
DATEINAME = "episoden.jsonl"

#: So viel Text je Frage/Antwort wird abgelegt. Laengeres steht ohnehin in
#: der Vault oder im Werkzeugergebnis; fuer den Abruf genuegt der Anfang.
TEXT_GRENZE = 4000

#: Unter diesem Anteil gedeckter Fragewoerter gilt ein Zug als unbeteiligt.
#: Ohne Schwelle kaeme zu JEDER Frage irgendein alter Zug mit, und
#: Irrelevantes verdraengte das Relevante (Pruefung H).
MINDEST_RELEVANZ = 0.3

# Woerter, die in fast jeder Frage stehen und nichts darueber sagen, worum es
# geht. Ohne diese Liste gaelte "Was steht an?" schon als belegt, sobald
# irgendeine Notiz "steht" enthaelt - und ein alter Zug mit "nochmal" kaeme
# zu jeder Frage mit "nochmal" wieder hoch. Gebraucht von `abrufen` und von
# der Beleglage (gespraech/kontextwahl.py).
ALLERWELTSWOERTER = {
    "steht", "stehen", "gibt", "neues", "machen", "tun", "heute", "aktuell", "offen",
    "offene", "punkte", "alles", "etwas", "dinge", "sache", "sachen", "frage", "fragen",
    "wissen", "weisst", "weiss", "sagen", "erzaehl", "erzaehle", "zeige", "zusammen",
    "danke", "notiz", "notizen", "vault", "meine", "meinen", "meiner", "meinem", "deine",
    "nochmal", "eigentlich", "genau", "denn", "dazu", "darueber", "ueber", "welche",
    "welcher", "welches", "wann", "warum", "wieso", "weshalb", "woher", "wohin", "haben",
    "hatte", "hatten", "sind", "sein", "wird", "werden", "koennen", "soll", "sollen",
    "muss", "muessen", "nennen", "nenne", "erklaere", "erklaer", "beschreibe",
    "zusammenfassung", "fasse", "kurz", "bisschen", "viele", "viel", "aus", "drin",
    "habe", "hab", "hat", "hast", "mal", "bitte", "about", "tell", "know", "what",
    "does", "have",
    # Umstandswoerter, die in jeder zweiten Frage stehen
    "gerade", "jetzt", "immer", "schon", "wieder", "eben", "gleich", "sehr", "ganz",
    "wirklich", "vielleicht", "einfach", "bisher", "dann", "also", "noch", "doch",
    "morgen", "gestern", "vorhin", "damals", "kannst", "koenntest", "wuerdest",
}

ART_NOTIZ = "notiz"
ART_DATEI = "datei"
ART_CODE = "code"
ART_BEGRIFF = "begriff"

# ------------------------------------------------------------------ Muster
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_DATEINAME = re.compile(
    r"(?<![\w/.-])([\w./-]{1,80}\.(?:py|md|js|ts|tsx|jsx|json|ya?ml|toml|txt|html|css|"
    r"java|cs|cpp|c|h|go|rs|sql|sh|bat|ps1|ini|cfg|csv|ipynb))\b")
_CODEBEZUG = re.compile(r"`([A-Za-z_][\w.]{1,60}(?:\(\))?)`|\b([A-Za-z_]\w{2,40}\(\))")
_ANFUEHRUNG = re.compile(r"[„\"“]([^„\"“”\n]{3,60})[“”\"]")

# Werkzeugparameter, die eine Notiz benennen. Fertigkeiten, Netz und Code
# haben auch ein 'name'/'datei', meinen damit aber keine Notiz.
_NOTIZ_PARAMETER = ("name", "titel", "quellen", "von", "nach", "neuer_name", "ziel_titel")
_KEINE_NOTIZ_WERKZEUGE = ("fertigkeit", "verhalten", "web_", "code", "quellcode")

# Vorgaben: was der Nutzer als geltend festlegt. Starke Marker reichen allein;
# ein blosses Modalverb ("muss", "soll") erst in einem Satz mit Inhalt -
# "Ich muss gleich los" ist keine Vorgabe.
_VORGABE_STARK = re.compile(
    r"\b(immer|stets|nie|niemals|grunds(ae|ä)tzlich|ausschlie(ss|ß)lich|nur\s+noch|"
    r"ab\s+sofort|ab\s+jetzt|von\s+nun\s+an|k(ue|ü)nftig|zuk(ue|ü)nftig|"
    r"wichtig|anforderung\w*|vorgabe\w*|bedingung\w*|voraussetzung\w*|"
    r"einschr(ae|ä)nkung\w*|merk\s*e?\s+dir|beachte|denk\s+dran|vergiss\s+nicht|"
    r"achte\s+darauf|h(oe|ö)chstens|maximal|mindestens|nicht\s+mehr\s+als|"
    r"always|never|requirement|constraint|make\s+sure|at\s+most|at\s+least|"
    r"remember)\b", re.IGNORECASE)
_VORGABE_MODAL = re.compile(
    r"\b(muss|m(ue|ü)ssen|soll|sollen|sollte|sollten|darf\s+nicht|d(ue|ü)rfen\s+nicht|"
    r"must|should|shall)\b", re.IGNORECASE)

# Geheimnisse, die nicht auf die Platte gehoeren. Bewusst grob.
_GEHEIM_WERT = re.compile(
    r"(?i)\b(passwort|password|passwd|kennwort|pin|api[-_ ]?key|apikey|token|secret|"
    r"geheimnis|zugangsdaten|private[-_ ]?key)\b(\s*(?:ist|lautet|is|=|:)\s*)(\S+)")
_GEHEIM_FORM = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,})\b")
_GEHEIM_BLOCK = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL)


def _tragend(wort: str) -> bool:
    """Traegt das Wort Inhalt? Vier Zeichen, eine Ziffer oder CJK."""
    return len(wort) >= 4 or any(z.isdigit() for z in wort) or ist_cjk_wort(wort)


def schwaerzen(text: str) -> str:
    """Offensichtliche Geheimnisse ersetzen, bevor etwas auf die Platte geht."""
    text = _GEHEIM_BLOCK.sub("[entfernt]", text or "")
    text = _GEHEIM_FORM.sub("[entfernt]", text)
    return _GEHEIM_WERT.sub(lambda m: f"{m.group(1)}{m.group(2)}[entfernt]", text)


def vorgaben_finden(text: str) -> List[str]:
    """Saetze des Nutzers, die etwas als geltend festlegen."""
    gefunden: List[str] = []
    for satz in saetze(text):
        satz = " ".join(satz.split())
        if satz.endswith("?") or not (12 <= len(satz) <= 400):
            continue
        stark = _VORGABE_STARK.search(satz)
        modal = _VORGABE_MODAL.search(satz)
        if not stark and not (modal and len(satz.split()) >= 5):
            continue
        inhalt = [t for t in tokens(satz) if not _VORGABE_STARK.fullmatch(t)
                  and not _VORGABE_MODAL.fullmatch(t)]
        if len(inhalt) >= 2:
            gefunden.append(satz)
    return gefunden


def aussagen_finden(text: str) -> List[str]:
    """Aussagesaetze des Nutzers mit erkennbarer Struktur (siehe aussagen.py).

    Befehle zaehlen hier nicht - "erstelle eine Notiz" ist keine Aussage
    ueber die Welt. Als Vorgabe kann derselbe Satz trotzdem gelten.
    """
    gefunden: List[str] = []
    for satz in saetze(text):
        satz = " ".join(satz.split())
        aussage = zerlegen(satz)
        if aussage is not None and aussage.beziehung != "befehl":
            gefunden.append(satz)
    return gefunden


# ------------------------------------------------------------- Datenmodell
@dataclass
class Bezug:
    """Etwas, worauf man sich spaeter mit "die Notiz", "die Datei" bezieht."""

    name: str
    art: str            # notiz | datei | code | begriff
    quelle: str         # frage | antwort | werkzeug


@dataclass
class Zug:
    """Ein Gespraechszug: Frage des Nutzers, Antwort, was dabei lief."""

    nr: int
    zeit: str                      # "2026-10-12T10:15:03"
    sitzung: str                   # Kennung des Programmlaufs
    frage: str
    antwort: str = ""
    weg: str = ""
    werkzeuge: List[str] = field(default_factory=list)      # "notiz_lesen:ok"
    bezuege: List[Bezug] = field(default_factory=list)
    vorgaben: List[str] = field(default_factory=list)
    aussagen: List[str] = field(default_factory=list)
    _woerter: Optional[Set[str]] = field(default=None, repr=False, compare=False)

    @property
    def woerter(self) -> Set[str]:
        if self._woerter is None:
            self._woerter = set(tokens(" ".join(
                [self.frage, self.antwort[:1500]] + [b.name for b in self.bezuege])))
        return self._woerter

    @property
    def uhrzeit(self) -> str:
        return self.zeit[11:16] if len(self.zeit) >= 16 else self.zeit

    @property
    def tag(self) -> str:
        return self.zeit[:10]

    def als_dict(self) -> dict:
        return {"v": VERSION, "typ": "zug", "nr": self.nr, "zeit": self.zeit,
                "sitzung": self.sitzung, "frage": self.frage, "antwort": self.antwort,
                "weg": self.weg, "werkzeuge": list(self.werkzeuge),
                "bezuege": [[b.name, b.art, b.quelle] for b in self.bezuege],
                "vorgaben": list(self.vorgaben), "aussagen": list(self.aussagen)}

    @classmethod
    def aus_dict(cls, daten: dict) -> "Zug":
        """Strenger als noetig mit den Typen: Eine Zeile, die von Hand oder von
        einer kaputten Version geschrieben wurde, soll hier scheitern - und
        nicht spaeter mitten im Abruf."""
        nr = int(daten["nr"])
        frage = str(daten["frage"])
        bezuege = []
        for eintrag in daten.get("bezuege") or []:
            name, art, quelle = (list(eintrag) + ["", "", ""])[:3]
            if str(name).strip():
                bezuege.append(Bezug(str(name), str(art or ART_NOTIZ), str(quelle or "")))
        return cls(nr=nr, zeit=str(daten.get("zeit") or ""),
                   sitzung=str(daten.get("sitzung") or ""), frage=frage,
                   antwort=str(daten.get("antwort") or ""),
                   weg=str(daten.get("weg") or ""),
                   werkzeuge=[str(w) for w in daten.get("werkzeuge") or []],
                   bezuege=bezuege,
                   vorgaben=[str(v) for v in daten.get("vorgaben") or []],
                   aussagen=[str(a) for a in daten.get("aussagen") or []])


@dataclass
class Treffer:
    zug: Zug
    wert: float
    gedeckt: List[str]             # welche Fragewoerter der Zug traegt


# ---------------------------------------------------------------- Bezuege
def bezuege_aus_text(text: str, quelle: str) -> List[Bezug]:
    """Notizen, Dateien, Code und Begriffe, die im Text ausdruecklich stehen."""
    gefunden: List[Bezug] = []

    def an(name: str, art: str) -> None:
        name = (name or "").strip().strip(".,;:")
        if name and len(name) <= 120 and not any(
                b.name == name and b.art == art for b in gefunden):
            gefunden.append(Bezug(name, art, quelle))

    for treffer in _WIKILINK.finditer(text or ""):
        an(treffer.group(1), ART_NOTIZ)
    for treffer in _DATEINAME.finditer(text or ""):
        an(treffer.group(1), ART_DATEI)
    for treffer in _CODEBEZUG.finditer(text or ""):
        name = treffer.group(1) or treffer.group(2) or ""
        if _DATEINAME.fullmatch(name):
            continue
        an(name, ART_CODE)
    for treffer in _ANFUEHRUNG.finditer(text or ""):
        an(treffer.group(1), ART_BEGRIFF)
    return gefunden


def bezuege_aus_aufrufen(calls: Iterable) -> List[Bezug]:
    """Was ein Werkzeug tatsaechlich angefasst hat - der verlaesslichste Bezug."""
    gefunden: List[Bezug] = []
    for call in calls or []:
        name = str(getattr(call, "werkzeug", "") or "")
        if not name or any(teil in name for teil in _KEINE_NOTIZ_WERKZEUGE):
            if "code" in name or "quellcode" in name:
                datei = (getattr(call, "parameter", {}) or {}).get("datei")
                if datei:
                    gefunden.append(Bezug(str(datei), ART_DATEI, "werkzeug"))
            continue
        ergebnis = getattr(call, "ergebnis", None)
        daten = getattr(ergebnis, "daten", {}) or {}
        pfad = daten.get("pfad") or daten.get("nachher")
        if pfad and getattr(ergebnis, "ok", False):
            gefunden.append(Bezug(Path(str(pfad)).stem, ART_NOTIZ, "werkzeug"))
            continue
        parameter = getattr(call, "parameter", {}) or {}
        for schluessel in _NOTIZ_PARAMETER:
            wert = parameter.get(schluessel)
            werte = wert if isinstance(wert, (list, tuple)) else [wert]
            for einzel in werte:
                if isinstance(einzel, str) and einzel.strip():
                    gefunden.append(Bezug(einzel.strip(), ART_NOTIZ, "werkzeug"))
    return gefunden


# ------------------------------------------------------------------ Speicher
class Episodengedaechtnis:
    """Legt Gespraechszuege ab und findet die passenden wieder.

    Darf nie eine Frage verhindern: Jeder Fehler beim Lesen oder Schreiben
    landet in `letzter_fehler`, der Speicher arbeitet dann im Arbeitsspeicher
    weiter. Wer abruft, sieht an `gestoert`, ob er sich darauf verlassen kann.
    """

    def __init__(self, cfg=settings, pfad: Optional[Path] = None,
                 sitzung: Optional[str] = None) -> None:
        self.cfg = cfg
        self._pfad = pfad
        # Mit Mikrosekunden: Zwei Programmlaeufe in derselben Sekunde (Tests,
        # Neustart nach Absturz) sind trotzdem zwei Sitzungen.
        self.sitzung = sitzung or dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.%f")
        self._sperre = threading.RLock()
        self._zuege: Optional[List[Zug]] = None
        # (alte Zugnummer, Satz) -> Zugnummer, die ihn ersetzt hat
        self._ersetzt: Dict[Tuple[int, str], int] = {}
        self.letzter_fehler = ""
        self.defekte = 0
        self.lesbar = True             # False, wenn die Datei nicht zu lesen war
        self.geschrieben = True        # False, sobald ein Schreiben misslang
        self._zeilen = 0               # Zeilen in der Datei - fuers Verdichten

    # ------------------------------------------------------------- Zustand
    @property
    def aktiv(self) -> bool:
        return bool(getattr(self.cfg, "episoden_gedaechtnis", True))

    @property
    def pfad(self) -> Path:
        return self._pfad or (Path(self.cfg.work_dir) / DATEINAME)

    @property
    def gestoert(self) -> bool:
        """Ist auf den Abruf kein Verlass? Lesen oder Schreiben misslang.

        Einzelne defekte Zeilen zaehlen NICHT: Dann fehlt ein Zug, nicht das
        Gedaechtnis. Sie stehen in `defekte` und in den Kennzahlen.
        """
        return not (self.lesbar and self.geschrieben)

    @property
    def zuege(self) -> List[Zug]:
        with self._sperre:
            if self._zuege is None:
                self._zuege = self._laden()
            return self._zuege

    def sitzungszuege(self) -> List[Zug]:
        return [z for z in self.zuege if z.sitzung == self.sitzung]

    def ersetzt_durch(self, zug: Zug, satz: str) -> Optional[int]:
        return self._ersetzt.get((zug.nr, satz))

    # --------------------------------------------------------------- Laden
    def _laden(self) -> List[Zug]:
        if not self.aktiv:
            return []
        try:
            if not self.pfad.exists():
                return []
            zeilen = self.pfad.read_text(encoding="utf-8").splitlines()
            self._zeilen = len(zeilen)
        except (OSError, UnicodeDecodeError) as exc:
            self.lesbar = False
            self.letzter_fehler = f"Episodengedaechtnis nicht lesbar: {exc}"
            return []

        zuege: List[Zug] = []
        for zeile in zeilen:
            if not zeile.strip():
                continue
            try:
                daten = json.loads(zeile)
                if daten.get("typ") == "ersetzt":
                    self._ersetzt[(int(daten["alt_nr"]), str(daten["text"]))] = \
                        int(daten["neu_nr"])
                elif daten.get("typ") == "zug":
                    zuege.append(Zug.aus_dict(daten))
                else:
                    raise ValueError(f"unbekannter Typ {daten.get('typ')!r}")
            except (ValueError, KeyError, TypeError, AttributeError):
                # Eine kaputte Zeile kostet genau diese Zeile, nicht das
                # Gedaechtnis. Gezaehlt wird sie trotzdem - stilles
                # Ueberspringen sieht hinterher aus wie Vergesslichkeit.
                self.defekte += 1
        if self.defekte:
            self.letzter_fehler = (f"{self.defekte} defekte Eintraege in {self.pfad.name} "
                                   f"uebersprungen")
        behalten = self._behalten()
        return zuege[-behalten:]

    def _behalten(self) -> int:
        return max(50, int(getattr(self.cfg, "episoden_behalten", 2000) or 2000))

    # ------------------------------------------------------------ Schreiben
    def vermerken(self, frage: str, antwort: str = "", weg: str = "",
                  calls: Sequence = ()) -> Optional[Zug]:
        """Einen Zug ablegen. Gibt ihn zurueck - auch wenn die Platte streikt."""
        if not self.aktiv:
            return None
        frage = schwaerzen(str(frage or "").strip())
        if not frage:
            return None
        antwort = schwaerzen(str(antwort or "").strip())
        with self._sperre:
            zuege = self.zuege
            zug = Zug(
                nr=(zuege[-1].nr + 1) if zuege else 1,
                zeit=dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
                sitzung=self.sitzung,
                frage=frage[:TEXT_GRENZE], antwort=antwort[:TEXT_GRENZE], weg=weg,
                werkzeuge=[f"{getattr(c, 'werkzeug', '?')}:"
                           f"{'ok' if getattr(getattr(c, 'ergebnis', None), 'ok', False) else 'fehler'}"
                           for c in calls or []][:20],
                bezuege=(bezuege_aus_text(frage, "frage") + bezuege_aus_aufrufen(calls)
                         + bezuege_aus_text(antwort[:TEXT_GRENZE], "antwort"))[:30],
                vorgaben=vorgaben_finden(frage)[:8],
                aussagen=aussagen_finden(frage)[:8],
            )
            zuege.append(zug)
            del zuege[:-self._behalten()]
            self._anhaengen(zug.als_dict())
            return zug

    def ersetzung_vermerken(self, alt: Zug, satz: str, neu: Zug) -> None:
        """Eine fruehere Aussage gilt nicht mehr. Angehaengt, nicht ueberschrieben."""
        with self._sperre:
            self._ersetzt[(alt.nr, satz)] = neu.nr
            self._anhaengen({"v": VERSION, "typ": "ersetzt", "alt_nr": alt.nr,
                             "text": satz, "neu_nr": neu.nr,
                             "zeit": dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")})

    def _anhaengen(self, daten: dict) -> None:
        try:
            self.pfad.parent.mkdir(parents=True, exist_ok=True)
            with open(self.pfad, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(daten, ensure_ascii=False) + "\n")
            self._zeilen += 1
            self._verdichten_wenn_noetig()
        except OSError as exc:
            self.geschrieben = False
            self.letzter_fehler = f"Episodengedaechtnis nicht schreibbar: {exc}"

    def _verdichten_wenn_noetig(self) -> None:
        """Die Datei auf die letzten EPISODEN_BEHALTEN Zuege kuerzen.

        Erst ab einem Viertel Ueberhang, damit nicht jeder Zug die ganze Datei
        neu schreibt. Atomar ueber eine Nachbardatei - ein Absturz mittendrin
        hinterlaesst die alte Fassung, nicht eine halbe.
        """
        behalten = self._behalten()
        zuege = self._zuege or []
        # Gezaehlt wird mit, nicht nachgelesen: Sonst laese jeder Zug bei
        # voller Datei das ganze Megabyte noch einmal.
        if len(zuege) < behalten or self._zeilen <= behalten * 1.25:
            return
        erste = zuege[0].nr
        datensaetze = [z.als_dict() for z in zuege]
        datensaetze += [{"v": VERSION, "typ": "ersetzt", "alt_nr": alt, "text": text,
                         "neu_nr": neu}
                        for (alt, text), neu in self._ersetzt.items() if alt >= erste]
        neu_pfad = self.pfad.with_suffix(".jsonl.neu")
        with open(neu_pfad, "w", encoding="utf-8") as fh:
            for daten in datensaetze:
                fh.write(json.dumps(daten, ensure_ascii=False) + "\n")
        os.replace(neu_pfad, self.pfad)
        self._zeilen = len(datensaetze)

    # ---------------------------------------------------------------- Abruf
    def abrufen(self, frage: str, ausser: Iterable[int] = (), anzahl: int = 4,
                zusatz: Sequence[str] = ()) -> List[Treffer]:
        """Die passendsten frueheren Zuege - ohne die, die ohnehin im Verlauf stehen.

        Wert = gedeckter IDF-Anteil * (1 + Bonus) * Alter.

        Der Anteil zaehlt nur die Fragewoerter, die im Gedaechtnis ueberhaupt
        vorkommen. Ein Wort, das nie fiel, kann keinen Zug auszeichnen - zaehlte
        es mit, verwaesserte jede ausfuehrliche Frage die Relevanz unter die
        Schwelle ("Welche Datenbank nutzen wir fuer das Projekt?" fand die
        Datenbank-Stelle nicht, weil "nutzen" und "Projekt" nie gefallen waren).
        Ein Zug braucht ausserdem mindestens ein tragendes Wort (vier Zeichen,
        eine Ziffer oder CJK) - ein Allerweltswort allein reicht nicht.

        Bonus fuer Zuege mit Vorgaben (sie werden spaeter am haeufigsten
        gemeint) und fuer ausdruecklich aufgeloeste Bezuege (`zusatz`).
        """
        ausgenommen = set(ausser)
        zuege = [z for z in self.zuege if z.nr not in ausgenommen]
        gefragt = set(tokens(" ".join([frage, *zusatz]))) - ALLERWELTSWOERTER
        if not zuege or not gefragt:
            return []
        idf, bekannt = self._idf(gefragt)
        gesamt = sum(idf[t] for t in bekannt) or 1.0
        zusatz_woerter = set(tokens(" ".join(zusatz)))
        juengste = max(z.nr for z in self.zuege)
        jetzt = dt.datetime.now()

        treffer: List[Treffer] = []
        for zug in zuege:
            gedeckt = gefragt & zug.woerter
            if not any(_tragend(t) for t in gedeckt):
                continue
            relevanz = sum(idf[t] for t in gedeckt) / gesamt
            if relevanz < MINDEST_RELEVANZ and not (gedeckt & zusatz_woerter):
                continue
            bonus = (0.3 if zug.vorgaben or zug.aussagen else 0.0) + \
                    (0.4 if gedeckt & zusatz_woerter else 0.0)
            treffer.append(Treffer(zug, relevanz * (1 + bonus) *
                                   self._alter(zug, juengste, jetzt), sorted(gedeckt)))
        treffer.sort(key=lambda t: -t.wert)
        return treffer[:max(0, anzahl)]

    def _idf(self, woerter: Set[str]) -> Tuple[Dict[str, float], Set[str]]:
        """IDF je Wort - und welche Woerter im Gedaechtnis ueberhaupt vorkommen."""
        alle = self.zuege
        n = len(alle)
        haeufig = {w: sum(1 for z in alle if w in z.woerter) for w in woerter}
        idf = {w: math.log((n + 1) / (df + 0.5)) + 0.1 for w, df in haeufig.items()}
        return idf, {w for w, df in haeufig.items() if df}

    def _alter(self, zug: Zug, juengste: int, jetzt: dt.datetime) -> float:
        """Abschlag fuer Alter: in der Sitzung nach Zuegen, davor nach Tagen.

        Bewusst flach. Eine Vorgabe vom Anfang der Sitzung ist nicht weniger
        gueltig, nur weil zwanzig Zuege dazwischen liegen.
        """
        if zug.sitzung == self.sitzung:
            return max(0.6, 0.98 ** (juengste - zug.nr))
        try:
            tage = max(0.0, (jetzt - dt.datetime.fromisoformat(zug.zeit)).days)
        except ValueError:
            tage = 30.0
        return max(0.2, 0.5 * 0.5 ** (tage / 30.0))

    # ------------------------------------------------- Vorgaben und Aussagen
    def geltende_vorgaben(self, anzahl: int = 8) -> List[Tuple[str, Zug]]:
        """Vorgaben dieser Sitzung, die nicht ersetzt wurden - juengste zuerst.

        Nur DIESE Sitzung: "antworte auf Englisch" von gestern soll heute nicht
        stillschweigend weitergelten. Was dauerhaft gelten soll, ist eine
        Lektion (learning.py) oder eine Grundregel.
        """
        aus: List[Tuple[str, Zug]] = []
        for zug in reversed(self.sitzungszuege()):
            for satz in zug.vorgaben:
                if self.ersetzt_durch(zug, satz) is None:
                    aus.append((satz, zug))
        return aus[:anzahl]

    def nutzeraussagen(self, grenze: int = 300) -> List[Tuple[str, Zug]]:
        """Alle nicht ersetzten Aussagen und Vorgaben des Nutzers, juengste zuerst."""
        aus: List[Tuple[str, Zug]] = []
        for zug in reversed(self.zuege[-grenze:]):
            for satz in list(zug.aussagen) + [v for v in zug.vorgaben
                                              if v not in zug.aussagen]:
                if self.ersetzt_durch(zug, satz) is None:
                    aus.append((satz, zug))
        return aus

    def letzte_bezuege(self, grenze: int = 40) -> List[Tuple[Bezug, Zug]]:
        """Alle Bezuege der letzten Zuege, juengste zuerst; im Zug die zuletzt
        genannten zuerst, Werkzeugbezuege vor blossen Erwaehnungen."""
        rang = {"werkzeug": 0, "frage": 1, "antwort": 2}
        aus: List[Tuple[Bezug, Zug]] = []
        for zug in reversed(self.zuege[-grenze:]):
            for bezug in sorted(reversed(zug.bezuege), key=lambda b: rang.get(b.quelle, 3)):
                aus.append((bezug, zug))
        return aus

    def vorige_sitzung(self) -> List[Zug]:
        """Die Zuege des juengsten FRUEHEREN Programmlaufs."""
        frueher = [z for z in self.zuege if z.sitzung != self.sitzung]
        if not frueher:
            return []
        letzte = frueher[-1].sitzung
        return [z for z in frueher if z.sitzung == letzte]

    # ---------------------------------------------------------- Kennzahlen
    def kennzahlen(self) -> Dict[str, object]:
        if not self.aktiv:
            return {"aktiv": False}
        zuege = self.zuege
        return {"aktiv": True, "zuege": len(zuege),
                "diese_sitzung": len(self.sitzungszuege()),
                "sitzungen": len({z.sitzung for z in zuege}),
                "vorgaben": len(self.geltende_vorgaben(99)),
                "ersetzt": len(self._ersetzt), "defekte": self.defekte,
                "datei": str(self.pfad), "geschrieben": self.geschrieben,
                "letzter_fehler": self.letzter_fehler}
