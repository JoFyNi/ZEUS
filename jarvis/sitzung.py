"""Sitzungsgedächtnis: was im Gespräch gesagt wurde, überlebt das Programmende.

Bisher stand der Verlauf ausschließlich in einem Ringpuffer im RAM
(`agent.history`). Programm zu, Wissen weg. Dieses Modul verdichtet den
Verlauf zu datierten Zeilen und legt sie als ganz normale Markdown-Notiz in
`_Sitzungen/` ab - eine Notiz **pro Tag**, nicht pro Sitzung: Mehrere Starts
am selben Tag ergäben sonst ein Dutzend Schnipsel, die einzeln zu klein sind,
um im Abruf je zu treffen.

Drei Entscheidungen, die den Rest erklären:

* **Alles bleibt Markdown in der Vault.** Kein zweiter Vektorspeicher, keine
  neue Abhängigkeit. Die Notiz liegt in Obsidian, taucht im Graphen auf, ist
  im Backup, ist über `notiz_lesen` erreichbar - und der Nutzer kann sie von
  Hand korrigieren, ohne die Buchführung zu beschädigen.
* **ADD-only.** Eine bestehende Zeile wird nie überschrieben und nie gelöscht,
  auch bei Widerspruch nicht. Der neue Fakt kommt als neue datierte Zeile dazu;
  beim Einsetzen in den Prompt gewinnt der jüngere. Verdrängt wird nur über die
  Bewährung - und auch dann bleibt die Zeile in der Datei stehen.
* **Dieselbe Bewährung wie bei den Lektionen.** Status, Zähler und Schwellen
  kommen aus `learning.py`, nicht aus einer zweiten Kopie. Ein einzelner
  Gesprächszug macht keine dauerhafte Wahrheit.

Dieser Teil (Schritt 1) enthält Datenmodell, Lesen und Schreiben. Extraktion,
Abruf und Budgetierung folgen.
"""
from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import settings
from .learning import (BESTAETIGT, VERWORFEN, VORLAEUFIG, Bewaehrungszeile,
                       zeile_zerlegen)
from .planner import _json_laden          # tolerantes JSON: Codefence, Vortext
from .vault import similarity

# --------------------------------------------------------------- Konstanten

FAKT = "fakt"
ENTSCHEIDUNG = "entscheidung"
OFFEN = "offen"

#: Überschrift je Rubrik, in der Reihenfolge, in der sie in der Notiz stehen.
RUBRIKEN = {
    FAKT: "Fakten",
    ENTSCHEIDUNG: "Entscheidungen",
    OFFEN: "Offen",
}
BERUEHRT = "Berührte Notizen"

#: Eine brauchbare Aussage ist ein Satz, kein Wort und kein Absatz.
MINDESTLAENGE = 15
HOECHSTLAENGE = 300

#: Schwelle für "das steht schon da". Gleicher Wert wie `learning._aehnlich`.
DUBLETTENSCHWELLE = 0.88

#: So viel Verlauf geht höchstens in den Extraktionsprompt. Mehr bringt nichts:
#: Ein schnelles Modell verliert bei langen Eingaben die Genauigkeit, und
#: verdichtet wird ohnehin alle paar Züge.
AUSZUG_ZEICHEN = 6000

#: Obergrenze des Zwischenspeichers, falls das Modell mehrfach nicht antwortet.
#: Ohne sie wüchse der Puffer bei einem toten Server bis zum Sitzungsende.
MAX_GEMERKTE_ZUEGE = 40

_ABSCHNITT_RE = re.compile(r"^##\s+(\d{1,2}:\d{2})\s*[–\-]\s*(\d{1,2}:\d{2})\s*$")
_RUBRIK_RE = re.compile(r"^###\s+(.+?)\s*$")
_ZEIT_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2})[ T](\d{1,2}:\d{2})\]\s*(.*)$")
_HAKEN_RE = re.compile(r"^\[([ xX])\]\s*(.*)$")
_WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)")

_TAGESDATEI_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")


def _jetzt() -> dt.datetime:
    return dt.datetime.now()


def brauchbar(text: str) -> bool:
    """Ist die Zeichenkette als Erinnerung zu gebrauchen?

    Die Prüfung steht bewusst im Code und nicht im Prompt: Ein Modell hält sich
    an eine Längenvorgabe, wenn es gerade passt. Zu kurz heißt ohne Aussage
    ("qwen3"), zu lang heißt: es hat den ganzen Absatz durchgereicht.
    """
    sauber = (text or "").strip()
    return MINDESTLAENGE < len(sauber) < HOECHSTLAENGE


#: Zeichenketten mit Ziffern oder Doppelpunkt sind Kennzeichen, keine Wörter:
#: Modellnamen, Versionen, Ports, Datumsangaben.
_KENNZEICHEN_RE = re.compile(r"[a-zäöüß0-9][a-zäöüß0-9._:/-]*\d[a-zäöüß0-9._:/-]*",
                             re.IGNORECASE)


def _aehnlich(a: str, b: str) -> bool:
    """Ist `a` derselbe Satz wie `b` - oder seine spätere Korrektur?

    Die reine Ähnlichkeit reicht hier nicht. "Hauptmodell ist qwen3.8:27b" und
    "Hauptmodell ist jetzt qwen3:32b" liegen bei über 0,9 - als Dublette
    behandelt wäre die NEUERE Angabe nie geschrieben worden, und das
    Gedächtnis bliebe auf dem alten Stand stehen. Unterscheiden sich die
    Kennzeichen (Modellnamen, Versionen, Ports, Zahlen), ist es eine neue
    Aussage und kommt als eigene Zeile dazu; welche gilt, entscheidet erst
    der Abruf über den Zeitstempel.
    """
    if similarity(a, b) <= DUBLETTENSCHWELLE:
        return False
    return set(_KENNZEICHEN_RE.findall(a.lower())) == \
        set(_KENNZEICHEN_RE.findall(b.lower()))


# -------------------------------------------------------------- Datenmodell

@dataclass
class Sitzungsfakt(Bewaehrungszeile):
    """Eine datierte Zeile aus einer Sitzungsnotiz.

    Erbt Status, Zähler und Bewährung von der Lektion - dieselbe Mechanik,
    getrennte Dateien. Der Zeitstempel steht **vor** dem Text und ist die
    Grundlage für "der jüngere gewinnt".
    """

    zeit: str = ""            # "2026-09-02 15:10"
    art: str = FAKT           # fakt | entscheidung | offen
    erledigt: bool = False    # nur bei art == offen
    tag: str = ""             # aus dem Dateinamen, für die Sortierung
    abschnitt: str = ""       # "14:32 – 15:10", zur Anzeige
    score: float = 0.0        # Trefferwert aus dem Index, nur zur Sortierung
    # Welche ältere Angabe dieser Fakt im Prompt verdrängt hat - nur zur
    # Anzeige, wird nie in die Datei geschrieben (_widerspruchsfrei).
    ersetzt: str = ""

    # ------------------------------------------------------------- Schreiben
    def als_zeile(self) -> str:
        """Markdown-Zeile, wie sie in der Notiz steht.

        Offene Punkte bekommen Obsidians Checkbox-Syntax, damit sie dort
        abhakbar sind - und tragen deshalb keine Bewährungsmarke: Ein Vorhaben
        bewährt sich nicht, es wird erledigt.
        """
        if self.art == OFFEN:
            return f"- [{'x' if self.erledigt else ' '}] {self.text}"
        kopf = f"[{self.zeit}] " if self.zeit else ""
        return f"- {kopf}{self.text}  <!-- {self.kommentar()} -->"

    # ---------------------------------------------------------------- Lesen
    @classmethod
    def aus_zeile(cls, zeile: str, art: str = FAKT, tag: str = "",
                  abschnitt: str = "") -> Optional["Sitzungsfakt"]:
        """Liest eine Zeile - auch eine von Hand geschriebene.

        Erlaubt ist deshalb alles, was ein Mensch in Obsidian tippt: ohne
        Bewährungsmarke (gilt als `vorlaeufig`, Vorbild `Lektion.aus_zeile`),
        ohne Zeitstempel, mit abgehaktem Kästchen.
        """
        text, kommentar = zeile_zerlegen(zeile.rstrip())
        if not text:
            return None

        erledigt = False
        haken = _HAKEN_RE.match(text)
        if haken:
            erledigt = haken.group(1).lower() == "x"
            text = haken.group(2).strip()

        zeit = ""
        stempel = _ZEIT_RE.match(text)
        if stempel:
            zeit = f"{stempel.group(1)} {stempel.group(2)}"
            text = stempel.group(3).strip()

        if not text:
            return None

        eintrag = cls(text=text, zeit=zeit or (f"{tag} 00:00" if tag else ""),
                      art=art, erledigt=erledigt, tag=tag, abschnitt=abschnitt)
        if kommentar:
            eintrag.marke_lesen(kommentar)
        return eintrag

    # --------------------------------------------------------------- Ordnung
    @property
    def sortierschluessel(self) -> tuple:
        """`bestaetigt` vor `vorlaeufig`, danach jüngerer Zeitstempel zuerst."""
        rang = {BESTAETIGT: 0, VORLAEUFIG: 1, VERWORFEN: 2}
        return (rang.get(self.status, 1), _negiert(self.zeit))

    def anzeige(self) -> str:
        marke = "" if self.status == VORLAEUFIG else f" [{self.status}]"
        return f"{self.text}{marke}"


#: Rubrik -> Feldname im Abschnitt. Einmal, damit es nicht dreimal dasteht.
_FELD = {FAKT: "fakten", ENTSCHEIDUNG: "entscheidungen", OFFEN: "offen"}


def _widerspruchsfrei(fakten: Sequence["Sitzungsfakt"],
                      grenze: Optional[int] = None) -> List["Sitzungsfakt"]:
    """Von zwei Aussagen zum selben Gegenstand bleibt nur die jüngere.

    Beide Zeilen bleiben in der Datei stehen - verdrängt wird ausschließlich
    im Prompt. Sonst müsste beim Schreiben entschieden werden, was falsch ist,
    und genau das kann niemand zuverlässig: "Hauptmodell ist qwen3:32b" ist
    kein Widerspruch zu "Rückfallmodell ist qwen3:32b".

    Bis zum 12.10.2026 entschied das eine Wortüberlappung (Jaccard >= 0,5) -
    und genau dieses Paar lag bei 0,6: Das Rückfallmodell verschwand aus dem
    Prompt. Jetzt urteilt `aussagen.vergleichen` über Gegenstand und Wert;
    verdrängt wird nur bei GLEICH (Dublette) und WIDERSPRUCH, und der jüngere
    Fakt trägt dann die verdrängte Angabe in `ersetzt` mit - der Prompt zeigt
    die Änderung, statt sie zu verschweigen.

    Die Liste kommt bereits sortiert an (jüngere/bestätigte zuerst), deshalb
    genügt es, spätere Dubletten wegzulassen - und bei `grenze` aufzuhören,
    sobald genug beisammen sind: Was danach käme, käme ohnehin nicht dran.
    """
    from .aussagen import GLEICH, WIDERSPRUCH, vergleichen

    behalten: List["Sitzungsfakt"] = []
    for fakt in fakten:
        if grenze is not None and len(behalten) >= grenze:
            break
        if not (fakt.text or "").strip():
            continue
        verdraengt_von = None
        for andere in behalten:
            urteil = vergleichen(andere.text, fakt.text)
            if urteil in (GLEICH, WIDERSPRUCH):
                verdraengt_von = (andere, urteil)
                break
        if verdraengt_von is None:
            behalten.append(fakt)
            continue
        andere, urteil = verdraengt_von
        if urteil == WIDERSPRUCH and not andere.ersetzt:
            andere.ersetzt = f"„{fakt.text}“ ({fakt.tag or fakt.zeit[:10]})"
    return behalten


def _liste(wert) -> List[str]:
    """Was das Modell liefert, in eine Liste von Zeichenketten zwingen."""
    if isinstance(wert, str):
        wert = [wert]
    if not isinstance(wert, (list, tuple)):
        return []
    return [str(e) for e in wert if isinstance(e, (str, int, float)) and str(e).strip()]


def _marke_setzen(zeile: str, fakt: "Sitzungsfakt") -> str:
    """Die Bewährungsmarke einer bestehenden Zeile ersetzen oder anhängen.

    Bewusst per Textchirurgie und nicht über `als_zeile()`: So bleibt alles
    links vom Kommentar unangetastet - Einrückung, Zeitstempel, Handarbeit.
    """
    kern = re.sub(r"\s*<!--.*?-->\s*$", "", zeile.rstrip())
    return f"{kern}  <!-- {fakt.kommentar()} -->"


def _negiert(zeit: str) -> str:
    """Sortierhilfe: macht aus 'später' ein 'kleiner'."""
    return "".join(chr(0x10FFFF - ord(z)) if ord(z) < 0x10FFFF else z
                   for z in (zeit or ""))


@dataclass
class Sitzungsabschnitt:
    """Ein `## HH:MM – HH:MM`-Block: ein Programmlauf."""

    beginn: str = ""
    ende: str = ""
    fakten: List[Sitzungsfakt] = field(default_factory=list)
    entscheidungen: List[Sitzungsfakt] = field(default_factory=list)
    offen: List[Sitzungsfakt] = field(default_factory=list)
    notizen: List[str] = field(default_factory=list)

    @property
    def ueberschrift(self) -> str:
        return f"{self.beginn} – {self.ende}"

    def alle(self) -> List[Sitzungsfakt]:
        return list(self.fakten) + list(self.entscheidungen) + list(self.offen)


@dataclass
class Sitzungstag:
    """Eine Tagesnotiz mit allen Abschnitten."""

    tag: str = ""
    pfad: Optional[Path] = None
    abschnitte: List[Sitzungsabschnitt] = field(default_factory=list)
    roh: str = ""

    @property
    def vorhanden(self) -> bool:
        return bool(self.pfad and self.pfad.exists())

    def alle(self) -> List[Sitzungsfakt]:
        eintraege: List[Sitzungsfakt] = []
        for abschnitt in self.abschnitte:
            eintraege.extend(abschnitt.alle())
        return eintraege

    def texte(self) -> List[str]:
        """Alle Zeilentexte des Tages - Grundlage der Dublettenprüfung."""
        return [e.text for e in self.alle()]

    def notizen(self) -> List[str]:
        gesehen: List[str] = []
        for abschnitt in self.abschnitte:
            for name in abschnitt.notizen:
                if name not in gesehen:
                    gesehen.append(name)
        return gesehen

    def zahlen(self) -> Dict[str, int]:
        werte = {VORLAEUFIG: 0, BESTAETIGT: 0, VERWORFEN: 0, "offen": 0}
        for eintrag in self.alle():
            if eintrag.art == OFFEN:
                if not eintrag.erledigt:
                    werte["offen"] += 1
                continue
            werte[eintrag.status] = werte.get(eintrag.status, 0) + 1
        return werte


# ----------------------------------------------------------------- Das Werk

class Sitzungsgedaechtnis:
    """Liest und schreibt die Tagesnotizen in `_Sitzungen/`.

    Der Agent wird optional übergeben, damit sich das Modul für Tests auch
    allein aufsetzen lässt; gebraucht wird von ihm nur die Vault-Wurzel und
    (später) das Modell.
    """

    def __init__(self, agent=None, cfg=settings, index=None):
        self.agent = agent
        self.cfg = cfg
        self._index = index
        # Letzter verschluckter Fehler. Gedächtnis ist eine Zutat, kein
        # Fundament: Es darf den Agenten nie anhalten - aber lautlos
        # scheitern soll es auch nicht (Vorbild: Lernwerk.letzter_fehler).
        self.letzter_fehler: str = ""
        self.zuletzt_verdichtet: str = ""
        self.beginn: str = _jetzt().strftime("%H:%M")
        self.geschriebene_zeilen: int = 0
        # Verlauf SEIT der letzten Verdichtung. Ein eigener Puffer, nicht
        # `agent.history`: Der ist ein Ringpuffer über HISTORY_TURNS und hätte
        # die älteren Züge längst verworfen, wenn nach zehn verdichtet wird.
        self._zuege: List[dict] = []
        # Fakten, die in den laufenden Prompt gewandert sind (Bewährung).
        self._offen: List[Sitzungsfakt] = []
        self.verdichtungen: int = 0

    # ------------------------------------------------------------- Schalter
    @property
    def aktiv(self) -> bool:
        return bool(getattr(self.cfg, "session_memory", False))

    # ------------------------------------------------------------ Ablageorte
    @property
    def wurzel(self) -> Path:
        if self.agent is not None:
            struktur = getattr(self.agent, "structure", None)
            wurzel = getattr(struktur, "vault_root", None)
            if wurzel:
                return Path(wurzel)
        return Path(self.cfg.vault_path)

    @property
    def ordner(self) -> Path:
        return self.wurzel / (self.cfg.session_folder or "_Sitzungen")

    def datei(self, tag: Optional[dt.date] = None) -> Path:
        tag = tag or _jetzt().date()
        return self.ordner / f"{tag.isoformat()}.md"

    def notizen(self) -> List[Path]:
        """Alle Tagesnotizen, jüngste zuerst."""
        try:
            gefunden = [p for p in self.ordner.glob("*.md")
                        if _TAGESDATEI_RE.match(p.name)]
        except OSError as exc:
            self.letzter_fehler = f"Sitzungsordner nicht lesbar: {exc}"
            return []
        return sorted(gefunden, key=lambda p: p.name, reverse=True)

    # ----------------------------------------------------------------- Lesen
    def lesen(self, tag: Optional[dt.date] = None) -> Sitzungstag:
        """Eine Tagesnotiz einlesen. Fehlt sie, kommt ein leerer Tag zurück."""
        if not self.aktiv:
            return Sitzungstag(tag=(tag or _jetzt().date()).isoformat())
        return self._datei_lesen(self.datei(tag))

    def lesen_pfad(self, pfad: Path) -> Sitzungstag:
        """Eine bestimmte Tagesdatei einlesen (für /sitzungen)."""
        return self._datei_lesen(pfad) if self.aktiv else Sitzungstag(tag=pfad.stem)

    def juengste(self) -> Optional[Sitzungstag]:
        """Die jüngste vorhandene Tagesnotiz - Grundlage des Starts."""
        if not self.aktiv:
            return None
        for pfad in self.notizen():
            gelesen = self._datei_lesen(pfad)
            if gelesen.alle():
                return gelesen
        return None

    def _datei_lesen(self, pfad: Path) -> Sitzungstag:
        tag = pfad.stem
        if not pfad.exists():
            return Sitzungstag(tag=tag, pfad=pfad)
        try:
            roh = pfad.read_text(encoding="utf-8")
        except OSError as exc:
            self.letzter_fehler = f"Sitzungsnotiz '{pfad.name}' nicht lesbar: {exc}"
            return Sitzungstag(tag=tag, pfad=pfad)
        gelesen = self._zerlegen(roh, tag)
        gelesen.pfad = pfad
        gelesen.roh = roh
        return gelesen

    @staticmethod
    def _zerlegen(roh: str, tag: str) -> Sitzungstag:
        """Markdown -> Datenmodell, tolerant gegen Handarbeit.

        Der Nutzer soll die Notiz in Obsidian bearbeiten dürfen. Alles, was
        hier nicht in eine Rubrik passt, wird deshalb übergangen statt zu
        einem Fehler zu führen.
        """
        gelesen = Sitzungstag(tag=tag)
        abschnitt: Optional[Sitzungsabschnitt] = None
        rubrik = ""

        for zeile in (roh or "").splitlines():
            gestutzt = zeile.strip()
            kopf = _ABSCHNITT_RE.match(gestutzt)
            if kopf:
                abschnitt = Sitzungsabschnitt(beginn=kopf.group(1), ende=kopf.group(2))
                gelesen.abschnitte.append(abschnitt)
                rubrik = ""
                continue
            unterkopf = _RUBRIK_RE.match(gestutzt)
            if unterkopf:
                rubrik = unterkopf.group(1).strip()
                continue
            if not gestutzt.startswith("- ") or abschnitt is None:
                continue

            if rubrik == BERUEHRT or rubrik.lower().startswith("berührte"):
                for treffer in _WIKILINK_RE.finditer(gestutzt):
                    name = treffer.group(1).strip()
                    if name and name not in abschnitt.notizen:
                        abschnitt.notizen.append(name)
                continue

            art = {"Fakten": FAKT, "Entscheidungen": ENTSCHEIDUNG,
                   "Offen": OFFEN}.get(rubrik)
            if art is None:
                continue
            eintrag = Sitzungsfakt.aus_zeile(gestutzt, art=art, tag=tag,
                                             abschnitt=abschnitt.ueberschrift)
            if eintrag is None:
                continue
            {FAKT: abschnitt.fakten, ENTSCHEIDUNG: abschnitt.entscheidungen,
             OFFEN: abschnitt.offen}[art].append(eintrag)

        return gelesen

    # ------------------------------------------------------------- Schreiben
    def schreiben(self, fakten: Sequence[str] = (),
                  entscheidungen: Sequence[str] = (),
                  offen: Sequence[str] = (),
                  notizen: Sequence[str] = (),
                  zeitpunkt: Optional[dt.datetime] = None) -> Optional[Path]:
        """Hängt einen neuen Abschnitt an die Tagesnotiz an.

        Geschrieben wird **direkt**, bewusst nicht über die Werkzeugebene:
        Gedächtnis darf nicht am Trockenlauf oder an `MAX_FILES_PER_RUN`
        scheitern - genau wie bei `Lernwerk._anhaengen`.

        Rückgabe: der Pfad, wenn etwas geschrieben wurde; sonst None. Nichts
        Brauchbares zu haben ist der Normalfall einer kurzen Sitzung und kein
        Fehler - insbesondere entsteht dann **keine leere Notiz**.
        """
        if not self.aktiv:
            return None

        jetzt = zeitpunkt or _jetzt()
        pfad = self.datei(jetzt.date())
        vorhanden = self._datei_lesen(pfad)
        bekannt = vorhanden.texte()

        eintraege: Dict[str, List[Sitzungsfakt]] = {FAKT: [], ENTSCHEIDUNG: [],
                                                    OFFEN: []}
        stempel = jetzt.strftime("%Y-%m-%d %H:%M")
        for art, roh in ((FAKT, fakten), (ENTSCHEIDUNG, entscheidungen),
                         (OFFEN, offen)):
            for text in roh or ():
                sauber = " ".join(str(text or "").split())
                if not brauchbar(sauber):
                    continue
                if any(_aehnlich(sauber, alt) for alt in bekannt):
                    continue
                bekannt.append(sauber)
                eintraege[art].append(
                    Sitzungsfakt(text=sauber, zeit=stempel, art=art))

        verweise = self._verweise(notizen)
        if not any(eintraege.values()) and not verweise:
            return None

        block = self._block_bauen(jetzt, eintraege, verweise)
        if not self._anhaengen(pfad, block, jetzt.date()):
            return None

        self.zuletzt_verdichtet = jetzt.strftime("%Y-%m-%d %H:%M")
        self.geschriebene_zeilen += sum(len(v) for v in eintraege.values())
        self._einindexieren(pfad)
        return pfad

    def _verweise(self, notizen: Sequence[str]) -> List[str]:
        """Notiztitel zu echten Wikilinks - damit Backlinks entstehen."""
        sauber: List[str] = []
        for name in notizen or ():
            titel = str(name or "").strip().strip("[]").strip()
            if not titel or len(titel) > 200:
                continue
            if titel not in sauber:
                sauber.append(titel)
        return sauber[:20]

    def _block_bauen(self, jetzt: dt.datetime,
                     eintraege: Dict[str, List[Sitzungsfakt]],
                     verweise: Sequence[str]) -> str:
        zeilen = [f"## {self.beginn} – {jetzt.strftime('%H:%M')}", ""]
        for art, ueberschrift in RUBRIKEN.items():
            if not eintraege.get(art):
                continue
            zeilen.append(f"### {ueberschrift}")
            zeilen.extend(e.als_zeile() for e in eintraege[art])
            zeilen.append("")
        if verweise:
            zeilen.append(f"### {BERUEHRT}")
            zeilen.extend(f"- [[{name}]]" for name in verweise)
            zeilen.append("")
        return "\n".join(zeilen)

    def _kopf(self, tag: dt.date) -> str:
        return (f"---\ntags:\n  - sitzung\n  - gedaechtnis\n"
                f"datum: {tag.isoformat()}\n---\n\n"
                f"# Sitzung {tag.isoformat()}\n\n")

    def _anhaengen(self, pfad: Path, block: str, tag: dt.date) -> bool:
        """Anhängen, niemals überschreiben. False bei Fehler - ohne Ausnahme."""
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            neu = not pfad.exists()
            if neu:
                pfad.write_text(self._kopf(tag), encoding="utf-8")
            with open(pfad, "a", encoding="utf-8") as fh:
                if not neu:
                    fh.write("\n")
                fh.write(block.rstrip() + "\n")
            return True
        except OSError as exc:
            # Stillschweigen hieße: die Sitzung ist weg und niemand merkt es.
            self.letzter_fehler = f"Sitzungsnotiz nicht schreibbar: {exc}"
            return False

    def _einindexieren(self, pfad: Path) -> None:
        """Sofort einindexieren, damit die Notiz noch heute auffindbar ist."""
        index = self._index or getattr(self.agent, "index", None)
        if index is None:
            return
        try:
            from .vault import parse_note
            note = parse_note(pfad, self.wurzel)
            if note is not None:
                index.add_note_to_index(note)
        except Exception as exc:      # Index ist Beiwerk, nicht Bedingung
            self.letzter_fehler = f"Sitzungsnotiz nicht indexierbar: {exc}"

    # ================================================== Auslösung / Buchführung
    def zug_vermerken(self, frage: str, antwort: str) -> None:
        """Einen Gesprächszug für die nächste Verdichtung vormerken.

        Wird nach jeder beantworteten Frage gerufen. Kostet nichts - der
        Modellaufruf passiert erst beim Verdichten.
        """
        if not self.aktiv:
            return
        frage = " ".join(str(frage or "").split())
        antwort = " ".join(str(antwort or "").split())
        if not frage and not antwort:
            return
        self._zuege.append({"frage": frage, "antwort": antwort})
        del self._zuege[:-MAX_GEMERKTE_ZUEGE]

    @property
    def offene_zuege(self) -> int:
        return len(self._zuege)

    def faellig(self) -> bool:
        """Ist die periodische Verdichtung dran?

        Der periodische Lauf ist der eigentliche Schutz: Ohne ihn geht bei
        einem Absturz oder einem zugeklappten Terminal die ganze Sitzung
        verloren, weil `/ende` nie erreicht wird.
        """
        jeder = max(1, int(getattr(self.cfg, "session_summary_every", 10) or 10))
        return self.aktiv and len(self._zuege) >= jeder

    # ------------------------------------------------------------- Extraktion
    def verdichten(self, grund: str = "", melde=None) -> Optional[Path]:
        """Verlauf seit der letzten Verdichtung zu datierten Zeilen machen.

        Genau EIN Modellaufruf, `schnell=True`, `temperature=0.0`,
        `format_json=True`. Nichts Brauchbares dabei zu haben ist der Normalfall
        einer kurzen Sitzung und kein Fehler - dann entsteht keine Datei.
        """
        if not self.aktiv or not self._zuege:
            return None

        auszug = self._auszug()
        antwort = ""
        try:
            antwort = self._modell().chat(
                [{"role": "user", "content": self._prompt(auszug)}],
                temperature=0.0, format_json=True, num_predict=700, schnell=True)
        except Exception as exc:
            # Puffer bleibt stehen: Beim nächsten Versuch soll derselbe Verlauf
            # noch einmal drankommen, statt still verloren zu gehen.
            self.letzter_fehler = f"Verdichtung fehlgeschlagen: {exc}"
            if melde:
                melde(self.letzter_fehler)
            return None

        daten = _json_laden(antwort)
        self._zuege = []
        self.verdichtungen += 1
        if not isinstance(daten, dict):
            # Kein Fehler des Nutzers und kein Grund für eine leere Notiz -
            # aber sichtbar bleiben soll es trotzdem.
            self.letzter_fehler = "Verdichtung: Antwort war kein brauchbares JSON."
            if melde:
                melde(self.letzter_fehler)
            return None

        pfad = self.schreiben(
            fakten=_liste(daten.get("fakten")),
            entscheidungen=_liste(daten.get("entscheidungen")),
            offen=_liste(daten.get("offen")),
            notizen=_liste(daten.get("notizen")),
        )
        if pfad and melde:
            melde(f"Sitzung verdichtet{f' ({grund})' if grund else ''}: {pfad.name}")
        return pfad

    def _modell(self):
        llm = getattr(self.agent, "llm", None)
        if llm is None:
            raise RuntimeError("Kein Modell verfügbar")
        return llm

    def _auszug(self) -> str:
        zeilen = []
        for zug in self._zuege:
            if zug.get("frage"):
                zeilen.append(f"NUTZER: {zug['frage']}")
            if zug.get("antwort"):
                zeilen.append(f"AGENT: {zug['antwort']}")
        text = "\n".join(zeilen)
        return text[-AUSZUG_ZEICHEN:] if len(text) > AUSZUG_ZEICHEN else text

    def _prompt(self, auszug: str) -> str:
        sprache = getattr(self.cfg, "language", "Deutsch")
        return f"""Lies den folgenden Gesprächsverlauf und ziehe daraus heraus, was
in einer Woche noch von Bedeutung ist.

STRENGE REGELN:
- Nur, was im Verlauf TATSÄCHLICH STEHT. Keine Interpretation, keine Vermutung,
  kein Weltwissen, keine Höflichkeit.
- Was DU vorgeschlagen hast, ist erst dann eine Entscheidung, wenn der Nutzer
  zugestimmt hat. Sonst gehört es nirgends hin.
- Jede Angabe ist ein vollständiger Satz auf {sprache}, 20 bis 200 Zeichen.
- Lieber eine leere Liste als ein erfundener Eintrag.

WAS WOHIN:
- "fakten": Aussagen über den Nutzer, sein Vorgehen oder seinen Aufbau, die auch
  nächste Woche noch gelten. Vergängliches (ein Dateiname von heute, ein
  Zwischenstand, eine Fehlermeldung) gehört NICHT hinein.
- "entscheidungen": Etwas wurde festgelegt oder verworfen - mit dem Ergebnis.
- "offen": Etwas wurde ausdrücklich als noch zu tun benannt.
- "notizen": Titel von Notizen, die im Gespräch eine Rolle gespielt haben.

VERLAUF:
{auszug}

Antworte mit NICHTS als diesem JSON:
{{"fakten": [], "entscheidungen": [], "offen": [], "notizen": []}}"""

    # ============================================================ Abruf
    def startauszug(self) -> str:
        """Die jüngste Sitzungsnotiz, gekürzt - der Faden vom letzten Mal.

        Bewusst der ROHTEXT der Notiz und nicht eine Auswahl einzelner Zeilen:
        Der Zusammenhang eines Abschnitts ("erst das entschieden, dann jenes
        offen geblieben") geht verloren, sobald man ihn in Einzelsätze zerlegt.
        """
        if not self.aktiv:
            return ""
        juengste = self.juengste()
        if juengste is None:
            return ""
        zeilen = [f"Letzte Sitzung ({juengste.tag}):"]
        for abschnitt in juengste.abschnitte[-2:]:
            for art, ueberschrift in RUBRIKEN.items():
                gruppe = [e for e in getattr(abschnitt, _FELD[art])
                          if e.brauchbar and not (art == OFFEN and e.erledigt)]
                if not gruppe:
                    continue
                zeilen.append(f"{ueberschrift}:")
                zeilen.extend(f"- {e.text}" for e in gruppe)
        if len(zeilen) < 2:
            return ""
        text = "\n".join(zeilen)
        grenze = max(200, int(getattr(self.cfg, "session_recall_chars", 1500) or 1500))
        if len(text) > grenze:
            text = text[:grenze].rsplit("\n", 1)[0] + "\n- […]"
        return text

    def passende_fakten(self, frage: str,
                        anzahl: Optional[int] = None) -> List[Sitzungsfakt]:
        """Die passendsten Fakten früherer Sitzungen zu dieser Frage.

        Der Abruf läuft über den VORHANDENEN Index: Sitzungsnotizen liegen in
        der Vault und sind damit schon indexiert - kein zweiter Speicher, kein
        zusätzlicher Embedding-Aufruf, der nicht ohnehin passierte.
        """
        if not self.aktiv:
            return []
        anzahl = anzahl or int(getattr(self.cfg, "session_facts_in_prompt", 5) or 5)

        punkte: Dict[str, float] = {}
        index = self._index or getattr(self.agent, "index", None)
        if index is not None:
            try:
                for hit in index.search(frage, folder=self.cfg.session_folder,
                                        include_structure=False):
                    stem = str((hit.metadata or {}).get("stem", ""))
                    if _TAGESDATEI_RE.match(stem + ".md"):
                        punkte[stem] = max(punkte.get(stem, 0.0), float(hit.score))
            except Exception as exc:
                # Ein kaputter Index darf keine Frage verhindern; es bleibt
                # die Lesart über die jüngsten Dateien.
                self.letzter_fehler = f"Sitzungsabruf über den Index misslang: {exc}"

        kandidaten: List[Sitzungsfakt] = []
        for pfad in self.notizen()[:14]:
            tag = pfad.stem
            score = punkte.get(tag, 0.0)
            for fakt in self._datei_lesen(pfad).alle():
                if not fakt.brauchbar or fakt.art == OFFEN:
                    continue
                fakt.score = score
                kandidaten.append(fakt)

        kandidaten.sort(key=lambda f: (f.sortierschluessel[0], f.sortierschluessel[1],
                                       -f.score))
        return _widerspruchsfrei(kandidaten, grenze=anzahl)

    def offene_punkte(self, anzahl: int = 5) -> List[Sitzungsfakt]:
        """Nicht abgehakte Punkte aus `### Offen`, jüngste zuerst."""
        if not self.aktiv:
            return []
        gefunden: List[Sitzungsfakt] = []
        for pfad in self.notizen()[:14]:
            for fakt in self._datei_lesen(pfad).alle():
                if fakt.art == OFFEN and not fakt.erledigt:
                    gefunden.append(fakt)
        return gefunden[:anzahl]

    # -------------------------------------------------------------- Bewährung
    def fakt_verwendet(self, fakten: Sequence[Sitzungsfakt]) -> None:
        """Merkt, welche Fakten in den Prompt gewandert sind."""
        self._offen = [f for f in fakten if f is not None]

    def bewerten(self, geglueckt: bool) -> None:
        """Nach dem Zug: Haben die eingesetzten Fakten getaugt?

        Derselbe Tiefpass wie bei den Lektionen und derselbe Maßstab: ob die
        verändernden Werkzeuge geglückt sind, nicht was das Modell behauptet.
        Ein einzelner Zug macht keine dauerhafte Wahrheit.
        """
        offen, self._offen = self._offen, []
        if not offen or not self.aktiv:
            return
        nach_tag: Dict[str, List[Sitzungsfakt]] = {}
        for fakt in offen:
            fakt.verwendet += 1
            if geglueckt:
                fakt.bewaehrt += 1
            fakt.bewerten()
            nach_tag.setdefault(fakt.tag, []).append(fakt)
        for tag, fakten in nach_tag.items():
            self._zaehler_schreiben(self.ordner / f"{tag}.md", fakten)

    def _zaehler_schreiben(self, pfad: Path, fakten: Sequence[Sitzungsfakt]) -> None:
        """Nur die Bewährungsmarke der betroffenen Zeilen neu setzen.

        Alles links vom Kommentar bleibt Byte für Byte stehen - auch bei einer
        von Hand geschriebenen Zeile. Das ist die einzige Stelle, an der eine
        bestehende Zeile überhaupt angefasst wird, und sie ändert ausschließlich
        die Buchführung, nie den Text.
        """
        nach_text = {f.text: f for f in fakten}
        try:
            zeilen = pfad.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            self.letzter_fehler = f"Bewährung nicht schreibbar: {exc}"
            return

        geaendert = False
        for i, zeile in enumerate(zeilen):
            if not zeile.lstrip().startswith("- "):
                continue
            text, _ = zeile_zerlegen(zeile.strip())
            stempel = _ZEIT_RE.match(text)
            if stempel:
                text = stempel.group(3).strip()
            treffer = nach_text.get(text)
            if treffer is None:
                continue
            neu = _marke_setzen(zeile, treffer)
            if neu != zeile:
                zeilen[i] = neu
                geaendert = True

        if not geaendert:
            return
        try:
            pfad.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
        except OSError as exc:
            self.letzter_fehler = f"Bewährung nicht schreibbar: {exc}"

    # ------------------------------------------------------------ Kennzahlen
    def kennzahlen(self) -> Dict[str, object]:
        if not self.aktiv:
            return {"aktiv": False}
        tage = self.notizen()
        zahlen = {VORLAEUFIG: 0, BESTAETIGT: 0, VERWORFEN: 0, "offen": 0}
        for pfad in tage[:30]:
            for schluessel, wert in self._datei_lesen(pfad).zahlen().items():
                zahlen[schluessel] = zahlen.get(schluessel, 0) + wert
        return {
            "aktiv": True,
            "tage": len(tage),
            "ordner": self.cfg.session_folder,
            "zuletzt_verdichtet": self.zuletzt_verdichtet,
            "letzter_fehler": self.letzter_fehler,
            **zahlen,
        }
