"""Abläufe zeichnen - im Code, nicht im Modell.

Josh ist visueller Lerner: er will Strukturen SEHEN, nicht lesen. Der
naheliegende Weg wäre, das Modell "mal ein Diagramm zeichnen" zu lassen. Das
scheitert aus demselben Grund wie alles andere in diesem Projekt (siehe
ENTWICKLUNG.md, Muster 2.3): Ein mittelgroßes Modell hält keine mehrstufige
Konstruktion durch. Es produziert Kästen, die nicht schließen, Pfeile, die
irgendwo enden, und beim zweiten Aufruf ein völlig anderes Layout.

Deshalb hier die Arbeitsteilung, die im Rest des Systems auch gilt:

    Das Modell liefert die STRUKTUR als eine Zeile je Kante.
    Das LAYOUT rechnet Python - immer gleich, immer geschlossen.

Damit ist eine Zeichnung reproduzierbar: dieselbe Beschreibung ergibt dasselbe
Bild, und ein Fehler im Bild ist ein Fehler in der Beschreibung, nicht im
Zufall der Generierung.

Eingabeformat (das, was das Modell schreiben muss):

    titel: Messkette
    richtung: ab

    (Maschine1) -> [Connector]
    (Maschine2) -> [Connector]
    [Connector] -> [Verarbeitung]: Rohwerte
    [Verarbeitung] -> {Grenzwert?}
    {Grenzwert?} -> [Datenbank]: ok
    {Grenzwert?} -> [Alarm]: ueberschritten

Klammern geben die Form: (rund) = Anfang/Ende, [eckig] = Schritt,
{geschweift} = Entscheidung, <spitz> = Ein-/Ausgabe. Ohne Klammern ein Schritt.

Ausgabe in drei Formen aus DEMSELBEN Layout:

    als_zeichen()  Unicode-Kästen für den Chat - überall lesbar, auch im Terminal
    als_svg()      Vektorbild für die Oberfläche
    als_mermaid()  Quelltext für Obsidian, das Mermaid selbst rendert

Das Layout ist ein abgespecktes Sugiyama-Verfahren: Ebenen über den längsten
Weg, Rückwärtskanten aus der Ebenenrechnung genommen, Zwischenknoten für
Kanten über mehrere Ebenen, Baryzentrum gegen Kreuzungen, dann rechtwinklige
Führung auf eigenen Spuren. Kein Paket, keine Schrift, keine Netzverbindung.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set, Tuple

# ------------------------------------------------------------------ Grenzen
MAX_KNOTEN = 60          # darüber wird kein Bild mehr gelesen, sondern gestarrt
MAX_BESCHRIFTUNG = 26    # längere Namen sprengen jede Zeilenbreite
MAX_KANTEN = 120

PFEIL_RE = re.compile(r"\s*(?:-{1,3}>|={1,2}>|→|->)\s*")
DIREKTIVE_RE = re.compile(r"^\s*(titel|richtung|title|direction)\s*:\s*(.+?)\s*$", re.I)

FORMEN = {
    "(": ("rund", ")"),
    "[": ("kasten", "]"),
    "{": ("raute", "}"),
    "<": ("schraeg", ">"),
}

# Farben aus der Oberfläche (:root in oberflaeche.html). Bewusst hier
# wiederholt statt importiert: das Bild muss auch im Terminal, in einer
# Notiz und in einer Datei ohne die Oberfläche stimmen.
FARBEN = {
    "grund": "#0f131b",
    "kasten": "#151b26",
    "linie": "#4b6386",
    "text": "#d7e0ee",
    "eis": "#5fd3f5",
    "gruen": "#57d9a3",
    "bernstein": "#e8b04b",
    "lila": "#a98bff",
}
FORM_FARBE = {
    "rund": FARBEN["gruen"],
    "kasten": FARBEN["eis"],
    "raute": FARBEN["bernstein"],
    "schraeg": FARBEN["lila"],
}


class DarstellungsFehler(ValueError):
    """Die Beschreibung ist nicht zeichenbar - mit einem Grund, den man liest."""


@dataclass
class Knoten:
    schluessel: str
    beschriftung: str
    form: str = "kasten"
    ebene: int = 0
    spalte: int = 0
    platzhalter: bool = False   # Zwischenknoten für eine lange Kante

    @property
    def breite_zeichen(self) -> int:
        return 1 if self.platzhalter else len(self.beschriftung) + 4


@dataclass
class Kante:
    von: str
    nach: str
    beschriftung: str = ""
    rueckwaerts: bool = False


@dataclass
class Graph:
    knoten: Dict[str, Knoten] = field(default_factory=dict)
    kanten: List[Kante] = field(default_factory=list)
    titel: str = ""
    richtung: str = "ab"

    # ------------------------------------------------------------- Aufbau
    def knoten_sichern(self, roh: str) -> str:
        """Legt den Knoten an, falls neu, und gibt seinen Schlüssel zurück."""
        beschriftung, form = _form_lesen(roh)
        schluessel = _schluessel(beschriftung)
        if not schluessel:
            raise DarstellungsFehler("Ein Knoten ohne Namen lässt sich nicht zeichnen.")
        vorhanden = self.knoten.get(schluessel)
        if vorhanden:
            # Eine spätere Zeile darf die Form nachschärfen: erst "Connector",
            # später "{Connector}" - die ausdrückliche Form gewinnt.
            if form != "kasten" and vorhanden.form == "kasten":
                vorhanden.form = form
            return schluessel
        if len(self.knoten) >= MAX_KNOTEN:
            raise DarstellungsFehler(
                f"Mehr als {MAX_KNOTEN} Knoten - das liest niemand mehr. "
                f"Teile den Ablauf in Abschnitte.")
        self.knoten[schluessel] = Knoten(schluessel, beschriftung, form)
        return schluessel

    def kante_sichern(self, von: str, nach: str, beschriftung: str = "") -> None:
        if len(self.kanten) >= MAX_KANTEN:
            raise DarstellungsFehler(f"Mehr als {MAX_KANTEN} Verbindungen.")
        if von == nach:
            # Eine Schleife auf sich selbst ist zeichenbar, aber in einem
            # Ebenenlayout immer hässlich - und fast immer ein Tippfehler.
            raise DarstellungsFehler(
                f"'{self.knoten[von].beschriftung}' zeigt auf sich selbst.")
        for k in self.kanten:
            if k.von == von and k.nach == nach:
                if beschriftung and not k.beschriftung:
                    k.beschriftung = beschriftung
                return
        self.kanten.append(Kante(von, nach, beschriftung))


# ==================================================================== Lesen
def aus_text(beschreibung: str, titel: str = "", richtung: str = "") -> Graph:
    """Liest die Kantenschreibweise ein. Wirft mit Begründung, wenn es nicht geht."""
    graph = Graph(titel=titel.strip(), richtung=(richtung or "ab").strip().lower())
    zeilen = str(beschreibung or "").splitlines()
    gelesen = 0
    unlesbar: List[str] = []

    for roh in zeilen:
        zeile = roh.strip()
        if not zeile or zeile.startswith("#") or zeile.startswith("//"):
            continue
        # ```mermaid / graph TD - kommt vor, wenn das Modell in Mermaid denkt.
        if zeile.startswith("```") or re.match(r"^(graph|flowchart)\b", zeile, re.I):
            continue

        direktive = DIREKTIVE_RE.match(zeile)
        if direktive and not PFEIL_RE.search(zeile):
            schluessel = direktive.group(1).lower()
            wert = direktive.group(2).strip()
            if schluessel in ("titel", "title") and not graph.titel:
                graph.titel = wert[:80]
            elif schluessel in ("richtung", "direction"):
                graph.richtung = "rechts" if wert.lower().startswith(("r", "l")) else "ab"
            continue

        zeile = re.sub(r"^\s*\d+[.)]\s*", "", zeile)   # "1. A -> B"
        zeile = re.sub(r"^\s*[-*+]\s+(?=\S)", "", zeile)  # "- A -> B"

        if not PFEIL_RE.search(zeile):
            # Eine Zeile ohne Pfeil ist eine reine Knotenerklärung.
            try:
                graph.knoten_sichern(zeile)
                gelesen += 1
            except DarstellungsFehler:
                unlesbar.append(roh.strip()[:60])
            continue

        zeile, beschriftung = _kantenbeschriftung_abtrennen(zeile)
        teile = [t.strip() for t in PFEIL_RE.split(zeile) if t.strip()]
        if len(teile) < 2:
            unlesbar.append(roh.strip()[:60])
            continue
        try:
            vorher = graph.knoten_sichern(teile[0])
            for index, roh_ziel in enumerate(teile[1:], 1):
                ziel = graph.knoten_sichern(roh_ziel)
                # Bei einer Kette gehört die Beschriftung an den LETZTEN Pfeil:
                # "A -> B -> C: fertig" beschreibt den Schritt nach C.
                graph.kante_sichern(
                    vorher, ziel,
                    beschriftung if index == len(teile) - 1 else "")
                vorher = ziel
            gelesen += 1
        except DarstellungsFehler as exc:
            unlesbar.append(f"{roh.strip()[:44]} ({exc})")

    if not graph.knoten:
        hinweis = (" Unlesbar war: " + "; ".join(unlesbar[:3])) if unlesbar else ""
        raise DarstellungsFehler(
            "Keine Struktur erkannt. Erwartet wird eine Verbindung je Zeile, "
            'z. B. "Messung -> Verarbeitung" oder "{Grenzwert?} -> [Alarm]: '
            'ueberschritten".' + hinweis)
    if not graph.kanten:
        # Ein einzelner Kasten ist kein Bild, sondern ein Wort mit Rahmen. Und
        # genau das kam heraus, wenn das Modell statt der Kantenschreibweise
        # einen Fließtext übergab: eine Zeile Prosa wurde zu einem Knoten, das
        # Werkzeug meldete Erfolg, und niemand sah, dass nichts erkannt wurde.
        raise DarstellungsFehler(
            f"{len(graph.knoten)} Knoten erkannt, aber keine einzige Verbindung - "
            f"dann ist es kein Ablauf. Jede Zeile braucht einen Pfeil: 'A -> B'.")
    return graph


def _kantenbeschriftung_abtrennen(zeile: str) -> Tuple[str, str]:
    """Trennt ': Beschriftung' am Zeilenende ab - ohne ein '{Grenzwert?}' zu zerreißen."""
    tiefe = 0
    for i, zeichen in enumerate(zeile):
        if zeichen in "([{<":
            tiefe += 1
        elif zeichen in ")]}>":
            tiefe = max(0, tiefe - 1)
        elif zeichen == "|" and tiefe == 0:
            # Mermaid-Schreibweise A -->|ja| B
            rest = zeile[i + 1:]
            if "|" in rest:
                beschriftung, _, hinten = rest.partition("|")
                return (zeile[:i] + " " + hinten), beschriftung.strip()[:40]
    tiefe = 0
    for i in range(len(zeile) - 1, -1, -1):
        zeichen = zeile[i]
        if zeichen in ")]}>":
            tiefe += 1
        elif zeichen in "([{<":
            tiefe = max(0, tiefe - 1)
        elif zeichen == ":" and tiefe == 0:
            return zeile[:i], zeile[i + 1:].strip()[:40]
    return zeile, ""


def _form_lesen(roh: str) -> Tuple[str, str]:
    text = str(roh).strip()
    form = "kasten"
    if text and text[0] in FORMEN:
        art, schluss = FORMEN[text[0]]
        if text.endswith(schluss):
            form = art
            text = text[1:-1].strip()
    text = re.sub(r"\s+", " ", text).strip(" \t\"'")
    if len(text) > MAX_BESCHRIFTUNG:
        text = text[: MAX_BESCHRIFTUNG - 1].rstrip() + "…"
    return text, form


def _schluessel(beschriftung: str) -> str:
    return re.sub(r"\s+", " ", beschriftung.strip().lower())


# ================================================================== Layout
def _layout(graph: Graph) -> Tuple[List[List[Knoten]], List[Kante]]:
    """Ebenen bilden, Zwischenknoten einziehen, Reihenfolge gegen Kreuzungen.

    Rückgabe: die Ebenen von oben nach unten, und die Kantenliste NACH dem
    Einziehen der Zwischenknoten (also nur noch Kanten zwischen benachbarten
    Ebenen - das braucht die Führung unten).
    """
    _rueckwaerts_markieren(graph)
    vorwaerts = [k for k in graph.kanten if not k.rueckwaerts]

    # --- Ebenen über den längsten Weg ---
    eingang: Dict[str, int] = {s: 0 for s in graph.knoten}
    nachfolger: Dict[str, List[str]] = {s: [] for s in graph.knoten}
    for kante in vorwaerts:
        eingang[kante.nach] += 1
        nachfolger[kante.von].append(kante.nach)

    ebene: Dict[str, int] = {s: 0 for s in graph.knoten}
    warteschlange = [s for s in graph.knoten if eingang[s] == 0]
    offen = dict(eingang)
    besucht = 0
    while warteschlange:
        aktuell = warteschlange.pop(0)
        besucht += 1
        for ziel in nachfolger[aktuell]:
            ebene[ziel] = max(ebene[ziel], ebene[aktuell] + 1)
            offen[ziel] -= 1
            if offen[ziel] == 0:
                warteschlange.append(ziel)
    if besucht < len(graph.knoten):
        # Kann nach dem Entfernen der Rückwärtskanten eigentlich nicht mehr
        # passieren. Falls doch: nicht abstürzen, sondern hinten anhängen.
        rest = max(ebene.values(), default=0) + 1
        for schluessel in graph.knoten:
            if offen.get(schluessel, 0) > 0:
                ebene[schluessel] = rest

    for schluessel, wert in ebene.items():
        graph.knoten[schluessel].ebene = wert

    # --- Zwischenknoten für Kanten über mehr als eine Ebene ---
    knoten = dict(graph.knoten)
    gefuehrt: List[Kante] = []
    zaehler = 0
    for kante in graph.kanten:
        start, ziel = kante.von, kante.nach
        von_ebene, nach_ebene = ebene[start], ebene[ziel]
        if kante.rueckwaerts or nach_ebene <= von_ebene:
            # Rückwärts- und Querkanten bleiben als eine Kante stehen; sie
            # werden gestrichelt gezeichnet und nicht durchs Layout geführt.
            gefuehrt.append(Kante(start, ziel, kante.beschriftung, rueckwaerts=True))
            continue
        vorher = start
        for zwischen_ebene in range(von_ebene + 1, nach_ebene):
            zaehler += 1
            hilfsname = f"\x00zwischen{zaehler}"
            knoten[hilfsname] = Knoten(hilfsname, "", "kasten",
                                       ebene=zwischen_ebene, platzhalter=True)
            gefuehrt.append(Kante(vorher, hilfsname,
                                  kante.beschriftung if vorher == start else ""))
            vorher = hilfsname
        gefuehrt.append(Kante(vorher, ziel,
                              kante.beschriftung if vorher == start else ""))

    # --- Reihenfolge in den Ebenen ---
    tiefe = max((k.ebene for k in knoten.values()), default=0)
    ebenen: List[List[Knoten]] = [[] for _ in range(tiefe + 1)]
    for schluessel in knoten:
        ebenen[knoten[schluessel].ebene].append(knoten[schluessel])

    direkt = [k for k in gefuehrt if not k.rueckwaerts]
    for _ in range(4):
        _baryzentrum(ebenen, direkt, knoten, abwaerts=True)
        _baryzentrum(ebenen, direkt, knoten, abwaerts=False)

    for reihe in ebenen:
        for spalte, eintrag in enumerate(reihe):
            eintrag.spalte = spalte
    return ebenen, gefuehrt


def _rueckwaerts_markieren(graph: Graph) -> None:
    """Findet Zyklen und nimmt je Zyklus eine Kante aus der Ebenenrechnung.

    Ohne das hätte ein Regelkreis (Messung -> Regler -> Stellglied -> Messung)
    keine gültige Ebenenzuweisung, und die Berechnung liefe nie fertig. Die
    Kante verschwindet nicht - sie wird gestrichelt zurückgezeichnet.
    """
    nachfolger: Dict[str, List[Kante]] = {s: [] for s in graph.knoten}
    for kante in graph.kanten:
        kante.rueckwaerts = False
        nachfolger[kante.von].append(kante)

    farbe: Dict[str, int] = {s: 0 for s in graph.knoten}   # 0 weiss, 1 grau, 2 schwarz

    def gehen(start: str) -> None:
        stapel: List[Tuple[str, int]] = [(start, 0)]
        farbe[start] = 1
        while stapel:
            aktuell, index = stapel[-1]
            if index < len(nachfolger[aktuell]):
                stapel[-1] = (aktuell, index + 1)
                kante = nachfolger[aktuell][index]
                if farbe[kante.nach] == 1:
                    kante.rueckwaerts = True      # zurück auf einen grauen: Zyklus
                elif farbe[kante.nach] == 0:
                    farbe[kante.nach] = 1
                    stapel.append((kante.nach, 0))
            else:
                farbe[aktuell] = 2
                stapel.pop()

    for schluessel in graph.knoten:
        if farbe[schluessel] == 0:
            gehen(schluessel)


def _baryzentrum(ebenen: List[List[Knoten]], kanten: List[Kante],
                 knoten: Dict[str, Knoten], abwaerts: bool) -> None:
    """Ordnet jede Ebene nach dem Mittel der Nachbarn - das entwirrt Kreuzungen."""
    stellen = {reihe[i].schluessel: i for reihe in ebenen for i in range(len(reihe))}
    reihenfolge = range(1, len(ebenen)) if abwaerts else range(len(ebenen) - 2, -1, -1)
    for nummer in reihenfolge:
        gewicht: Dict[str, float] = {}
        for eintrag in ebenen[nummer]:
            nachbarn = [
                stellen.get(k.von if abwaerts else k.nach, 0)
                for k in kanten
                if (k.nach if abwaerts else k.von) == eintrag.schluessel
                and knoten.get(k.von if abwaerts else k.nach) is not None
                and knoten[k.von if abwaerts else k.nach].ebene
                == (nummer - 1 if abwaerts else nummer + 1)
            ]
            gewicht[eintrag.schluessel] = (sum(nachbarn) / len(nachbarn)
                                           if nachbarn else stellen[eintrag.schluessel])
        ebenen[nummer].sort(key=lambda e: (gewicht[e.schluessel], stellen[e.schluessel]))
        for i, eintrag in enumerate(ebenen[nummer]):
            stellen[eintrag.schluessel] = i


# ============================================================ Zeichenbild
_VERBINDER = {
    frozenset("ud"): "│", frozenset("lr"): "─",
    frozenset("dr"): "┌", frozenset("dl"): "┐",
    frozenset("ur"): "└", frozenset("ul"): "┘",
    frozenset("udr"): "├", frozenset("udl"): "┤",
    frozenset("dlr"): "┬", frozenset("ulr"): "┴",
    frozenset("udlr"): "┼",
    frozenset("u"): "│", frozenset("d"): "│",
    frozenset("l"): "─", frozenset("r"): "─",
}


class _Leinwand:
    """Ein Zeichenraster mit sauberen Kreuzungen.

    Linien werden als Richtungen je Zelle gesammelt und erst am Ende in ein
    Zeichen übersetzt. Nur so wird aus einer Kreuzung ein '┼' statt eines
    '│', das ein '─' überschreibt.
    """

    def __init__(self) -> None:
        self.text: Dict[Tuple[int, int], str] = {}
        self.linien: Dict[Tuple[int, int], Set[str]] = {}

    def setzen(self, zeile: int, spalte: int, zeichen: str) -> None:
        for versatz, einzeln in enumerate(zeichen):
            self.text[(zeile, spalte + versatz)] = einzeln

    def linie(self, zeile: int, spalte: int, richtungen: str) -> None:
        self.linien.setdefault((zeile, spalte), set()).update(richtungen)

    def senkrecht(self, spalte: int, von_zeile: int, bis_zeile: int,
                  oben_offen: bool = True, unten_offen: bool = True) -> None:
        """Ein senkrechtes Stück.

        Die beiden Schalter sagen, ob das Stück an seinem Ende an etwas
        ANDERES anschließt (Kasten, Spur, Pfeilspitze) - genau daran hängt,
        welches Eckzeichen entsteht. Ohne sie wurde aus einem einzelligen
        Stück, das nach rechts abbiegt, ein '├' statt eines '└': die Linie
        behauptete einen Weg nach unten, den es nicht gab.
        """
        von, bis = min(von_zeile, bis_zeile), max(von_zeile, bis_zeile)
        for zeile in range(von, bis + 1):
            richtungen = ""
            if zeile > von or oben_offen:
                richtungen += "u"
            if zeile < bis or unten_offen:
                richtungen += "d"
            self.linie(zeile, spalte, richtungen or "ud")

    def waagerecht(self, zeile: int, von_spalte: int, bis_spalte: int) -> None:
        for spalte in range(min(von_spalte, bis_spalte), max(von_spalte, bis_spalte) + 1):
            richtungen = ""
            if spalte > min(von_spalte, bis_spalte):
                richtungen += "l"
            if spalte < max(von_spalte, bis_spalte):
                richtungen += "r"
            self.linie(zeile, spalte, richtungen or "lr")

    def ausgeben(self) -> str:
        zellen: Dict[Tuple[int, int], str] = {}
        for stelle, richtungen in self.linien.items():
            zellen[stelle] = _VERBINDER.get(frozenset(richtungen), "·")
        zellen.update(self.text)          # Kästen und Pfeilspitzen gewinnen
        if not zellen:
            return ""
        letzte_zeile = max(z for z, _ in zellen)
        zeilen = []
        for zeile in range(letzte_zeile + 1):
            spalten = [s for z, s in zellen if z == zeile]
            if not spalten:
                zeilen.append("")
                continue
            breite = max(spalten) + 1
            zeilen.append("".join(zellen.get((zeile, s), " ")
                                  for s in range(breite)).rstrip())
        return "\n".join(zeilen)


def als_zeichen(graph: Graph) -> str:
    """Das Bild als Unicode-Kästen - die Form, die überall ankommt."""
    ebenen, kanten = _layout(graph)
    leinwand = _Leinwand()

    # --- Spaltenpositionen: jede Ebene wird in der Gesamtbreite zentriert ---
    ABSTAND = 4
    breiten = [sum(k.breite_zeichen for k in reihe) + ABSTAND * max(0, len(reihe) - 1)
               for reihe in ebenen]
    gesamt = max(breiten) if breiten else 0
    links: Dict[str, int] = {}
    for nummer, reihe in enumerate(ebenen):
        stelle = (gesamt - breiten[nummer]) // 2
        for eintrag in reihe:
            links[eintrag.schluessel] = stelle
            stelle += eintrag.breite_zeichen + ABSTAND

    mitte = {s: links[s] + (graph_knoten(ebenen, s).breite_zeichen - 1) // 2
             for s in links}

    # --- Zeilenpositionen: Kastenhöhe 3, dazwischen so viele Spuren wie nötig ---
    hoch: Dict[int, int] = {}
    zeile = 0
    if graph.titel:
        zeile = 2
    spuren: Dict[int, List[Kante]] = {}
    for nummer in range(len(ebenen)):
        hoch[nummer] = zeile
        zeile += 3
        if nummer + 1 >= len(ebenen):
            break
        gap = [k for k in kanten
               if not k.rueckwaerts
               and graph_knoten(ebenen, k.von).ebene == nummer]
        schraeg = [k for k in gap if mitte[k.von] != mitte[k.nach]]
        spuren[nummer] = schraeg
        zeile += 2 + len(schraeg)

    if graph.titel:
        leinwand.setzen(0, max(0, (gesamt - len(graph.titel)) // 2), graph.titel)

    # --- Kästen zeichnen ---
    for reihe in ebenen:
        for eintrag in reihe:
            oben = hoch[eintrag.ebene]
            if eintrag.platzhalter:
                leinwand.senkrecht(mitte[eintrag.schluessel], oben, oben + 2)
                continue
            _kasten(leinwand, oben, links[eintrag.schluessel], eintrag)

    # --- Kanten führen ---
    for nummer, schraeg in spuren.items():
        gap = [k for k in kanten
               if not k.rueckwaerts and graph_knoten(ebenen, k.von).ebene == nummer]
        unterkante = hoch[nummer] + 2
        pfeilzeile = hoch[nummer + 1] - 1
        for kante in gap:
            von_spalte, nach_spalte = mitte[kante.von], mitte[kante.nach]
            if von_spalte == nach_spalte:
                leinwand.senkrecht(von_spalte, unterkante + 1, pfeilzeile - 1)
            else:
                spur = unterkante + 1 + schraeg.index(kante)
                # Der Abstieg endet AUF der Spur, der Aufstieg beginnt dort -
                # die Waagerechte liefert an dieser Stelle links/rechts.
                leinwand.senkrecht(von_spalte, unterkante + 1, spur, unten_offen=False)
                leinwand.waagerecht(spur, von_spalte, nach_spalte)
                leinwand.senkrecht(nach_spalte, spur, pfeilzeile - 1, oben_offen=False)
            # Eine Pfeilspitze steht nur vor einem echten Kasten. Ein
            # Zwischenknoten ist bloß eine durchlaufende Linie - dort hätte
            # eine Spitze behauptet, die Kante endet hier.
            if graph_knoten(ebenen, kante.nach).platzhalter:
                leinwand.linie(pfeilzeile, nach_spalte, "ud")
            else:
                leinwand.setzen(pfeilzeile, nach_spalte, "▼")
                _durchstich(leinwand, hoch[nummer + 1], nach_spalte, "┴")
            if not graph_knoten(ebenen, kante.von).platzhalter:
                _durchstich(leinwand, unterkante, von_spalte, "┬")
            if kante.beschriftung:
                spur = (unterkante + 1 + schraeg.index(kante)) if von_spalte != nach_spalte \
                    else (unterkante + 1)
                leinwand.setzen(spur, max(von_spalte, nach_spalte) + 2,
                                kante.beschriftung[:20])

    zeichnung = leinwand.ausgeben()

    rueck = [k for k in kanten if k.rueckwaerts]
    if rueck:
        # Eine Rückführung als Linie durch das ganze Bild zu ziehen macht
        # jedes Ebenenlayout unlesbar. Sie steht deshalb ausdrücklich darunter -
        # sichtbar, aber ohne das Bild zu zerstören.
        namen = {s: graph_knoten(ebenen, s).beschriftung for s in links}
        zeichnung += "\n\nRückführungen:\n" + "\n".join(
            f"  {namen.get(k.von, k.von)} ↩ zurück zu {namen.get(k.nach, k.nach)}"
            + (f" ({k.beschriftung})" if k.beschriftung else "")
            for k in rueck)
    return zeichnung


def graph_knoten(ebenen: List[List[Knoten]], schluessel: str) -> Knoten:
    for reihe in ebenen:
        for eintrag in reihe:
            if eintrag.schluessel == schluessel:
                return eintrag
    return Knoten(schluessel, schluessel)


# Vier Formen, vier eindeutig unterscheidbare Rahmen. Jede Form ist in sich
# geschlossen - gemischte Ecken (oben eckig, unten schräg) lesen sich als
# Zeichenfehler, nicht als Bedeutung.
#   (oben_links, oben_rechts, waagerecht, senkrecht_links, senkrecht_rechts,
#    unten_links, unten_rechts)
_RAHMEN = {
    "kasten":  ("┌", "┐", "─", "│", "│", "└", "┘"),   # Arbeitsschritt
    "rund":    ("╭", "╮", "─", "│", "│", "╰", "╯"),   # Anfang oder Ende
    "raute":   ("╱", "╲", "─", "│", "│", "╲", "╱"),   # Entscheidung
    "schraeg": ("╔", "╗", "═", "║", "║", "╚", "╝"),   # Ein- oder Ausgabe
}


def _kasten(leinwand: _Leinwand, oben: int, links_spalte: int, eintrag: Knoten) -> None:
    """Ein Kasten in der Form, die seine Rolle anzeigt."""
    text = eintrag.beschriftung
    innen = len(text) + 2
    ol, orr, quer, sl, sr, ul, ur = _RAHMEN.get(eintrag.form, _RAHMEN["kasten"])
    leinwand.setzen(oben, links_spalte, ol + quer * innen + orr)
    leinwand.setzen(oben + 1, links_spalte, sl + " " + text + " " + sr)
    leinwand.setzen(oben + 2, links_spalte, ul + quer * innen + ur)


def _durchstich(leinwand: _Leinwand, zeile: int, spalte: int, zeichen: str) -> None:
    """Markiert in der Kastenkante, wo eine Linie ein- oder austritt.

    Nur die waagerechte Kastenkante wird ersetzt - eine Ecke oder ein
    Beschriftungszeichen bleibt stehen, sonst frisst der Durchstich den Kasten.
    """
    vorhanden = leinwand.text.get((zeile, spalte), "")
    if vorhanden == "─":
        leinwand.text[(zeile, spalte)] = zeichen
    elif vorhanden == "═":
        leinwand.text[(zeile, spalte)] = "╤" if zeichen == "┬" else "╧"


# ==================================================================== SVG
def als_svg(graph: Graph) -> str:
    """Dasselbe Layout als Vektorbild - für die Oberfläche."""
    ebenen, kanten = _layout(graph)

    ZEICHEN = 8.4       # Breite eines Monospace-Zeichens bei 14px
    HOEHE = 38
    ABSTAND_X = 34
    ABSTAND_Y = 62
    RAND = 18

    breite_von = {}
    for reihe in ebenen:
        for eintrag in reihe:
            breite_von[eintrag.schluessel] = (6 if eintrag.platzhalter
                                              else len(eintrag.beschriftung) * ZEICHEN + 26)

    reihenbreite = [sum(breite_von[e.schluessel] for e in reihe)
                    + ABSTAND_X * max(0, len(reihe) - 1) for reihe in ebenen]
    gesamt = max(reihenbreite) if reihenbreite else 0
    kopf = 30 if graph.titel else 0

    x: Dict[str, float] = {}
    y: Dict[str, float] = {}
    for nummer, reihe in enumerate(ebenen):
        stelle = RAND + (gesamt - reihenbreite[nummer]) / 2
        for eintrag in reihe:
            x[eintrag.schluessel] = stelle
            y[eintrag.schluessel] = RAND + kopf + nummer * (HOEHE + ABSTAND_Y)
            stelle += breite_von[eintrag.schluessel] + ABSTAND_X

    gesamt_breite = gesamt + 2 * RAND
    gesamt_hoehe = RAND * 2 + kopf + len(ebenen) * HOEHE + max(0, len(ebenen) - 1) * ABSTAND_Y

    teile = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {gesamt_breite:.0f} '
        f'{gesamt_hoehe:.0f}" width="100%" style="max-width:{gesamt_breite:.0f}px">',
        '<defs><marker id="spitze" viewBox="0 0 10 10" refX="9" refY="5" '
        'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M0,0 L10,5 L0,10 z" fill="{FARBEN["linie"]}"/></marker></defs>',
        f'<rect width="100%" height="100%" fill="{FARBEN["grund"]}" rx="8"/>',
    ]
    if graph.titel:
        teile.append(
            f'<text x="{gesamt_breite/2:.0f}" y="24" text-anchor="middle" '
            f'font-family="ui-monospace,monospace" font-size="14" '
            f'fill="{FARBEN["text"]}">{_xml(graph.titel)}</text>')

    for kante in kanten:
        if kante.von not in x or kante.nach not in x:
            continue
        x1 = x[kante.von] + breite_von[kante.von] / 2
        y1 = y[kante.von] + HOEHE
        x2 = x[kante.nach] + breite_von[kante.nach] / 2
        y2 = y[kante.nach]
        if kante.rueckwaerts:
            bogen = max(breite_von.values()) / 2 + 24
            teile.append(
                f'<path d="M {x1:.0f} {y[kante.von]:.0f} '
                f'C {x1 - bogen:.0f} {y[kante.von]:.0f} '
                f'{x2 - bogen:.0f} {y2 + HOEHE:.0f} {x2:.0f} {y2 + HOEHE:.0f}" '
                f'fill="none" stroke="{FARBEN["linie"]}" stroke-width="1.4" '
                f'stroke-dasharray="4 4" marker-end="url(#spitze)"/>')
            continue
        mitte_y = (y1 + y2) / 2
        teile.append(
            f'<path d="M {x1:.0f} {y1:.0f} L {x1:.0f} {mitte_y:.0f} '
            f'L {x2:.0f} {mitte_y:.0f} L {x2:.0f} {y2:.0f}" fill="none" '
            f'stroke="{FARBEN["linie"]}" stroke-width="1.4" marker-end="url(#spitze)"/>')
        if kante.beschriftung:
            teile.append(
                f'<text x="{(x1 + x2) / 2:.0f}" y="{mitte_y - 5:.0f}" '
                f'text-anchor="middle" font-family="ui-monospace,monospace" '
                f'font-size="11" fill="{FARBEN["text"]}" opacity=".75">'
                f'{_xml(kante.beschriftung)}</text>')

    for reihe in ebenen:
        for eintrag in reihe:
            if eintrag.platzhalter:
                continue
            breite = breite_von[eintrag.schluessel]
            farbe = FORM_FARBE.get(eintrag.form, FARBEN["eis"])
            ecke = HOEHE / 2 if eintrag.form == "rund" else 5
            teile.append(
                f'<rect x="{x[eintrag.schluessel]:.0f}" y="{y[eintrag.schluessel]:.0f}" '
                f'width="{breite:.0f}" height="{HOEHE}" rx="{ecke:.0f}" '
                f'fill="{FARBEN["kasten"]}" stroke="{farbe}" stroke-width="1.4"/>')
            teile.append(
                f'<text x="{x[eintrag.schluessel] + breite / 2:.0f}" '
                f'y="{y[eintrag.schluessel] + HOEHE / 2 + 4:.0f}" text-anchor="middle" '
                f'font-family="ui-monospace,monospace" font-size="13" '
                f'fill="{FARBEN["text"]}">{_xml(eintrag.beschriftung)}</text>')

    teile.append("</svg>")
    return "".join(teile)


def _xml(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# ================================================================ Mermaid
_MERMAID_FORM = {
    "rund": ("([", "])"),
    "kasten": ("[", "]"),
    "raute": ("{", "}"),
    "schraeg": ("[/", "/]"),
}


def als_mermaid(graph: Graph) -> str:
    """Quelltext für Obsidian - das rendert Mermaid von Haus aus.

    Deshalb ist DAS die Form, die in eine Notiz gehört, während der Chat die
    Zeichenversion bekommt: In der Vault soll ein Bild stehen, kein Abbild.
    """
    kuerzel: Dict[str, str] = {}
    zeilen = ["flowchart " + ("LR" if graph.richtung == "rechts" else "TD")]
    for nummer, schluessel in enumerate(graph.knoten, 1):
        eintrag = graph.knoten[schluessel]
        marke = f"n{nummer}"
        kuerzel[schluessel] = marke
        auf, zu = _MERMAID_FORM.get(eintrag.form, ("[", "]"))
        zeilen.append(f'    {marke}{auf}"{eintrag.beschriftung}"{zu}')
    for kante in graph.kanten:
        pfeil = "-.->" if kante.rueckwaerts else "-->"
        beschriftung = f'|"{kante.beschriftung}"|' if kante.beschriftung else ""
        zeilen.append(f"    {kuerzel[kante.von]} {pfeil}{beschriftung} {kuerzel[kante.nach]}")
    return "\n".join(zeilen)


# =============================================================== Übersicht
def mittel(oberflaeche: str = "") -> Dict[str, str]:
    """Was steht zum Darstellen zur Verfügung - geprüft, nicht geraten.

    Josh hat ausdrücklich gesagt: "anfangs scannen was du hast". Diese
    Funktion ist die Antwort darauf, und sie beschreibt nur, was dieses
    System wirklich kann - kein Bild, kein Video, und das steht auch so da.
    """
    vorhanden = {
        "zeichen": "Unicode-Kästen mit Linien und Pfeilen - im Chat und im Terminal",
        "mermaid": "Mermaid-Quelltext - wird von Obsidian in der Vault gerendert",
    }
    if oberflaeche in ("", "web"):
        vorhanden["svg"] = "Vektorbild - wird in der grafischen Oberfläche gezeichnet"
    return vorhanden


def zeichnen(beschreibung: str, titel: str = "", richtung: str = "",
             formen: Sequence[str] = ()) -> Dict[str, str]:
    """Ein Aufruf, alle Formen. Wirft DarstellungsFehler mit lesbarer Begründung."""
    graph = aus_text(beschreibung, titel, richtung)
    gewuenscht = set(formen) or {"zeichen", "mermaid", "svg"}
    ausgabe: Dict[str, str] = {}
    if "zeichen" in gewuenscht:
        ausgabe["zeichen"] = als_zeichen(graph)
    if "mermaid" in gewuenscht:
        ausgabe["mermaid"] = als_mermaid(graph)
    if "svg" in gewuenscht:
        ausgabe["svg"] = als_svg(graph)
    ausgabe["knoten"] = str(len(graph.knoten))
    ausgabe["kanten"] = str(len(graph.kanten))
    return ausgabe
