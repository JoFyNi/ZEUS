"""Blockweise Bearbeitung: eine Notiz an EINER Stelle ändern.

Was schon da war und in der Aufgabe als fehlend angenommen wurde
-----------------------------------------------------------------
Der dreistufige Pfad ist zu zwei Dritteln gebaut, nur an anderer Stelle:

* **Stufe 1, Schnappschuss.** `VaultTools._track()` sichert JEDE betroffene
  Datei vor der Änderung nach `.jarvis/backups/`, und `transaktion()` macht
  aus mehreren Änderungen eine Einheit mit Rollback. `rueckgaengig()` spielt
  sie zurück.
* **Stufe 3, Bearbeitung.** `notiz_bearbeiten` (Suchen/Ersetzen),
  `abschnitt_ersetzen`, `abschnitt_einfuegen`, `abschnitt_loeschen` und
  `notiz_ueberschreiben` gibt es seit längerem. Angehängt wird nur von
  `notiz_ergaenzen`.

Was wirklich fehlte, und was hier steht
---------------------------------------
1. **Block-Adressierung.** Bisher lässt sich ein Abschnitt nur über seine
   ÜBERSCHRIFT ansprechen. Ein einzelner Absatz unter einer Überschrift, eine
   Aufzählung oder ein Codeblock hatte keine Adresse. Genau darum geht es in
   `parse_blocks`/`block_id`: Überschriftenebene plus Absatzzähler.
2. **Diff-Vorschau.** `CONFIRM_WRITES` stand in der Konfiguration und wurde
   NIRGENDS ausgewertet - eine Einstellung, die nichts tut, ist schlimmer als
   keine, weil man sich auf sie verlässt. Hier entsteht der Unterschied als
   Text; die Rückfrage stellt die Oberfläche.

Bewusst NICHT hier: das Schreiben selbst. Das läuft weiter über
`VaultTools._write()` mit Rücklesekontrolle, Backup und Journal. Ein zweiter
Schreibpfad wäre ein zweiter Ort, an dem dieselben Sicherungen vergessen
werden können.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

UEBERSCHRIFT_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
ZAUN_RE = re.compile(r"^\s*(```|~~~)")


class AbortEdit(RuntimeError):
    """Der Benutzer hat die Änderung abgelehnt."""


@dataclass
class Block:
    """Ein adressierbares Stück einer Notiz."""

    nummer: int                    # 1-basiert, das ist die Block-ID
    art: str                       # ueberschrift | absatz | liste | code | zitat
    text: str
    von: int                       # erste Quellzeile (1-basiert)
    bis: int                       # letzte Quellzeile
    ebene: int = 0                 # Überschriftenebene, 0 wenn keine
    ueberschrift: str = ""         # unter welcher Überschrift der Block steht

    @property
    def adresse(self) -> str:
        """Menschenlesbare Adresse - für Anzeige und Fehlermeldungen."""
        ort = f"unter „{self.ueberschrift}“" if self.ueberschrift else "am Anfang"
        return f"[{self.nummer}] {self.art} {ort}, Zeile {self.von}–{self.bis}"

    def vorschau(self, zeichen: int = 90) -> str:
        einzeilig = " ".join(self.text.split())
        return einzeilig if len(einzeilig) <= zeichen else einzeilig[:zeichen] + "…"


def parse_blocks(text: str) -> List[Block]:
    """Zerlegt eine Notiz in adressierbare Blöcke.

    Ein Block ist, was durch Leerzeilen getrennt zusammensteht: eine
    Überschrift, ein Absatz, eine Aufzählung, ein Codeblock, ein Zitat.
    Codeblöcke bleiben am Stück - eine leere Zeile INNERHALB von ``` trennt
    nichts, sonst zerfällt jedes Beispiel in Bruchstücke, und eine Änderung
    daran würde die Zäune zerreißen.

    Die Nummerierung ist 1-basiert und stabil, solange die Notiz unverändert
    ist. Sie ist ausdrücklich KEINE dauerhafte Kennung: Wird ein Block
    eingefügt, verschieben sich die folgenden Nummern. Deshalb gehört zwischen
    Anzeige und Änderung immer ein Blick - genau dafür gibt es die Vorschau.
    """
    zeilen = text.splitlines()
    bloecke: List[Block] = []
    puffer: List[str] = []
    start = 0
    im_code = False
    zaun = ""
    aktuelle_ueberschrift = ""

    def abschliessen(ende: int) -> None:
        nonlocal puffer, start
        if not puffer or not any(z.strip() for z in puffer):
            puffer = []
            return
        roh = "\n".join(puffer).rstrip()
        bloecke.append(Block(
            nummer=len(bloecke) + 1, art=_art_bestimmen(roh), text=roh,
            von=start + 1, bis=ende, ueberschrift=aktuelle_ueberschrift,
        ))
        puffer = []

    for nummer, zeile in enumerate(zeilen):
        if ZAUN_RE.match(zeile):
            marke = ZAUN_RE.match(zeile).group(1)
            if not im_code:
                abschliessen(nummer)
                start = nummer
                im_code, zaun = True, marke
                puffer = [zeile]
                continue
            if marke == zaun:
                puffer.append(zeile)
                abschliessen(nummer + 1)
                im_code, zaun = False, ""
                continue
        if im_code:
            puffer.append(zeile)
            continue

        kopf = UEBERSCHRIFT_RE.match(zeile)
        if kopf:
            abschliessen(nummer)
            aktuelle_ueberschrift = kopf.group(2).strip()
            bloecke.append(Block(
                nummer=len(bloecke) + 1, art="ueberschrift", text=zeile.rstrip(),
                von=nummer + 1, bis=nummer + 1, ebene=len(kopf.group(1)),
                ueberschrift=aktuelle_ueberschrift,
            ))
            start = nummer + 1
            continue

        if not zeile.strip():
            abschliessen(nummer)
            start = nummer + 1
            continue

        if not puffer:
            start = nummer
        puffer.append(zeile)

    abschliessen(len(zeilen))
    return bloecke


def _art_bestimmen(text: str) -> str:
    erste = text.lstrip().splitlines()[0] if text.strip() else ""
    if ZAUN_RE.match(erste):
        return "code"
    if re.match(r"^\s*([-*+]|\d+[.)])\s", erste):
        return "liste"
    if erste.startswith(">"):
        return "zitat"
    if re.match(r"^\s*\|", erste):
        return "tabelle"
    return "absatz"


def serialize(bloecke: Sequence[Block], original: str = "") -> str:
    """Blöcke wieder zu einer Notiz zusammensetzen.

    Zwischen Blöcken steht genau eine Leerzeile - außer nach einer
    Überschrift, wo Markdown ohnehin keine verlangt, und vor der ersten.
    Ein abschließender Zeilenumbruch bleibt erhalten, wenn das Original
    einen hatte: sonst erzeugt jede Bearbeitung eine Diff-Zeile, die
    niemand geschrieben hat.
    """
    teile: List[str] = []
    for block in bloecke:
        text = block.text.rstrip()
        if not text:
            continue
        teile.append(text)
    ergebnis = "\n\n".join(teile)
    if original.endswith("\n") or not original:
        ergebnis += "\n"
    return ergebnis


def block_finden(bloecke: Sequence[Block], kennung) -> Optional[Block]:
    """Block über seine Nummer holen - nachsichtig gegenüber '[3]' und '3.'."""
    if isinstance(kennung, Block):
        return kennung
    text = str(kennung).strip().strip("[]").rstrip(".")
    try:
        nummer = int(text)
    except (TypeError, ValueError):
        return None
    for block in bloecke:
        if block.nummer == nummer:
            return block
    return None


# ==================================================================== Diff
def unterschied(vorher: str, nachher: str, name: str = "Notiz",
                rahmen: int = 2) -> str:
    """Der Unterschied als Text - das, was vor dem Schreiben gezeigt wird.

    Bewusst als einfacher Zeilen-Diff mit '-' und '+'. Ein Wort-Diff sieht im
    Terminal hübscher aus und lässt genau die Änderung übersehen, auf die es
    ankommt: eine gelöschte Zeile.
    """
    a = vorher.splitlines()
    b = nachher.splitlines()
    if a == b:
        return "(keine Änderung)"
    zeilen = list(difflib.unified_diff(a, b, fromfile=f"{name} (vorher)",
                                       tofile=f"{name} (nachher)",
                                       lineterm="", n=rahmen))
    return "\n".join(zeilen) if zeilen else "(keine Änderung)"


def unterschied_knapp(vorher: str, nachher: str) -> str:
    """Eine Zeile Bilanz: wie viel kommt weg, wie viel dazu."""
    a, b = vorher.splitlines(), nachher.splitlines()
    matcher = difflib.SequenceMatcher(None, a, b)
    entfernt = hinzu = 0
    for art, i1, i2, j1, j2 in matcher.get_opcodes():
        if art in ("replace", "delete"):
            entfernt += i2 - i1
        if art in ("replace", "insert"):
            hinzu += j2 - j1
    if not entfernt and not hinzu:
        return "keine Änderung"
    return f"−{entfernt} Zeilen, +{hinzu} Zeilen"


@dataclass
class Aenderungsvorschau:
    """Was passieren WÜRDE - erzeugt, bevor irgendetwas geschrieben ist."""

    notiz: str
    pfad: str
    vorher: str
    nachher: str
    block: Optional[Block] = None
    diagnose: List[str] = field(default_factory=list)

    @property
    def leer(self) -> bool:
        return self.vorher == self.nachher

    @property
    def bilanz(self) -> str:
        return unterschied_knapp(self.vorher, self.nachher)

    def diff(self, rahmen: int = 2) -> str:
        return unterschied(self.vorher, self.nachher, self.notiz, rahmen)

    def bericht(self) -> str:
        if self.leer:
            return f"An „{self.notiz}“ würde sich nichts ändern."
        zeilen = [f"**{self.notiz}** · {self.bilanz}"]
        if self.block is not None:
            zeilen.append(f"*{self.block.adresse}*")
        zeilen += ["", "```diff", self.diff(), "```"]
        return "\n".join(zeilen)


def block_ersetzen_text(inhalt: str, kennung, neuer_text: str) -> Tuple[str, Optional[Block], str]:
    """Reine Textumformung: Block ersetzen, ohne zu schreiben.

    Gibt (neuer Inhalt, betroffener Block, Fehlermeldung) zurück. Getrennt vom
    Schreiben, damit sich die Vorschau erzeugen lässt, ohne dass etwas
    passiert - das ist die Voraussetzung für eine ehrliche Rückfrage.
    """
    bloecke = parse_blocks(inhalt)
    if not bloecke:
        return inhalt, None, "Die Notiz enthält keine adressierbaren Blöcke."
    block = block_finden(bloecke, kennung)
    if block is None:
        verfuegbar = ", ".join(str(b.nummer) for b in bloecke[:20])
        return inhalt, None, (
            f"Block {kennung} gibt es nicht. Vorhanden sind: {verfuegbar}"
            + (" …" if len(bloecke) > 20 else "")
            + ". `bloecke_anzeigen` listet sie mit Vorschau.")
    neu = list(bloecke)
    ersetzt = Block(nummer=block.nummer, art=_art_bestimmen(neuer_text),
                    text=str(neuer_text).rstrip(), von=block.von, bis=block.bis,
                    ebene=block.ebene, ueberschrift=block.ueberschrift)
    neu[block.nummer - 1] = ersetzt
    return serialize(neu, inhalt), block, ""
