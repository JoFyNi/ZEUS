"""Kontextwahl: was zu DIESER Frage aus dem Gespraech gehoert - ausgewaehlt, mit Herkunft.

Der Verlauf (`history`) zeigt dem Modell die letzten HISTORY_TURNS Zuege. Was
davor lag, sah es nicht; worauf sich "die Datei" bezieht, musste es raten;
ob eine neue Angabe einer frueheren widerspricht, merkte niemand. Dieses
Modul stellt dafuer vor jeder Antwort ein ARBEITSGEDAECHTNIS zusammen:

    Bezuege        "die Notiz", "die Datei", "die vorige Loesung" -> worauf?
    Vorgaben       was der Nutzer in dieser Sitzung als geltend festgelegt hat
    Widersprueche  neue Aussage gegen fruehere (Episoden + Sitzungsfakten)
    Anknuepfung    "mach da weiter, wo wir aufgehoert haben" -> womit?
    Erinnerungen   fruehere Zuege, die nicht mehr im Verlauf stehen
    Beleglage      deckt das Abgerufene die Frage ueberhaupt?

Grundsaetze - dieselben wie in kern/kontext.py:

* **Auswaehlen, nicht anhaengen.** Jeder Posten hat einen Rang; passt er
  nicht ins Budget, faellt er GANZ weg und wird gezaehlt.
* **Herkunft steht dabei.** Jede Zeile sagt, woher sie stammt (Nutzer in Zug
  3, Sitzung vom 05.09., Vermutung). Eine Erinnerung ohne Herkunft liest das
  Modell als Tatsache.
* **Vermutung bleibt Vermutung.** Eine aufgeloeste Referenz ist eine
  Annahme und steht als solche da; mehrdeutige werden als unklar gemeldet
  statt geraten (Grundregel R12).
* **Ein kaputtes Gedaechtnis meldet sich.** Scheitert ein Schritt, steht der
  Grund im Block - mit der Anweisung, keine Erinnerung zu behaupten (R9).

Das ist die Ebene UM das Modell herum. Die Aufmerksamkeit im Modell selbst
bleibt, wie sie ist; hier wird nur entschieden, was es zu sehen bekommt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from ..aussagen import WIDERSPRUCH, saetze, vergleichen, zerlegen
from ..episoden import (ALLERWELTSWOERTER, ART_CODE, ART_DATEI, ART_NOTIZ, Bezug,
                        Episodengedaechtnis, Treffer, Zug, bezuege_aus_text,
                        vorgaben_finden)
from ..index import tokens
from ..sprache import ist_cjk_wort
from ..vault import fold
from .triage import Weg

#: Anteil des Promptbudgets fuer das Arbeitsgedaechtnis. Getrennt von
#: GEDAECHTNIS_ANTEIL (Lektionen, Sitzungsfakten): Das hier betrifft DIESES
#: Gespraech und darf von dort nicht verdraengt werden - und umgekehrt.
ARBEIT_ANTEIL = 0.12

_BESTIMMER = (r"(?:die|der|das|den|dem|des|diese[rnms]?|jene[rnms]?|selbe[nm]?|"
              r"gleiche[nm]?|besagte[nm]?|genannte[nm]?|vorige[nm]?|letzte[nm]?|obige[nm]?)")
# Kein Rueckbezug, wenn das Ding gleich danach benannt wird ("die Notiz ueber
# Katzen", "die Datei namens x"). "zur Schwelle" benennt dagegen nichts - es
# sagt, was in dem Ding gesucht wird.
_BENANNT = (r"(?!\s+(?:(?:ue|ü)ber|namens|mit\s+dem\s+(?:titel|namen)|"
            r"\[\[|„|\"|`))")
_VERWEISE: Tuple[Tuple[re.Pattern, Tuple[str, ...]], ...] = (
    (re.compile(rf"\b{_BESTIMMER}\s+(notiz|seite|zusammenfassung|(?:ue|ü)bersicht)\b{_BENANNT}",
                re.IGNORECASE), (ART_NOTIZ,)),
    (re.compile(rf"\b{_BESTIMMER}\s+(funktion|klasse|methode)\b{_BENANNT}", re.IGNORECASE),
     (ART_CODE, ART_DATEI)),
    (re.compile(rf"\b{_BESTIMMER}\s+(datei|skript|script|modul|programm|konfiguration|code)"
                rf"\b{_BENANNT}", re.IGNORECASE), (ART_DATEI, ART_CODE, ART_NOTIZ)),
    (re.compile(rf"\b{_BESTIMMER}\s+(komponente|baustein)\b{_BENANNT}", re.IGNORECASE),
     (ART_CODE, ART_DATEI, ART_NOTIZ)),
    (re.compile(r"\b(?:that|this|the)\s+(note|page)\b", re.IGNORECASE), (ART_NOTIZ,)),
    (re.compile(r"\b(?:that|this|the)\s+(file|script|module)\b", re.IGNORECASE),
     (ART_DATEI, ART_CODE, ART_NOTIZ)),
    (re.compile(r"\b(?:that|this|the)\s+(component|function|class|method)\b", re.IGNORECASE),
     (ART_CODE, ART_DATEI, ART_NOTIZ)),
    (re.compile(r"\b(dort|darin|da\s+drin|dorthin|daraus)\b", re.IGNORECASE),
     (ART_NOTIZ, ART_DATEI, ART_CODE)),
)
_LOESUNG = re.compile(
    r"\b((?:die|deine|unsere|der|den|dem)\s+(?:vorige|vorherige|letzte|alte|bisherige|"
    r"vorgeschlagene|erste)[nm]?\s+(?:l(?:ö|oe)sung|variante|version|idee|antwort|"
    r"vorschlag|ansatz|entwurf)|(?:den|deinen)\s+(?:vorschlag|ansatz|entwurf)|"
    r"so\s+wie\s+(?:besprochen|gesagt|vorgeschlagen|vorhin)|wie\s+vorhin|"
    r"the\s+(?:previous|last|earlier|first)\s+(?:solution|version|answer|approach|"
    r"suggestion|draft))\b", re.IGNORECASE)
_FORTSETZUNG = re.compile(
    r"\b(mach\w*\s+(?:da\s+|dort\s+)?weiter|weiter\s*machen|weitermachen|"
    r"wo\s+(?:waren|sind)\s+wir|wo\s+wir\s+aufgeh(?:ö|oe)rt\s+haben|"
    r"wo\s+wir\s+stehen\s+geblieben|kn(?:ü|ue)pf\w*\s+an|anzukn(?:ü|ue)pfen|"
    r"continue\s+where\s+we\s+left\s+off|pick\s+up\s+where|where\s+were\s+we)\b",
    re.IGNORECASE)
# Woran man erkennt, dass eine Frage auf Frueheres zurueckgreift.
_RUECKGRIFF = re.compile(
    r"\b(nochmal|noch\s+mal|vorhin|damals|gestern|letztes\s+mal|wie\s+gesagt|"
    r"wie\s+besprochen|erinnerst\s+du|wei(ss|ß)t\s+du\s+noch|hatte\s+ich|hatten\s+wir|"
    r"haben\s+wir|am\s+anfang|zuvor|again|earlier|remember)\b", re.IGNORECASE)


# ----------------------------------------------------------------- Bausteine
@dataclass
class Aufloesung:
    """Ein Rueckbezug in der Frage und worauf er vermutlich zeigt."""

    ausdruck: str
    ziele: List[Bezug] = field(default_factory=list)
    zug: Optional[Zug] = None
    antwort_bezug: bool = False         # "die vorige Loesung" -> eine Antwort

    @property
    def eindeutig(self) -> bool:
        return len(self.ziele) == 1 or self.antwort_bezug


@dataclass
class Widerspruch:
    neu: str
    alt: str
    herkunft: str
    zug: Optional[Zug] = None           # gesetzt, wenn aus dem Episodengedaechtnis


def _bezug_zeigen(bezug: Bezug) -> str:
    if bezug.art == ART_NOTIZ:
        return f"[[{bezug.name}]]"
    if bezug.art in (ART_DATEI, ART_CODE):
        return f"`{bezug.name}`"
    return f"„{bezug.name}“"


def _kurz(text: str, laenge: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= laenge else text[:laenge - 1].rstrip() + "…"


@dataclass
class Arbeitsgedaechtnis:
    """Was fuer EINE Frage zusammengetragen wurde. Fluechtig - nie gespeichert."""

    frage: str
    sitzung: str = ""
    bezuege: List[Aufloesung] = field(default_factory=list)
    vorgaben: List[Tuple[str, Zug]] = field(default_factory=list)
    widersprueche: List[Widerspruch] = field(default_factory=list)
    fortsetzung: List[str] = field(default_factory=list)
    erinnerungen: List[Treffer] = field(default_factory=list)
    ersetzt: dict = field(default_factory=dict)    # (zug.nr, satz) -> neue nr
    beleg: str = ""                     # "" | keine | teilweise | belegt
    beleglage: str = ""
    fehler: List[str] = field(default_factory=list)
    ausgelassen: int = 0

    @property
    def leer(self) -> bool:
        return not (self.bezuege or self.vorgaben or self.widersprueche or self.fortsetzung
                    or self.erinnerungen or self.beleglage or self.fehler)

    def suchzusatz(self) -> List[str]:
        """Namen eindeutig aufgeloester Bezuege - fuer die Suche in der Vault."""
        return [a.ziele[0].name for a in self.bezuege if a.ziele and len(a.ziele) == 1]

    # ------------------------------------------------------------ Darstellung
    def wann(self, zug: Zug) -> str:
        if zug.sitzung == self.sitzung:
            return f"Zug {zug.nr}, {zug.uhrzeit}"
        return f"Zug {zug.nr}, {zug.tag} {zug.uhrzeit}"

    def _posten(self) -> List[Tuple[int, str, str]]:
        """(Rang, Abschnitt, Zeile). Kleiner Rang = weicht zuletzt."""
        posten: List[Tuple[int, str, str]] = []
        for fehler in self.fehler:
            posten.append((0, "fehler", f"- {fehler}"))
        for w in self.widersprueche:
            posten.append((1, "widerspruch",
                           f"- Jetzt: „{_kurz(w.neu, 200)}“ – früher ({w.herkunft}): "
                           f"„{_kurz(w.alt, 200)}“"))
        if self.beleglage:
            posten.append((2, "beleglage", self.beleglage))
        for satz, zug in self.vorgaben:
            posten.append((3, "vorgaben", f"- „{_kurz(satz, 240)}“ ({self.wann(zug)})"))
        for a in self.bezuege:
            posten.append((4, "bezuege", self._bezugszeile(a)))
        for zeile in self.fortsetzung:
            posten.append((5, "fortsetzung", zeile))
        for t in self.erinnerungen:
            posten.append((6, "erinnerungen", self.erinnerungszeile(t.zug)))
        return posten

    def _bezugszeile(self, a: Aufloesung) -> str:
        if a.antwort_bezug and a.zug is not None:
            return (f"- „{a.ausdruck}“ → deine Antwort in {self.wann(a.zug)}: "
                    f"„{_kurz(a.zug.antwort, 300)}“")
        if len(a.ziele) == 1:
            return (f"- „{a.ausdruck}“ → vermutlich {_bezug_zeigen(a.ziele[0])} "
                    f"(zuletzt in {self.wann(a.zug)})" if a.zug else
                    f"- „{a.ausdruck}“ → vermutlich {_bezug_zeigen(a.ziele[0])}")
        namen = " oder ".join(_bezug_zeigen(b) for b in a.ziele[:3])
        return (f"- „{a.ausdruck}“ → unklar: {namen} – frag nach, bevor du etwas "
                f"veränderst.")

    def erinnerungszeile(self, zug: Zug) -> str:
        zusatz = ""
        ersetzte = [s for s in zug.aussagen + zug.vorgaben
                    if (zug.nr, s) in self.ersetzt]
        if ersetzte:
            zusatz = (f" [Angabe „{_kurz(ersetzte[0], 80)}“ später ersetzt in Zug "
                      f"{self.ersetzt[(zug.nr, ersetzte[0])]}]")
        antwort = f" → Du: „{_kurz(zug.antwort, 260)}“" if zug.antwort else ""
        return (f"- {self.wann(zug)}: Nutzer: „{_kurz(zug.frage, 260)}“{antwort}"
                f"{zusatz}")

    _KOPF = {
        "fehler": "HINWEIS ZUM GEDÄCHTNIS (frühere Gesprächsstellen können fehlen – "
                  "behaupte keine Erinnerung, die weder hier noch im Verlauf steht):",
        "widerspruch": "MÖGLICHER WIDERSPRUCH ZU FRÜHER GESAGTEM (die neuere Angabe gilt; "
                       "sag kurz, dass du sie übernimmst, und behandle nicht beide als "
                       "gleichzeitig gültig):",
        "beleglage": "BELEGLAGE:",
        "vorgaben": "GELTENDE VORGABEN DES NUTZERS IN DIESEM GESPRÄCH (gelten, bis er "
                    "etwas anderes sagt):",
        "bezuege": "BEZÜGE IN DER FRAGE (aus dem Verlauf erschlossen – eine Annahme, "
                   "keine Gewissheit):",
        "fortsetzung": "ANKNÜPFUNG (worauf sich „weitermachen“ bezieht):",
        "erinnerungen": "FRÜHERE GESPRÄCHSSTELLEN, DIE NICHT MEHR IM VERLAUF STEHEN "
                        "(Auswahl nach Relevanz, wörtlich gekürzt – nicht der "
                        "vollständige Verlauf):",
    }

    def block(self, budget: int, nur: Sequence[str] = ()) -> str:
        """Der Promptblock, hoechstens `budget` Zeichen. Leer, wenn nichts da ist."""
        posten = [p for p in self._posten() if not nur or p[1] in nur]
        if not posten:
            return ""
        kopf = "\nARBEITSGEDÄCHTNIS FÜR DIESE FRAGE (zusammengestellt aus dem Gespräch; " \
               "die Herkunft steht jeweils dabei):\n"
        belegt = len(kopf)
        genommen: List[Tuple[int, str, str]] = []
        abschnitte_drin: set = set()
        self.ausgelassen = 0
        for rang, abschnitt, zeile in sorted(posten, key=lambda p: p[0]):
            bedarf = len(zeile) + 1 + (0 if abschnitt in abschnitte_drin
                                       else len(self._KOPF[abschnitt]) + 1)
            if belegt + bedarf > budget:
                self.ausgelassen += 1
                continue
            genommen.append((rang, abschnitt, zeile))
            abschnitte_drin.add(abschnitt)
            belegt += bedarf
        if not genommen:
            return ""
        teile = [kopf.rstrip("\n")]
        for abschnitt in self._KOPF:
            zeilen = [z for _, a, z in genommen if a == abschnitt]
            if zeilen:
                teile.append(self._KOPF[abschnitt])
                teile.extend(zeilen)
        return "\n".join(teile) + "\n"

    def als_dict(self) -> dict:
        return {
            "bezuege": [{"ausdruck": a.ausdruck, "ziele": [b.name for b in a.ziele],
                         "eindeutig": a.eindeutig} for a in self.bezuege],
            "vorgaben": len(self.vorgaben),
            "widersprueche": [{"neu": w.neu, "alt": w.alt, "herkunft": w.herkunft}
                              for w in self.widersprueche],
            "erinnerungen": [t.zug.nr for t in self.erinnerungen],
            "fortsetzung": bool(self.fortsetzung),
            "beleg": self.beleg,
            "fehler": list(self.fehler),
            "ausgelassen": self.ausgelassen,
        }


# ------------------------------------------------------------------ Das Teil
class KontextwahlTeil:
    """Wird in JarvisAgent eingemischt - siehe agent.py."""

    _episoden_obj = None
    _arbeit: Optional[Arbeitsgedaechtnis] = None
    _gedaechtnis_gewarnt = ""

    @property
    def episoden(self) -> Episodengedaechtnis:
        if self._episoden_obj is None:
            self._episoden_obj = Episodengedaechtnis(self.cfg)
        return self._episoden_obj

    def _kontextwahl_an(self) -> bool:
        """Aus im Messstand (beide Kontrollschalter) und per EPISODEN_GEDAECHTNIS."""
        return (bool(getattr(self.cfg, "episoden_gedaechtnis", True))
                and not getattr(self, "gedaechtnis_aus", False)
                and not getattr(self, "wissen_aus", False))

    def _arbeitsbudget(self) -> int:
        roh = int(getattr(self.cfg, "prompt_char_budget", 14000) * ARBEIT_ANTEIL)
        return max(600, min(4000, roh))

    # ------------------------------------------------------------- Aufbauen
    def _arbeitsgedaechtnis_bauen(self, frage: str,
                                  weg: Optional[Weg] = None) -> Optional[Arbeitsgedaechtnis]:
        """Alle Schritte, jeder in seiner eigenen Schutzschicht. Wirft nie."""
        if not self._kontextwahl_an():
            return None
        speicher = self.episoden
        arbeit = Arbeitsgedaechtnis(frage=frage, sitzung=speicher.sitzung)
        try:
            speicher.zuege              # laedt beim ersten Mal
        except Exception as exc:        # noqa: BLE001
            arbeit.fehler.append(f"Episodengedaechtnis nicht ladbar: {type(exc).__name__}")
            self._gedaechtnis_warnen(arbeit)
            return arbeit
        if speicher.gestoert:
            arbeit.fehler.append(speicher.letzter_fehler)

        schritte = [self._bezuege_aufloesen, self._vorgaben_sammeln,
                    self._fortsetzung_finden, self._erinnern]
        for schritt in schritte:
            try:
                schritt(arbeit)
            except Exception as exc:    # noqa: BLE001
                arbeit.fehler.append(f"{schritt.__name__.strip('_')} misslang: "
                                     f"{type(exc).__name__}: {exc}")
        try:
            self._widersprueche_finden(arbeit, weg)
        except Exception as exc:        # noqa: BLE001
            arbeit.fehler.append(f"Widerspruchspruefung misslang: {type(exc).__name__}: {exc}")
        arbeit.ersetzt = dict(getattr(speicher, "_ersetzt", {}) or {})
        self._gedaechtnis_warnen(arbeit)
        return arbeit

    def _gedaechtnis_warnen(self, arbeit: Arbeitsgedaechtnis) -> None:
        """Ins Protokoll - je Fehlerlage einmal, nicht bei jeder Frage."""
        if not arbeit.fehler:
            return
        marke = " | ".join(arbeit.fehler)
        if self._gedaechtnis_gewarnt == marke:
            return
        self._gedaechtnis_gewarnt = marke
        try:
            self.tools.log.warnung("Arbeitsgedächtnis: " + marke, "gedaechtnis")
        except Exception:               # noqa: BLE001
            pass

    def _im_verlauf(self) -> set:
        """Nummern der Zuege dieser Sitzung, die noch im Verlauf stehen.

        Abgelegt wird die Frage samt "@Ordner"-Vorsatz, im Verlauf steht sie
        ohne - deshalb genuegt es, wenn die abgelegte mit der aus dem Verlauf
        ENDET.
        """
        fragen = [str(m.get("content") or "").strip() for m in self.history
                  if m.get("role") == "user"]
        return {z.nr for z in self.episoden.sitzungszuege()
                if any(f and (z.frage == f or z.frage.endswith(f)) for f in fragen)}

    def _runde_vermerken(self, arbeit: Optional[Arbeitsgedaechtnis]) -> None:
        """Was das Arbeitsgedaechtnis beigetragen hat - fuer Laufbericht und Messung."""
        if arbeit is None:
            return
        runde = self._runde()
        runde.erinnert = len(arbeit.erinnerungen)
        runde.bezuege = len(arbeit.bezuege)
        runde.widersprueche = len(arbeit.widersprueche)

    def _gedaechtnisluecke_melden(self, text: str, frage: str) -> str:
        """Ein gestoertes Gedaechtnis steht in der Antwort, nicht nur im Protokoll (R9).

        Sonst ist "ich erinnere mich nicht" von einem Fehler im Speicher nicht
        zu unterscheiden. Gemeldet wird, wenn die Frage zurueckgreift, und
        sonst einmal je Sitzung.
        """
        arbeit = getattr(self, "_arbeit", None)
        if arbeit is None or not arbeit.fehler:
            return text
        greift_zurueck = bool(_RUECKGRIFF.search(frage) or arbeit.bezuege)
        if not greift_zurueck and getattr(self, "_luecke_gemeldet", False):
            return text
        self._luecke_gemeldet = True
        return ((text or "").rstrip()
                + f"\n\n*Gedächtnis: Frühere Gesprächsstellen waren gerade nur "
                  f"eingeschränkt abrufbar ({_kurz(arbeit.fehler[0], 120)}). Was länger "
                  f"her ist, nenne bitte noch einmal.*")

    # --------------------------------------------------------------- Bezuege
    def _bezuege_aufloesen(self, arbeit: Arbeitsgedaechtnis) -> None:
        frage = arbeit.frage
        eigene = {b.art for b in bezuege_aus_text(frage, "frage")}
        kandidaten = self.episoden.letzte_bezuege()
        gesehen: set = set()
        for muster, arten in _VERWEISE:
            treffer = muster.search(frage)
            if not treffer or treffer.group(0).lower() in gesehen:
                continue
            if arten[0] in eigene:
                continue                # die Frage nennt das Ding selbst
            gesehen.add(treffer.group(0).lower())
            aufloesung = self._aufloesen(treffer.group(0), arten, kandidaten)
            if aufloesung is not None:
                arbeit.bezuege.append(aufloesung)

        loesung = _LOESUNG.search(frage)
        if loesung:
            mit_antwort = [z for z in self.episoden.zuege if z.antwort]
            if mit_antwort:
                erste = "erste" in loesung.group(0).lower() or "first" in loesung.group(0).lower()
                sitzung = [z for z in mit_antwort if z.sitzung == arbeit.sitzung] or mit_antwort
                zug = sitzung[0] if erste else sitzung[-1]
                arbeit.bezuege.append(Aufloesung(loesung.group(0), [], zug,
                                                 antwort_bezug=True))

    @staticmethod
    def _aufloesen(ausdruck: str, arten: Sequence[str],
                   kandidaten: Sequence[Tuple[Bezug, Zug]]) -> Optional[Aufloesung]:
        """Juengster Zug, der ein Ding der bevorzugten Art traegt.

        Innerhalb des Zugs zaehlen angefasste und genannte Dinge vor solchen,
        die nur in der Antwort vorkamen - eine Trefferliste mit fuenf Notizen
        ist kein Bezug auf fuenf Notizen. Bleiben mehrere, ist es unklar.
        """
        for art in arten:
            for bezug, zug in kandidaten:
                if bezug.art != art:
                    continue
                im_zug = [b for b, z in kandidaten if z is zug and b.art == art]
                fest = [b for b in im_zug if b.quelle in ("werkzeug", "frage")] or im_zug
                namen: List[Bezug] = []
                for b in fest:
                    if fold(b.name) not in {fold(n.name) for n in namen}:
                        namen.append(b)
                return Aufloesung(ausdruck, namen[:3], zug)
        return None

    # -------------------------------------------------------------- Vorgaben
    def _vorgaben_sammeln(self, arbeit: Arbeitsgedaechtnis) -> None:
        anzahl = int(getattr(self.cfg, "episoden_vorgaben", 8) or 8)
        arbeit.vorgaben = self.episoden.geltende_vorgaben(anzahl)

    # ----------------------------------------------------------- Widersprueche
    def _widersprueche_finden(self, arbeit: Arbeitsgedaechtnis,
                              weg: Optional[Weg]) -> None:
        """Neue Aussagen der Frage gegen fruehere des Nutzers und Sitzungsfakten.

        Verglichen wird nur, was als Aussage oder Vorgabe zu lesen ist - eine
        Frage widerspricht nichts. Je neuer Aussage zaehlt der JUENGSTE
        Widerspruch: Die aelteren hat schon eine fruehere Ersetzung erledigt.
        """
        vorgaben = set(vorgaben_finden(arbeit.frage))
        neue = []
        for satz in saetze(arbeit.frage):
            satz = " ".join(satz.split())
            aussage = zerlegen(satz)
            if aussage is not None and (aussage.beziehung != "befehl" or satz in vorgaben):
                neue.append(satz)
        if not neue:
            return
        bisher = self.episoden.nutzeraussagen()
        for satz in neue:
            for alt, zug in bisher:
                if vergleichen(satz, alt) == WIDERSPRUCH:
                    arbeit.widersprueche.append(Widerspruch(
                        satz, alt, f"Nutzer, {arbeit.wann(zug)}", zug))
                    break
            else:
                self._gegen_sitzungsfakten(arbeit, satz, weg)

    def _gegen_sitzungsfakten(self, arbeit: Arbeitsgedaechtnis, satz: str,
                              weg: Optional[Weg]) -> None:
        """Sitzungsfakten kommen ueber einen Vektorabruf - auf RECHERCHE und
        WERKZEUG gibt es keinen (gespraech/wege.py), auf DIREKT schon gar
        nicht (direkt.py: "keine Werkzeuge, kein Abruf"), auch nicht hierdurch.
        Gefunden von test_gespraech_triage: "Erledigt ist nicht die Antwort auf
        meine Frage" liest sich als Aussage und loeste einen Abruf aus.
        Der Abgleich gegen die Episoden (ohne Vektor) laeuft auf jedem Weg."""
        if weg in (Weg.RECHERCHE, Weg.WERKZEUG, Weg.DIREKT):
            return
        sitzung = getattr(self, "sitzung", None)
        if sitzung is None or not getattr(sitzung, "aktiv", False):
            return
        for fakt in sitzung.passende_fakten(satz) or []:
            if vergleichen(satz, fakt.text) == WIDERSPRUCH:
                stand = "bestätigt" if fakt.status == "bestaetigt" else "unbestätigt"
                arbeit.widersprueche.append(Widerspruch(
                    satz, fakt.text, f"Sitzung vom {fakt.tag}, aus dem Verlauf verdichtet, "
                                     f"{stand}"))
                return

    # ----------------------------------------------------------- Anknuepfung
    def _fortsetzung_finden(self, arbeit: Arbeitsgedaechtnis) -> None:
        """'Mach da weiter, wo wir aufgehoert haben' - am Anfang einer Sitzung.

        Mitten in einer Sitzung steht das Worauf im Verlauf; dann bleibt das
        hier leer. Gibt es keinen frueheren Stand, wird DAS gesagt - sonst
        erfindet das Modell einen plausiblen.
        """
        if not _FORTSETZUNG.search(arbeit.frage):
            return
        if self.episoden.sitzungszuege() and self.history:
            return
        vorher = self.episoden.vorige_sitzung()
        if not vorher:
            arbeit.fortsetzung.append("- Im Gedächtnis steht kein früheres Gespräch. "
                                      "Sag das, statt einen Stand zu erfinden.")
            return
        for zug in vorher[-3:]:
            arbeit.fortsetzung.append(arbeit.erinnerungszeile(zug))
        sitzung = getattr(self, "sitzung", None)
        try:
            offen = sitzung.offene_punkte(3) if sitzung is not None else []
        except Exception:               # noqa: BLE001
            offen = []
        for punkt in offen:
            arbeit.fortsetzung.append(f"- offen aus der Sitzung vom {punkt.tag}: {punkt.text}")

    # ------------------------------------------------------------ Erinnern
    def _erinnern(self, arbeit: Arbeitsgedaechtnis) -> None:
        anzahl = int(getattr(self.cfg, "episoden_im_prompt", 4) or 0)
        if anzahl <= 0:
            return
        arbeit.erinnerungen = self.episoden.abrufen(
            arbeit.frage, ausser=self._im_verlauf(), anzahl=anzahl,
            zusatz=arbeit.suchzusatz())
        # Chronologisch zeigen: Das Modell liest eine Geschichte, keine Rangliste.
        arbeit.erinnerungen.sort(key=lambda t: t.zug.nr)

    # ------------------------------------------------------------ Beleglage
    def _beleglage_setzen(self, arbeit: Optional[Arbeitsgedaechtnis], frage: str,
                          belege: Sequence[str]) -> None:
        """Deckt das Abgerufene die Frage? Nur Zaehlen, kein Urteil ueber Inhalt.

        "keine" heisst: Kein einziges tragendes Wort der Frage steht in dem,
        was abgerufen wurde. Dann steht das ausdruecklich im Prompt - VOR der
        Antwort. Die Pruefung danach (pruefung.py) sieht nach, ob es beachtet
        wurde.
        """
        if arbeit is None:
            return
        # Tragend ist, was mindestens vier Zeichen hat, eine Ziffer traegt oder
        # CJK ist - "hat", "neu", "gut" stehen in fast jeder Notiz.
        kern = [t for t in dict.fromkeys(tokens(frage)) if t not in ALLERWELTSWOERTER
                and (len(t) >= 4 or any(z.isdigit() for z in t) or ist_cjk_wort(t))]
        if not kern:
            return
        vorhanden = set(tokens(" ".join(b for b in belege if b)))
        gefunden = [t for t in kern if t in vorhanden]
        woerter = {fold(w): w for w in re.findall(r"\w+", frage)}
        fehlend = [woerter.get(t, t) for t in kern if t not in vorhanden]
        if not gefunden:
            arbeit.beleg = "keine"
            arbeit.beleglage = (f"Zu „{', '.join(fehlend[:4])}“ steht in den abgerufenen "
                                f"Notizen nichts. Sag das ausdrücklich, statt eine Antwort "
                                f"zu erfinden; was du aus allgemeinem Wissen ergänzt, "
                                f"kennzeichne als solches.")
        elif len(gefunden) * 2 < len(kern):
            arbeit.beleg = "teilweise"
            arbeit.beleglage = (f"Die abgerufenen Notizen decken die Frage nur teilweise "
                                f"ab – zu „{', '.join(fehlend[:4])}“ steht dort nichts. "
                                f"Trenne Belegtes von eigener Einschätzung.")
        else:
            arbeit.beleg = "belegt"

    # --------------------------------------------------------- Weitergeben
    def _arbeitsblock(self, nur: Sequence[str] = (), budget: Optional[int] = None) -> str:
        arbeit = getattr(self, "_arbeit", None)
        if arbeit is None:
            return ""
        block = arbeit.block(budget or self._arbeitsbudget(), nur)
        if isinstance(getattr(self, "letzter_kontext", None), dict) and not nur:
            self.letzter_kontext["arbeitsgedaechtnis"] = arbeit.als_dict()
        return block

    def _planer_kontext(self, context: str) -> str:
        """Fuer den Schreibplaner: Vorgaben, Bezuege und Widersprueche VOR den
        Notizen - der Planer schneidet seinen Kontext bei 2 500 Zeichen ab."""
        block = self._arbeitsblock(nur=("vorgaben", "bezuege", "widerspruch"), budget=900)
        return (block.strip() + "\n\n" + (context or "")) if block else context

    # ------------------------------------------------------------ Ablegen
    def _zug_ablegen(self, frage: str, antwort, weg: str = "") -> None:
        """Nach JEDER Runde, auf jedem Rueckweg. Wirft nie (R13)."""
        if not bool(getattr(self.cfg, "episoden_gedaechtnis", True)):
            return
        try:
            text = "" if getattr(antwort, "kind", "") == "fehler" else \
                str(getattr(antwort, "text", "") or "")
            zug = self.episoden.vermerken(frage, text, weg,
                                          list(getattr(antwort, "tool_calls", None) or []))
            arbeit = getattr(self, "_arbeit", None)
            if zug is not None and arbeit is not None:
                for w in arbeit.widersprueche:
                    if w.zug is not None:
                        self.episoden.ersetzung_vermerken(w.zug, w.alt, zug)
        except Exception as exc:        # noqa: BLE001
            try:
                self.tools.log.warnung(f"Zug nicht abgelegt: {type(exc).__name__}: {exc}",
                                       "gedaechtnis")
            except Exception:           # noqa: BLE001
                pass
