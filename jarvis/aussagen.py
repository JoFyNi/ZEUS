"""Aussagen vergleichen: dieselbe Sache, eine Aenderung - oder etwas anderes?

Anlass (Audit vom 12.10.2026)
-----------------------------
`sitzung._widerspruchsfrei` sollte von zwei Aussagen zum SELBEN Gegenstand
die aeltere aus dem Prompt nehmen. Gemessen wurde aber nur die Wortueberlappung
(Jaccard >= 0,5). Damit galten

    "Hauptmodell ist qwen3:32b auf dem KI-Rechner"
    "Rueckfallmodell ist qwen3:32b auf dem KI-Rechner"

als derselbe Gegenstand (0,6), und die zweite - richtige - Aussage verschwand.
Der Modulkopf von sitzung.py nannte genau dieses Paar als Beispiel dafuer, was
NICHT passieren darf.

Was dieses Modul kann - und was nicht
-------------------------------------
Es zerlegt kurze Aussagesaetze in (Gegenstand, Beziehung, Wert) und vergleicht
zwei davon. Das ist Mustererkennung an der Satzoberflaeche, kein Verstehen:

    "Das Hauptmodell ist qwen3:32b."        -> (hauptmodell | ist | qwen3 32b)
    "Ab sofort nutzen wir MySQL."           -> (wir | nutzt | mysql), ersetzend
    "Antworte immer auf Englisch."          -> (antwort | befehl | auf englisch)

Das Urteil ist eines von vier:

    GLEICH       dieselbe Aussage, nichts Neues
    WIDERSPRUCH  derselbe Gegenstand, unvereinbarer Wert - die neuere gilt
    ERGAENZUNG   derselbe Gegenstand, anderer Wert, aber beides kann stimmen
                 ("wir nutzen PostgreSQL" / "wir nutzen Redis")
    FREMD        verschiedene Gegenstaende

WIDERSPRUCH heisst bewusst nur: "kann nicht beides gleichzeitig gelten". Wann
das so ist, entscheidet die Art der Beziehung - "ist/heisst/betraegt" hat genau
einen Wert, "nutzt/hat" mehrere -, ein Ersetzungswort ("statt", "nicht mehr",
"ab sofort", "jetzt") oder eine umgedrehte Verneinung. Alles Unklare bleibt
ERGAENZUNG: Ein falsch erkannter Widerspruch verdraengt eine richtige
Erinnerung, ein uebersehener laesst nur zwei Zeilen nebeneinander stehen.
Geloescht wird auf Grund dieses Urteils nie etwas - es steuert, was in den
Prompt kommt und was dort als ueberholt markiert wird.

Unterstuetzt: Deutsch und Englisch, Saetze bis etwa 25 Woerter.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

from .vault import fold as _fold_roh, similarity

# Verglichen wird paarweise (Sitzungsfakten: jeder gegen jeden behaltenen) -
# dieselben Saetze werden also immer wieder gefaltet und zerlegt. Beides ist
# eine reine Funktion des Textes und darf gemerkt werden.
fold = lru_cache(maxsize=8192)(_fold_roh)

GLEICH = "gleich"
WIDERSPRUCH = "widerspruch"
ERGAENZUNG = "ergaenzung"
FREMD = "fremd"

# Beziehungen mit genau EINEM Wert: zwei verschiedene Werte schliessen sich aus.
_FUNKTIONAL = {"ist", "heisst", "betraegt", "kostet", "dauert", "liegt", "="}
# Alle uebrigen (nutzt, hat, laeuft, enthaelt, befehl) haben mehrere moegliche
# Werte: Ein anderer Wert ist dort eine Ergaenzung, kein Widerspruch.

# Satzverb -> Beziehungsklasse. Reihenfolge egal; gesucht wird das ERSTE
# Vorkommen im Satz, denn im deutschen Aussagesatz steht das Verb an zweiter
# Stelle und trennt Gegenstand und Wert.
_VERBEN = {
    "ist": "ist", "sind": "ist", "bin": "ist", "bist": "ist", "war": "ist", "waren": "ist", "wird": "ist",
    "bleibt": "ist", "is": "ist", "are": "ist", "was": "ist", "were": "ist",
    "heisst": "heisst", "heissen": "heisst", "lautet": "heisst", "lauten": "heisst",
    "betraegt": "betraegt", "betragen": "betraegt", "kostet": "kostet",
    "kosten": "kostet", "costs": "kostet", "dauert": "dauert",
    "liegt": "liegt", "liegen": "liegt", "equals": "=",
    "nutzt": "nutzt", "nutzen": "nutzt", "benutzt": "nutzt", "benutzen": "nutzt",
    "verwendet": "nutzt", "verwenden": "nutzt", "nehmen": "nutzt", "nimmt": "nutzt",
    "uses": "nutzt", "use": "nutzt", "using": "nutzt",
    "hat": "hat", "haben": "hat", "has": "hat", "have": "hat",
    "laeuft": "laeuft", "laufen": "laeuft", "runs": "laeuft", "run": "laeuft",
    "enthaelt": "enthaelt", "enthalten": "enthaelt", "contains": "enthaelt",
}

# Woerter, die am Gegenstand nichts aendern.
_ARTIKEL = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen",
            "einem", "einer", "the", "a", "an", "unser", "unsere", "unseren",
            "unserem", "unserer", "mein", "meine", "meinen", "meinem", "meiner",
            "dein", "deine", "our", "my", "your"}
# Fuellwoerter, die weder Gegenstand noch Wert tragen.
_FUELLE = {"jetzt", "nun", "doch", "inzwischen", "mittlerweile", "neuerdings",
           "eigentlich", "stattdessen", "lieber", "wieder", "schon", "auch", "aber",
           "ab", "sofort", "immer", "noch", "bitte", "also", "dann", "ja", "mal",
           "now", "instead", "actually", "still", "also", "please", "always",
           "grundsaetzlich", "kuenftig", "zukuenftig", "fortan", "mehr", "anymore",
           "longer"}
_VERNEINUNG = {"nicht", "kein", "keine", "keinen", "keinem", "keiner", "nie",
               "niemals", "not", "no", "never"}
# Woerter, mit denen der Sprecher sagt: das Neue ERSETZT das Alte. Die
# schwachen ("jetzt", "doch") zaehlen nur in Aussagesaetzen - in einer
# Anweisung ("erstelle jetzt eine Notiz") heisst "jetzt" bloss "sofort".
_ERSETZEND_STARK = re.compile(
    r"\b(statt|stattdessen|anstatt|nicht\s+mehr|kein\w*\s+\w+\s+mehr|ab\s+sofort|"
    r"ab\s+jetzt|von\s+nun\s+an|k(ue|ü)nftig|umgestellt|umgestiegen|gewechselt|"
    r"ersetzt|korrigier\w*|instead|no\s+longer|switched|from\s+now\s+on)\b",
    re.IGNORECASE)
_ERSETZEND_SCHWACH = re.compile(
    r"\b(jetzt|inzwischen|mittlerweile|neuerdings|doch|now)\b", re.IGNORECASE)
# Ein Gegenstand, der nur aus Hinweiswoertern besteht, ist keiner:
# "Das ist komisch" sagt nichts ueber eine Sache, die man sich merken koennte.
_HINWEIS = {"das", "dies", "dieses", "es", "er", "sie", "this", "that", "it",
            "was", "wer", "wie", "wo", "hier", "da", "dort", "alles", "nichts"}
_PRONOMEN = {"wir", "ich", "man", "we", "i"}
_ETIKETTEN = {"wichtig", "hinweis", "achtung", "info", "anmerkung", "beachte", "merke",
              "bitte", "ps", "nb", "vorgabe", "anforderung", "regel", "korrektur", "update",
              "important", "note", "remember", "fyi"}

# Kennzeichen: Modellnamen, Versionen, Zahlen - wie in sitzung._KENNZEICHEN_RE.
_KENNZEICHEN_RE = re.compile(r"[\w.:/-]*\d[\w.:/-]*")

_SATZENDE = re.compile(r"(?<=[.!?])\s+(?=[A-ZÄÖÜ\"„(])|\n+")


@dataclass(frozen=True)
class Aussage:
    text: str
    gegenstand: Tuple[str, ...]
    beziehung: str
    wert: Tuple[str, ...]
    verneint: bool = False
    ersetzend: bool = False
    # Nennt der Wert ein DING (Name, Zahl, Version) - oder eine Eigenschaft?
    # "ist PostgreSQL 16" gegen "ist MySQL 8" schliesst sich aus; "ist
    # wichtig" gegen "ist MySQL 8" nicht: Das eine ist eine Bewertung.
    benennend: bool = True

    @property
    def funktional(self) -> bool:
        return self.beziehung in _FUNKTIONAL


def saetze(text: str) -> List[str]:
    """Text -> Saetze. Dezimalzahlen und Versionen ("3.9", "qwen3.8:27b")
    bleiben ganz, weil nur an Satzzeichen MIT folgendem Grossbuchstaben
    getrennt wird."""
    return [s.strip() for s in _SATZENDE.split(text or "") if s and s.strip()]


def _woerter(text: str) -> List[str]:
    return fold(text).split()


def _benennt(rest: str) -> bool:
    """Steht im (ungefalteten) Wert ein Name, eine Zahl oder ein Zitat?

    Im Deutschen beginnen Substantive und Namen gross, Eigenschaftswoerter
    klein - die Schreibung traegt hier die Unterscheidung, die sonst eine
    Wortartbestimmung braeuchte. Nicht-lateinische Schrift gilt vorsichtshalber
    als benennend: Dort gibt es diese Regel nicht.
    """
    for wort in (rest or "").split():
        wort = wort.strip(".,;:!?()[]")
        if not wort:
            continue
        if (any(z.isdigit() for z in wort) or wort[0].isupper() or wort[0] in "\"„“'`"
                or (any(z.isalpha() for z in wort) and not wort.isascii()
                    and not re.fullmatch(r"[A-Za-zÄÖÜäöüß-]+", wort))):
            return True
    return False


def _rest_nach(satz: str, verb: str) -> str:
    """Der Originaltext hinter dem ersten Wort, das gefaltet `verb` ergibt."""
    worte = satz.split()
    for stelle, wort in enumerate(worte):
        if verb in fold(wort).split():
            return " ".join(worte[stelle + 1:])
    return satz


def _wert_woerter(text: str) -> Tuple[Tuple[str, ...], bool]:
    """Wert ohne Fuellwoerter; zweiter Teil: steckte eine Verneinung darin?

    "MySQL statt PostgreSQL" -> nur "mysql": Was hinter "statt" steht, ist
    der ALTE Wert, nicht Teil des neuen.
    """
    roh = _woerter(text)
    for stelle, wort in enumerate(roh):
        if wort in ("statt", "anstatt", "anstelle", "instead"):
            roh = roh[:stelle]
            break
    verneint = any(w in _VERNEINUNG for w in roh)
    wert = tuple(w for w in roh if w not in _FUELLE and w not in _VERNEINUNG
                 and w not in _ARTIKEL)
    return wert, verneint


@lru_cache(maxsize=4096)
def zerlegen(satz: str) -> Optional[Aussage]:
    """Ein Aussagesatz -> Aussage. None fuer Fragen und alles ohne Struktur."""
    satz = (satz or "").strip()
    if not satz or satz.endswith("?") or len(satz.split()) > 25:
        return None
    stark = bool(_ERSETZEND_STARK.search(satz))
    ersetzend = stark or bool(_ERSETZEND_SCHWACH.search(satz))
    woerter = _woerter(satz)
    if len(woerter) < 2:
        return None

    # 1. Ein Satzverb trennt Gegenstand und Wert.
    for stelle, wort in enumerate(woerter):
        beziehung = _VERBEN.get(wort)
        if beziehung is None:
            continue
        links = [w for w in woerter[:stelle] if w not in _ARTIKEL and w not in _FUELLE]
        rechts = woerter[stelle + 1:]
        verneint_links = any(w in _VERNEINUNG for w in links)
        links = [w for w in links if w not in _VERNEINUNG]
        # Verbzweitstellung mit Vorfeld: "Ab sofort nutzen WIR MySQL".
        if not links and rechts and rechts[0] in _PRONOMEN:
            links, rechts = [rechts[0]], rechts[1:]
        wert, verneint = _wert_woerter(" ".join(rechts))
        if not links or not wert or set(links) <= _HINWEIS:
            return None
        return Aussage(satz, tuple(links), beziehung, wert,
                       verneint or verneint_links, ersetzend,
                       _benennt(_rest_nach(satz, wort)))

    # 2. Doppelpunkt, Gleichheitszeichen oder Gedankenstrich als Kopula:
    #    "Hauptmodell: qwen3:32b", "Наша база данных — PostgreSQL" (im
    #    Russischen steht im Praesens kein "ist", sondern der Strich).
    teile = re.split(r"\s*(?:=|:\s|\s[—–]\s)\s*", satz, maxsplit=1)
    if len(teile) == 2 and teile[0] and teile[1]:
        links = [w for w in _woerter(teile[0]) if w not in _ARTIKEL]
        # "Wichtig: Wir nutzen PostgreSQL" - vor dem Doppelpunkt steht ein
        # Etikett, kein Gegenstand. Die Aussage ist der Rest.
        if links and set(links) <= _ETIKETTEN:
            return zerlegen(teile[1])
        wert, verneint = _wert_woerter(teile[1])
        if links and wert and len(links) <= 4 and not set(links) <= _HINWEIS:
            return Aussage(satz, tuple(links), "=", wert, verneint, ersetzend,
                           _benennt(teile[1]))

    # 3. Befehlsform: "Antworte immer auf Englisch." Das erste Wort ist das
    #    Verb; der Gegenstand ist die Taetigkeit selbst. Grob gestutzt, damit
    #    "antworte" und "antworten" zusammenfallen. Als Verb gilt nur, was wie
    #    eine Befehlsform aussieht - sonst waere "Python 3.12 fuer Falke" ein
    #    Befehl namens "python".
    erstes = woerter[0] if woerter[0] not in _FUELLE else (woerter[1] if len(woerter) > 1 else "")
    if _sieht_nach_befehl_aus(erstes):
        rest = woerter[woerter.index(erstes) + 1:]
        wert, verneint = _wert_woerter(" ".join(rest))
        if wert:
            return Aussage(satz, (re.sub(r"(en|e|t)$", "", erstes),), "befehl",
                           wert, verneint, stark)
    return None


# Kurze Befehlsformen ohne End-e. Die mit -e ("antworte", "nutze", "erstelle")
# erkennt die Endung.
_BEFEHLSSTAEMME = {"mach", "nimm", "gib", "lies", "schreib", "sag", "nutz", "halt",
                   "bleib", "lass", "geh", "zeig", "frag", "denk", "merk", "sprich",
                   "vermeid", "verwend", "antwort", "use", "write", "answer", "keep",
                   "make", "do", "avoid", "always", "never", "reply", "respond"}


def _sieht_nach_befehl_aus(wort: str) -> bool:
    if not wort or wort in _HINWEIS | _ARTIKEL | _PRONOMEN | _VERNEINUNG:
        return False
    return wort in _BEFEHLSSTAEMME or (len(wort) >= 4 and wort.endswith("e")
                                       and wort.isalpha() and wort.isascii())


def _kennzeichen(text: str) -> set:
    return {k for k in _KENNZEICHEN_RE.findall(fold(text)) if k}


def _gleicher_gegenstand(a: Aussage, b: Aussage) -> bool:
    if a.beziehung != b.beziehung and not (a.funktional and b.funktional):
        return False
    ga, gb = set(a.gegenstand), set(b.gegenstand)
    if ga == gb:
        return True
    gemeinsam = len(ga & gb) / max(1, len(ga | gb))
    # Hoch angesetzt: "hauptmodell" und "rueckfallmodell" teilen nichts, und
    # "das hauptmodell fuer den agenten" / "das hauptmodell" teilen das
    # Wesentliche nur, wenn eines ganz im anderen steckt.
    return gemeinsam >= 0.75 or ((ga <= gb or gb <= ga) and min(len(ga), len(gb)) >= 1
                                 and max(len(ga), len(gb)) <= 4)


def _gleicher_wert(a: Aussage, b: Aussage) -> bool:
    wa, wb = set(a.wert), set(b.wert)
    if wa == wb:
        return True
    if _kennzeichen(" ".join(a.wert)) != _kennzeichen(" ".join(b.wert)):
        return False
    gemeinsam = len(wa & wb) / max(1, len(wa | wb))
    return gemeinsam >= 0.8 or ((wa <= wb or wb <= wa) and len(wa & wb) >= 2)


@lru_cache(maxsize=8192)
def vergleichen(neu: str, alt: str) -> str:
    """Urteil ueber zwei Aussagen; `neu` ist die juengere. Siehe Modulkopf."""
    if fold(neu) == fold(alt):
        return GLEICH
    a, b = zerlegen(neu), zerlegen(alt)
    if a is not None and b is not None:
        if not _gleicher_gegenstand(a, b):
            return FREMD
        if _gleicher_wert(a, b):
            return GLEICH if a.verneint == b.verneint else WIDERSPRUCH
        if a.verneint != b.verneint and set(a.wert) & set(b.wert):
            return WIDERSPRUCH
        if a.beziehung == "befehl":
            # Bei Anweisungen ist das Wie der Inhalt ("antworte kurz") - die
            # Schreibung sagt dort nichts. Es entscheidet das Ersetzungswort.
            return WIDERSPRUCH if a.ersetzend else ERGAENZUNG
        if not (a.benennend and b.benennend):
            return ERGAENZUNG           # eine Eigenschaft, kein anderes Ding
        if a.funktional or a.ersetzend:
            return WIDERSPRUCH
        return ERGAENZUNG
    return _ohne_struktur(neu, alt)


def _ohne_struktur(neu: str, alt: str) -> str:
    """Rueckfall fuer Saetze ohne erkennbare Struktur: Kennzeichen-Regel.

    Fast derselbe Satz mit anderen Kennzeichen ("Python 3.9 fuer das Projekt"
    / "Python 3.12 fuer das Projekt") ist eine Aenderung. Fast derselbe Satz
    mit denselben Kennzeichen ist eine Dublette. Alles andere ist FREMD -
    lieber zwei Zeilen im Prompt als eine verlorene.
    """
    ka, kb = _kennzeichen(neu), _kennzeichen(alt)
    ohne_a = set(_woerter(_KENNZEICHEN_RE.sub(" ", neu))) - _FUELLE - _ARTIKEL
    ohne_b = set(_woerter(_KENNZEICHEN_RE.sub(" ", alt))) - _FUELLE - _ARTIKEL
    if not ohne_a or not ohne_b:
        return FREMD
    gemeinsam = len(ohne_a & ohne_b) / len(ohne_a | ohne_b)
    if ka and kb and ka != kb and gemeinsam >= 0.6 and len(ohne_a & ohne_b) >= 2:
        return WIDERSPRUCH
    if ka == kb and similarity(neu, alt) > 0.88:
        return GLEICH
    return FREMD
