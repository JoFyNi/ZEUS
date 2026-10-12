"""Triage im Gespraech: Welcher Weg traegt diese Eingabe - VOR dem Vektorabruf?

Der Anlass (claude/PROMPT_Antwortpflicht_und_Triage.md, M4): `ask()` durchsuchte
fuer JEDE Eingabe zuerst die Vault - fuer "Hallo" genauso wie fuer
"reflektiere deine Aktionen". Die Antwort auf eine Meta-Frage steht aber nie in
den Notizen, sondern im Gespraech. Der Abruf war dort nicht nur teuer, er
lenkte das Modell in die Notizen und damit von der Frage weg.

Das hier ist KEINE zweite Einordnung
------------------------------------
Einordnung und Strategiewahl gibt es fertig und getestet in `jarvis/kern/`:
der `Aufgabenbewerter` (bewertung.py) und die `Strategieweiche` (router.py).
Dieses Modul ist der duenne Adapter davor und dahinter:

    Eingabe -> REGELN (eindeutige Faelle, ohne Modell)
            -> Aufgabenbewerter -> Strategieweiche
            -> WEGTABELLE (Befund/Entscheidung -> Weg)

Die Regelschicht deckt nur ab, was der Bewerter fuer den Gespraechspfad nicht
kennt: Rueckbezuege auf die eigene letzte Antwort ("und?", "das war keine
Antwort"), Reflexion ueber das eigene Verhalten und vault-fremdes Weltwissen
ohne ausdrueckliches Netzwort. Alles andere entscheidet der Bewerter.

Vorrang bei Mehrdeutigkeit - in dieser Reihenfolge, siehe `einordnen()`:

    1. ausdruecklicher [werkzeug]-Aufruf    -> wie bisher
       (1b. kurze Zustimmung zum eigenen Vorschlag -> SCHREIBEN, wie bisher)
    2. Meta-/Reflexionsanteil               -> DIREKT (ggf. danach SCHREIBEN)
    3. Schreibauftrag                       -> SCHREIBEN
    4. einzelne deterministische Auskunft   -> WERKZEUG
    5. Netzbezug oder vault-fremdes Wissen  -> RECHERCHE
    6. sonst                                -> VAULT

Ein Gruss mit Sachfrage ("Hallo, was steht an?") ist KEIN DIREKT: Der Gruss
entscheidet nur, wenn er die ganze Eingabe ist - so liest ihn auch der
Bewerter (`_GESPRAECH` verlangt die ganze Zeile).

Rueckfall bei Unsicherheit ist VAULT - nie DIREKT, denn DIREKT ohne Grund
hiesse raten. Jeder Rueckfall traegt `quelle="rueckfall"`, damit messbar
bleibt, wie oft die Einordnung nicht wusste, was sie vor sich hat.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..kern.bewertung import (ABRUF, ANALYSE, Aufgabenbewerter, Befund, FRAGE,
                              GESPRAECH, LESEN, META, SCHREIBEN as A_SCHREIBEN,
                              UMBAUEN, WARTUNG, ZAEHLEN)
from ..kern.router import Entscheidung, Strategieweiche, WERKZEUG as S_WERKZEUG


class Weg(str, Enum):
    DIREKT = "direkt"
    VAULT = "vault"
    RECHERCHE = "recherche"
    SCHREIBEN = "schreiben"
    WERKZEUG = "werkzeug"


REGEL = "regel"
BEWERTER = "bewerter"
RUECKFALL = "rueckfall"


@dataclass
class TriageErgebnis:
    weg: Weg
    begruendung: str
    quelle: str                                 # regel | bewerter | rueckfall
    folge: Optional[Weg] = None                 # nur DIREKT -> SCHREIBEN
    ausdruecklich: bool = False                 # [werkzeug]-Aufruf
    befund: Optional[Befund] = None
    entscheidung: Optional[Entscheidung] = None
    notizen: List[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.weg.value + (f"+{self.folge.value}" if self.folge else "")


# ============================================================ Regelschicht
# Trennbare Verben wie in bewertung._SCHREIBEN: Die Vorsilbe steht am
# Satzende ("denk mal ueber dein Verhalten NACH"), deshalb `.{0,40}?\bnach\b`
# statt der Vorsilbe direkt hinter dem Verb. Flexion ueber Wortstaemme
# ohne abschliessendes \b (siehe bewertung._ZERSTOEREND, warum).
_REFLEXION = re.compile(
    r"\b(reflektier|(ue|ü)berdenk|"
    r"denk\w*\s+.{0,40}?\bnach\b|"
    r"(warum|wieso|weshalb)\s+(hast|bist|konntest|wolltest|machst|tust|"
    r"antwortest|sagst|liest|suchst)\s+du|"
    r"was\s+(hast|machst|tust)\s+du\s+(da|gerade|eben|zuletzt|dabei)|"
    r"erkl(ae|ä)r\w*\s+.{0,30}?\b(deine?n?|dein)\s+"
    r"(entscheidung|vorgehen|antwort|verhalten|schritte|aktion)|"
    r"wie\s+funktionierst\s+du|wer\s+bist\s+du|"
    r"was\s+kannst\s+du(?!\s+(mir\s+)?(\w+\s+)?(über|ueber|zu|von)\b)|"
    r"welche\s+(werkzeuge|f(ae|ä)higkeiten|wege)\s+(hast|kennst)\s+du|"
    r"deine\s+(aktionen|antworten|f(ae|ä)higkeiten|werkzeuge|grenzen|"
    r"konfiguration|einstellungen|arbeitsweise|regeln)|"
    r"(anfangs|system|start)[-\s]?prompt|\bdein(en)?\s+prompt)",
    re.IGNORECASE)

# Klagen ueber die eigene letzte Antwort. Sie entscheiden IMMER fuer DIREKT:
# Wer sagt "das war keine Antwort", will keine neue Suche, sondern eine
# Antwort - und die Erklaerung, warum die letzte keine war.
_KLAGE = re.compile(
    r"\b((keine|nicht\s+die|nicht\s+meine)\s+antwort|"
    r"nicht\s+(geantwortet|beantwortet)|"
    r"beantworte\s+(doch\s+)?(endlich\s+)?(meine|die)\s+frage|"
    r"wo\s+ist\s+(der\s+sinn|die\s+antwort)|"
    r"du\s+(hast|hattest)\s+(mir\s+)?nicht\s+geantwortet)"
    r"|^\s*erledigt\s+ist\s+nicht", re.IGNORECASE)

# Kurze Rueckfragen. Nur mit Verlauf DIREKT - ohne vorige Antwort beziehen
# sie sich auf nichts, und dann ist die Vault der ehrlichere Versuch.
_RUECKFRAGE = re.compile(
    r"^\s*(und|und\s+jetzt|und\s+weiter|na\s+und|ja\s+und|also|"
    r"was\s+hei(ss|ß)t\s+das|was\s+bedeutet\s+das|wie\s+meinst\s+du\s+das|"
    r"was\s+meinst\s+du(\s+damit)?|erkl(ae|ä)r\s+das(\s+genauer)?|"
    r"warum|wieso|weshalb|echt|wirklich|sicher|genauer|mehr\s+dazu|"
    r"das\s+verstehe\s+ich\s+nicht)\s*[?!.]*\s*$", re.IGNORECASE)

# Weltwissen, das nicht aus eigenen Notizen stammen kann - auch ohne
# "online" im Satz. Der Anlass steht in werkzeuge/netz.py: "Allplan
# Systemvoraussetzungen" wurde in der Vault gesucht und als Recherche
# ausgegeben. Bewusst eng: Ein zu weites Muster schickt eigene Fragen ins Netz.
_VAULTFREMD = re.compile(
    r"\b(systemvoraussetzung|systemanforderung|hardwareanforderung|"
    r"mindestanforderung|hersteller|was\s+kostet|preis\s+(von|f(ue|ü)r)|"
    r"aktuellste?n?\s+version|neueste?n?\s+version|release|wetter|"
    r"nachrichten|recherchier|googl|such\w*\s+.{0,30}?\bim\s+(netz|internet|web))",
    re.IGNORECASE)
_EIGENES = re.compile(r"\b(notiz|vault|meine[nmrs]?\s+(notizen|unterlagen|aufgaben))",
                      re.IGNORECASE)

# "Was kannst du mir ueber Transformer sagen?" ist eine Sachfrage. Das
# Meta-Muster des Bewerters (`bewertung._META`, "was kannst du") liest sie als
# Frage ueber Zeus. Korrigiert wird das HIER, im Adapter - `jarvis/kern/`
# bleibt unangetastet (siehe Auftrag §3.0); gemeldet ist es trotzdem.
_SACHFRAGE_KANNST = re.compile(
    r"\bwas\s+kannst\s+du\s+(mir\s+)?(\w+\s+)?(über|ueber|zu|von)\b", re.IGNORECASE)

# WARTUNG ist nur dann EINE Auskunft, wenn nichts repariert werden soll.
_REPARIEREN = re.compile(r"\b(reparier|bereinig|entfern|korrigier|behebe?|fix)",
                         re.IGNORECASE)


# ============================================================= Wegtabelle
# Die EINE Stelle, an der Bewerter- und Weichenausgabe zu einem Weg wird.
# Absicht -> (Weg, Bedingung an die Weiche). Eine Absicht, die hier fehlt,
# ist ein Fehler, den `test_gespraech_triage` findet - kein stiller VAULT.
WEGTABELLE = {
    GESPRAECH: Weg.DIREKT,
    META: Weg.DIREKT,
    ZAEHLEN: Weg.WERKZEUG,        # nur wenn die Weiche WERKZEUG sagt
    WARTUNG: Weg.WERKZEUG,        # dito, und nur ohne Reparaturauftrag
    A_SCHREIBEN: Weg.SCHREIBEN,
    UMBAUEN: Weg.SCHREIBEN,
    ABRUF: Weg.VAULT,             # auch wenn die Weiche WERKZEUG sagt:
    LESEN: Weg.VAULT,             #   Gelesenes braucht Synthese
    ANALYSE: Weg.VAULT,
    FRAGE: Weg.VAULT,             # der Default des Bewerters -> Rueckfall
}


def weg_aus_befund(befund: Befund, entscheidung: Optional[Entscheidung]):
    """Befund + Entscheidung -> (Weg, Quelle, Begruendung). Ohne Seiteneffekt."""
    absicht = befund.absicht
    if absicht == META and _SACHFRAGE_KANNST.search(befund.auftrag):
        return Weg.VAULT, RUECKFALL, "'was kannst du ueber ...' ist eine Sachfrage"
    weg = WEGTABELLE[absicht]
    strategie = getattr(entscheidung, "strategie", "")

    if befund.netz_noetig and weg in (Weg.VAULT, Weg.WERKZEUG):
        return Weg.RECHERCHE, BEWERTER, "der Bewerter sieht einen Netzbezug"
    if weg is Weg.WERKZEUG:
        if strategie != S_WERKZEUG:
            return Weg.VAULT, RUECKFALL, (f"'{absicht}', aber die Weiche waehlt "
                                          f"'{strategie}' statt eines Werkzeugs")
        if absicht == WARTUNG and _REPARIEREN.search(befund.auftrag):
            return Weg.SCHREIBEN, BEWERTER, "Wartung mit Reparaturauftrag"
        return Weg.WERKZEUG, BEWERTER, f"'{absicht}' mit einem Werkzeug loesbar"
    if absicht == FRAGE:
        return Weg.VAULT, RUECKFALL, "keine Handlung erkennbar - erst in der Vault nachsehen"
    if befund.mehrdeutigkeit >= Aufgabenbewerter.SCHWELLE_NACHFRAGEN:
        return weg, RUECKFALL, (f"'{absicht}', aber mehrdeutig "
                                f"({befund.mehrdeutigkeit:.2f})")
    return weg, BEWERTER, f"'{absicht}' ({strategie or 'ohne Weiche'})"


class Triage:
    """Waehlt den Weg. Wirft nie - im Zweifel VAULT mit quelle='rueckfall'."""

    def __init__(self, cfg=None, bewerter=None, weiche=None,
                 werkzeugnamen=None) -> None:
        self.cfg = cfg
        self.bewerter = bewerter or Aufgabenbewerter(
            llm=None, cfg=cfg, werkzeugnamen=werkzeugnamen, modell_erlaubt=False)
        self.weiche = weiche or Strategieweiche(register=None, cfg=cfg)
        # Darf der Bewerter bei echter Unklarheit das kleine Modell fragen?
        # Vorgabe nein: Determinismus vor KI, und der Rueckfall (VAULT) ist
        # genau der Weg, den ask() vor der Triage fuer alles nahm.
        self.modell_fragen = bool(getattr(cfg, "triage_modell", False))

    def einordnen(self, text: str, *, verlauf: bool = False,
                  ausdrueckliche_rufe: bool = False,
                  schreibauftrag: bool = False,
                  wiederholung: bool = False,
                  bestaetigung: bool = False) -> TriageErgebnis:
        """Die Vorrangregeln aus dem Modulkopf, in genau dieser Reihenfolge.

        `schreibauftrag` und `wiederholung` kommen vom Aufrufer, weil sie
        dort schon mit den Mustern des Schreibplaners bestimmt werden
        (`looks_like_write_request`, `_ist_wiederholung`). Zweimal dasselbe
        Muster an zwei Stellen waere eine zweite Einordnung.
        """
        text = str(text or "").strip()

        # 1. Ausdruecklicher Aufruf: Der Nutzer hat entschieden.
        if ausdrueckliche_rufe:
            return TriageErgebnis(Weg.WERKZEUG, "ausdruecklicher [werkzeug]-Aufruf",
                                  REGEL, ausdruecklich=True)

        # 1b. Zustimmung zu einem eigenen Vorschlag ("ja, mach das", "ok").
        #     Kein Gruss, sondern eine Handlung - weiter auf dem alten Pfad.
        if bestaetigung:
            return TriageErgebnis(Weg.SCHREIBEN, "Zustimmung zum eigenen Vorschlag",
                                  REGEL)

        # 2. Meta, Reflexion, Rueckbezug.
        meta = self._meta_grund(text, verlauf)
        if meta:
            folge = Weg.SCHREIBEN if self._schreibt_auch(text) else None
            return TriageErgebnis(Weg.DIREKT, meta, REGEL, folge=folge)

        # 3. Schreibauftrag (Muster des Schreibplaners, unveraendert).
        if schreibauftrag or wiederholung:
            return TriageErgebnis(Weg.SCHREIBEN,
                                  "Wiederholung eines gescheiterten Plans" if wiederholung
                                  else "Schreibauftrag", REGEL)

        # 5 (vor dem Bewerter, weil er es nicht kennt): vault-fremdes Wissen.
        if _VAULTFREMD.search(text) and not _EIGENES.search(text):
            return TriageErgebnis(Weg.RECHERCHE, "Weltwissen, das nicht in der "
                                  "Vault stehen kann", REGEL)

        # 4-6 ueber Bewerter und Weiche.
        try:
            befund = self.bewerter.bewerten(text, nachfragen=self.modell_fragen)
        except Exception as exc:
            return TriageErgebnis(Weg.VAULT, f"Bewerter warf {type(exc).__name__}",
                                  RUECKFALL)
        try:
            entscheidung = self.weiche.waehlen(befund)
        except Exception:
            entscheidung = None
        weg, quelle, grund = weg_aus_befund(befund, entscheidung)
        if befund.quelle != "regeln" and quelle == BEWERTER:
            grund += " (Modell half)"
        return TriageErgebnis(weg, grund, quelle, befund=befund,
                              entscheidung=entscheidung)

    # ------------------------------------------------------------ Innen
    def _meta_grund(self, text: str, verlauf: bool) -> str:
        if _KLAGE.search(text):
            return "Klage ueber die eigene letzte Antwort"
        if _REFLEXION.search(text):
            return "Frage ueber das eigene Verhalten oder die eigene Konfiguration"
        if verlauf and _RUECKFRAGE.match(text):
            return "Rueckfrage zur eigenen letzten Antwort"
        # Gruss, Dank, "wer bist du" - so wie der Bewerter sie liest.
        try:
            befund = self.bewerter.bewerten(text, nachfragen=False)
        except Exception:
            return ""
        if befund.absicht == META and _SACHFRAGE_KANNST.search(text):
            return ""
        if befund.absicht in (GESPRAECH, META):
            return ("Gruss oder Dank" if befund.absicht == GESPRAECH
                    else "Frage ueber Zeus selbst")
        return ""

    def _schreibt_auch(self, text: str) -> bool:
        """Kombinierter Auftrag: 'reflektiere ... und notiere das'?

        Geprueft wird der Rest OHNE den Meta-Anteil - sonst haelt der
        Bewerter "erklaere deine Entscheidung" wegen "erklaere" fuer etwas
        anderes, und "notiere" geht unter.
        """
        rest = _KLAGE.sub(" ", _REFLEXION.sub(" ", text))
        rest = re.sub(r"^\W*(und|dann|danach)\b", " ", rest.strip(), flags=re.I)
        if len(rest.split()) < 1:
            return False
        try:
            return self.bewerter.bewerten(rest, nachfragen=False).absicht == A_SCHREIBEN
        except Exception:
            return False
