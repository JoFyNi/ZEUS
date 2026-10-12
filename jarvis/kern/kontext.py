"""Der Kontext eines Schritts - zusammengestellt, nicht zusammengeklebt.

§23 zaehlt auf, was vor einem Denkschritt bereitstehen soll: System, Nutzer,
Ziel, Aufgabe, Projekt, Gedaechtnis, Wissen, frueher Ergebnisse, Artefakte,
Werkzeuge, Regeln, Randbedingungen, Pruefkriterien. Dreizehn Quellen.

Die naheliegende Umsetzung - alles aneinanderhaengen - ist genau die, an der
dieses Projekt am 18.09. gescheitert ist. Gemessen wurde damals: 32 846
Zeichen vor dem ersten Werkzeugaufruf, 46 934 nach dem fuenften, bei 13 517
nutzbaren. Ollama schneidet den Ueberhang VORNE ab, also dort, wo die
Anweisungen stehen. Das Modell verlor dadurch genau das Ergebnis, das es
gerade geholt hatte, und las dieselbe Notiz noch einmal.

Deshalb ist dieses Modul kein Sammler, sondern ein AUSWAEHLER. Es arbeitet
mit zwei Festlegungen:

1. **Jeder Block hat einen Rang.** Wird der Platz knapp, fallen die hinteren
   weg - und zwar ganz, nicht halb. Ein angeschnittener Wissensauszug ist
   schlimmer als gar keiner: Er sieht vollstaendig aus.

2. **Was weggelassen wurde, wird GESAGT.** Nicht dem Modell zuliebe, sondern
   fuer die Fehlersuche. "Warum hat er die Notiz nicht gekannt?" ist sonst
   nicht zu beantworten.

Die Rangfolge ist keine Geschmacksfrage, sondern folgt daraus, was ohne den
Block schiefgeht:

    auftrag       ohne ihn tut er das Falsche
    schritt       ohne ihn tut er alles auf einmal
    erwartung     ohne sie weiss niemand, wann es reicht
    bedingungen   ohne sie verletzt er Vorgaben, die der Nutzer genannt hat
    bisher        ohne es wiederholt er, was schon getan ist
    fehler        ohne ihn versucht er noch einmal, was eben scheiterte
    wissen        ohne es erfindet er
    gedaechtnis   ohne es fragt er noch einmal, was er schon weiss
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

# Voreinstellung fuer den Platz. Bewusst kleiner als das Prompt-Budget des
# Gespraechs: Hier kommen Werkzeugliste und Anweisung noch dazu.
PLATZ = 6000


@dataclass
class Block:
    name: str
    rang: int
    text: str

    @property
    def laenge(self) -> int:
        return len(self.text)


@dataclass
class Kontext:
    """Die ausgewaehlten Bloecke plus die Auskunft, was fehlt."""
    bloecke: List[Block] = field(default_factory=list)
    ausgelassen: List[str] = field(default_factory=list)
    platz: int = PLATZ

    def text(self) -> str:
        return "\n\n".join(f"{b.name.upper()}:\n{b.text}"
                           for b in self.bloecke if b.text.strip())

    def als_dict(self) -> dict:
        return {"bloecke": {b.name: b.laenge for b in self.bloecke},
                "zeichen": sum(b.laenge for b in self.bloecke),
                "platz": self.platz,
                "ausgelassen": list(self.ausgelassen)}

    def bericht(self) -> str:
        teile = [f"{b.name} {b.laenge}" for b in self.bloecke]
        if self.ausgelassen:
            teile.append("ausgelassen: " + ", ".join(self.ausgelassen))
        return ", ".join(teile)


class Kontextwerk:
    """Stellt den Kontext fuer EINEN Schritt zusammen - innerhalb des Platzes.

    Alle Quellen sind optional. Fehlt eine (kein Index, kein Sitzungs-
    gedaechtnis, kein Speicher), faellt ihr Block ersatzlos weg, statt dass
    etwas wirft - dieselbe Regel wie ueberall: Ein fehlendes Nebenteil darf
    die Arbeit nicht aufhalten.
    """

    def __init__(self, speicher=None, index=None, sitzung=None, lernwerk=None,
                 platz: int = PLATZ) -> None:
        self.speicher = speicher
        self.index = index
        self.sitzung = sitzung
        self.lernwerk = lernwerk
        self.platz = max(500, int(platz))

    # ---------------------------------------------------------------- Bauen
    def fuer_schritt(self, aufgabe, ziel=None,
                     platz: Optional[int] = None) -> Kontext:
        roh: List[Block] = []
        _an(roh, "auftrag", 1, str(getattr(ziel, "beschreibung", "")
                                   or aufgabe.auftrag or aufgabe.titel)[:600])
        _an(roh, "dieser schritt", 2, str(aufgabe.titel)[:300])
        daten = aufgabe.daten or {}
        _an(roh, "woran man erfolg erkennt", 3,
            str(daten.get("erwartung") or aufgabe.beschreibung
                or getattr(ziel, "ergebnis", "") or "")[:300])
        _an(roh, "randbedingungen", 4, self._bedingungen(ziel))
        _an(roh, "was bisher geschah", 5, self._bisher(aufgabe))
        _an(roh, "was schon erfolglos war", 6, self._fehler(aufgabe))
        _an(roh, "aus der vault", 7, self._wissen(aufgabe, ziel))
        _an(roh, "aus dem gedaechtnis", 8, self._gedaechtnis(aufgabe, ziel))

        return self._auswaehlen(roh, platz if platz is not None else self.platz)

    def _auswaehlen(self, bloecke: Sequence[Block], platz: int) -> Kontext:
        """Nach Rang aufnehmen, bis der Platz voll ist. Ganz oder gar nicht."""
        kontext = Kontext(platz=platz)
        belegt = 0
        for block in sorted(bloecke, key=lambda b: b.rang):
            # +2 fuer die Trennzeile, + Ueberschrift
            bedarf = block.laenge + len(block.name) + 4
            if belegt + bedarf > platz and kontext.bloecke:
                kontext.ausgelassen.append(block.name)
                continue
            kontext.bloecke.append(block)
            belegt += bedarf
        return kontext

    # -------------------------------------------------------------- Quellen
    @staticmethod
    def _bedingungen(ziel) -> str:
        werte = list(getattr(ziel, "bedingungen", None) or [])
        return "\n".join(f"- {w}" for w in werte[:8])

    def _bisher(self, aufgabe) -> str:
        """Die Ergebnisse der Geschwisterschritte - der wichtigste Block.

        Ohne ihn liest das Modell dieselbe Notiz zum dritten Mal. Genau das
        steht im Protokoll vom 18.09.
        """
        if not (self.speicher and aufgabe.eltern_id):
            return ""
        eigene = int((aufgabe.daten or {}).get("generation") or 1)
        zeilen: List[str] = []
        for kind in self.speicher.kinder(aufgabe.eltern_id):
            if kind.id == aufgabe.id or not kind.ergebnis:
                continue
            if int((kind.daten or {}).get("generation") or 1) != eigene:
                continue
            nummer = (kind.daten or {}).get("nummer")
            marke = f"{nummer}. " if nummer else "- "
            zeilen.append(f"{marke}{kind.titel[:70]}: {kind.ergebnis[:400]}")
        return "\n".join(zeilen[-8:])

    @staticmethod
    def _fehler(aufgabe) -> str:
        teile: List[str] = []
        if aufgabe.fehler:
            teile.append(f"zuletzt gescheitert: {aufgabe.fehler[:300]}")
        versucht = list((aufgabe.daten or {}).get("gescheiterte_aufrufe") or [])
        if versucht:
            teile.append("bereits erfolglos versucht:\n"
                         + "\n".join(f"- {v[:160]}" for v in versucht[-5:]))
        return "\n".join(teile)

    def _wissen(self, aufgabe, ziel) -> str:
        """Was in der Vault zum Schritt passt. Nur Auszuege, mit Herkunft."""
        if self.index is None:
            return ""
        frage = f"{aufgabe.titel} {getattr(ziel, 'beschreibung', '') or ''}".strip()
        if not frage:
            return ""
        try:
            treffer = self.index.search(frage, k=3)
        except Exception:
            return ""
        zeilen: List[str] = []
        for eintrag in treffer or []:
            quelle = (getattr(eintrag, "title", None)
                      or getattr(eintrag, "stem", None)
                      or (eintrag.get("title") if isinstance(eintrag, dict) else "")
                      or "?")
            text = (getattr(eintrag, "text", None)
                    or getattr(eintrag, "page_content", None)
                    or (eintrag.get("text") if isinstance(eintrag, dict) else "")
                    or "")
            if not str(text).strip():
                continue
            zeilen.append(f"[[{quelle}]]: {str(text).strip()[:300]}")
        return "\n".join(zeilen)

    def _gedaechtnis(self, aufgabe, ziel) -> str:
        """Sitzungstatsachen und Lektionen - beides knapp und nur passend."""
        frage = f"{aufgabe.titel} {getattr(ziel, 'beschreibung', '') or ''}".strip()
        zeilen: List[str] = []
        # Beide Quellen in eigenen Schutzschichten: Ein abgeschaltetes
        # Sitzungsgedaechtnis oder ein leeres Lernwerk darf den Schritt nicht
        # aufhalten - es fehlt dann eben ein Block.
        try:
            if self.sitzung is not None:
                for fakt in (self.sitzung.passende_fakten(frage) or [])[:4]:
                    text = getattr(fakt, "text", None) or str(fakt)
                    zeilen.append(f"- {str(text)[:200]}")
        except Exception:
            pass
        try:
            if self.lernwerk is not None:
                for lektion in (self.lernwerk.lektionen() or [])[:3]:
                    zeilen.append(f"- {str(lektion)[:200]}")
        except Exception:
            pass
        return "\n".join(zeilen)


def _an(ziel: List[Block], name: str, rang: int, text: str) -> None:
    text = str(text or "").strip()
    if text:
        ziel.append(Block(name=name, rang=rang, text=text))
