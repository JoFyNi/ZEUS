"""Selbsterweiterung: Der Agent baut sich neue Fertigkeiten aus geprüften Teilen.

Die reine Lehre wäre: Der Agent schreibt Python und führt es aus. In einem
System mit Schreibzugriff auf deine Wissensbasis ist das der kürzeste Weg zum
Datenverlust – ein halluziniertes `shutil.rmtree` genügt.

Deshalb hier die tragfähige Variante: Eine Fertigkeit ist ein **Ablaufplan aus
vorhandenen Werkzeugen**, deklarativ als Notiz in der Vault gespeichert. Der
Agent kann sie selbst schreiben, sie werden validiert (nur bekannte Werkzeuge,
begrenzte Schritte, Platzhalter geprüft) und danach wie eingebaute Werkzeuge
aufgerufen. Neue Fähigkeit, keine neue Angriffsfläche.

Beispiel einer erzeugten Fertigkeit:

    ---
    fertigkeit: thema_aufarbeiten
    beschreibung: Recherchiert ein Thema in der Vault und legt eine Übersicht an
    parameter: [thema]
    ---
    1. notizen_suchen(frage="{thema}", anzahl=8)
    2. notiz_erstellen(titel="Übersicht {thema}", inhalt="...")
    3. verwandte_finden(name="Übersicht {thema}")
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Set

from .config import settings
from .tools import (
    SCHREIBENDE_WERKZEUGE,
    TOOL_NAMES,
    TOOL_PARAMS,
    TOOL_REQUIRED,
    ToolResult,
    VaultTools,
)
from .vault import safe_filename, strip_frontmatter

SCHRITT_RE = re.compile(r"^\s*(?:\d+[.)]\s*)?(\w+)\s*\((.*)\)\s*$")
PLATZHALTER_RE = re.compile(r"\{(\w+)\}")
ERGEBNIS_RE = re.compile(r"^ergebnis(\d*)$")
# Nur die NUMMERIERTEN Bezüge - {ergebnis} ohne Zahl wird später repariert und
# darf hier nicht mitgezogen werden.
ERGEBNIS_NUMMER_RE = re.compile(r"\{ergebnis(\d+)\}")

# Parameter, die eine Notiz oder einen Ordner BENENNEN. Hier darf kein
# Suchergebnis landen: notiz_ergaenzen(name="{ergebnis1}") sucht danach eine
# Notiz, die "3 Treffer für ..." heißt - die gibt es nie. Genau dieser Fall
# ist beim ersten Selbstbau-Versuch durchgerutscht.
BEZEICHNER_PARAMETER = {
    "name", "titel", "ziel", "zielordner", "ordner", "neuer_name", "quelle",
    "von", "nach", "pfad", "abschnitt", "ueberschrift", "datei", "alt", "neu",
}

# Werkzeuge, deren Ergebnis eine konkrete Notiz bezeichnet (Pfad im Rückgabewert).
# Nur deren {ergebnisN} darf in einem Bezeichner-Parameter stehen.
LIEFERT_NOTIZ = {
    "notiz_erstellen", "notiz_ergaenzen", "notiz_bearbeiten", "notiz_ueberschreiben",
    "notiz_verschieben", "notiz_lesen", "notiz_wiederherstellen", "uebersichtsnotiz",
    "notizen_zusammenfuehren", "abschnitt_ersetzen", "abschnitt_einfuegen",
    "frontmatter_setzen", "tags_setzen",
}

# Wie das Muster richtig aussieht - wird bei einer Ablehnung mitgeliefert,
# damit das Modell nicht dreimal denselben Fehler baut.
MUSTER_HILFE = (
    "Richtiges Muster: Ein Bezeichner wird als Parameter deklariert und dann "
    'eingesetzt, z. B. parameter: [thema] und notiz_erstellen(titel="Übersicht {thema}", '
    'inhalt="{ergebnis1}"). {ergebnisN} enthält den TEXT aus Schritt N und gehört '
    "in inhalt/text, nicht in name/titel."
)


class _SchrittFehlschlag(Exception):
    """Bricht die Ausfuehrungs-Transaktion ab, damit sie zurueckgerollt wird.

    Rein intern: wird in ausfuehren() geworfen und dort auch wieder gefangen.
    Nach aussen dringt sie nie.
    """


@dataclass
class Schritt:
    werkzeug: str
    parameter: Dict[str, str] = field(default_factory=dict)

    def aufloesen(self, werte: Dict[str, str], ergebnisse: List[ToolResult]) -> Dict[str, object]:
        """Platzhalter ersetzen: {thema} aus den Argumenten, {ergebnis1} aus Schritt 1.

        Ein Bezeichner-Parameter (name, titel, ziel …) bekommt den NOTIZNAMEN
        aus Schritt N, ein Inhalts-Parameter den Text. Vorher wurde überall der
        Text eingesetzt - deshalb landete in `name` eine Trefferliste.
        """
        gefuellt: Dict[str, object] = {}
        for schluessel, wert in self.parameter.items():
            text = str(wert)
            for name, ersatz in werte.items():
                text = text.replace("{" + name + "}", str(ersatz))
            bezeichner = schluessel in BEZEICHNER_PARAMETER
            for i, ergebnis in enumerate(ergebnisse, 1):
                marke = "{ergebnis" + str(i) + "}"
                if marke not in text:
                    continue
                ersatz = (_bezeichner_aus(ergebnis) if bezeichner
                          else _inhalt_aus(ergebnis))
                text = text.replace(marke, ersatz)
            gefuellt[schluessel] = text
        return gefuellt

    def signatur_zeile(self) -> str:
        """Der Schritt so, wie er in der Notiz steht - fuer Anzeige und Vergleich."""
        return f"{self.werkzeug}({_argumente_schreiben(self.parameter)})"

    def offene_platzhalter(self, gefuellt: Dict[str, object]) -> List[str]:
        """Platzhalter, die nach dem Auflösen noch dastehen - immer ein Fehler."""
        offen = []
        for wert in gefuellt.values():
            offen.extend(PLATZHALTER_RE.findall(str(wert)))
        return sorted(set(offen))


@dataclass
class Fertigkeit:
    name: str
    beschreibung: str
    parameter: List[str] = field(default_factory=list)
    schritte: List[Schritt] = field(default_factory=list)
    pfad: Optional[Path] = None
    probleme: List[str] = field(default_factory=list)

    @property
    def intakt(self) -> bool:
        return not self.probleme

    def signatur(self) -> str:
        return f"{self.name}({', '.join(self.parameter)}): {self.beschreibung}"


class Fertigkeiten:
    """Lädt, prüft, speichert und führt selbst definierte Fertigkeiten aus."""

    def __init__(self, tools: VaultTools, cfg=settings):
        self.tools = tools
        self.cfg = cfg
        self._geladen: Dict[str, Fertigkeit] = {}
        self._defekt: Dict[str, Fertigkeit] = {}

    @property
    def ordner(self) -> Path:
        wurzel = self.tools.root
        return wurzel / self.cfg.skills_folder

    # ------------------------------------------------------------------ Laden
    def laden(self) -> Dict[str, Fertigkeit]:
        """Lädt alle Fertigkeiten und prüft sie dabei.

        Früher wurde beim Laden gar nichts geprüft - eine Fertigkeit, die vor
        der Verschärfung angelegt wurde, war weiterhin aufrufbar und schrieb
        im Zweifel '{ergebnis}' wörtlich in eine Notiz. Defekte landen jetzt
        getrennt und tauchen nicht mehr in der Werkzeugliste auf.
        """
        self._geladen = {}
        self._defekt = {}
        if not self.ordner.exists():
            return self._geladen
        for pfad in sorted(self.ordner.glob("*.md")):
            fertigkeit = self._aus_datei(pfad)
            if not fertigkeit:
                continue
            if fertigkeit.intakt:
                self._geladen[fertigkeit.name] = fertigkeit
            else:
                self._defekt[fertigkeit.name] = fertigkeit
        return self._geladen

    @property
    def defekt(self) -> Dict[str, Fertigkeit]:
        if not self._geladen and not self._defekt:
            self.laden()
        return self._defekt

    def _aus_datei(self, pfad: Path) -> Optional[Fertigkeit]:
        try:
            roh = pfad.read_text(encoding="utf-8")
        except OSError:
            return None
        kopf, koerper = strip_frontmatter(roh)
        name = str(kopf.get("fertigkeit") or kopf.get("name") or pfad.stem).strip()
        if not name:
            return None
        parameter = kopf.get("parameter") or []
        if isinstance(parameter, str):
            parameter = [p.strip() for p in parameter.strip("[]").split(",") if p.strip()]

        parameter = [str(p) for p in parameter]
        schritte: List[Schritt] = []
        probleme: List[str] = []
        werkzeug_folge: List[str] = []
        for zeile in koerper.splitlines():
            treffer = SCHRITT_RE.match(zeile)
            if not treffer:
                continue
            werkzeug = treffer.group(1)
            if werkzeug not in TOOL_NAMES:
                continue
            argumente = _argumente_lesen(treffer.group(2))
            nummer = len(schritte) + 1
            fehlt = [p for p in _pflichtparameter(werkzeug) if p not in argumente]
            if fehlt:
                probleme.append(f"Schritt {nummer} {werkzeug}: Pflichtparameter fehlen "
                                f"({', '.join(fehlt)})")
            argumente, schritt_probleme, _ = _platzhalter_pruefen(
                nummer, werkzeug, argumente, parameter, werkzeug_folge)
            probleme.extend(schritt_probleme)
            werkzeug_folge.append(werkzeug)
            schritte.append(Schritt(werkzeug, argumente))
            if len(schritte) >= self.cfg.max_skill_steps:
                break
        if not schritte:
            return None
        return Fertigkeit(
            name=name,
            beschreibung=str(kopf.get("beschreibung") or kopf.get("description") or name),
            parameter=parameter,
            schritte=schritte,
            pfad=pfad,
            probleme=probleme,
        )

    @property
    def alle(self) -> Dict[str, Fertigkeit]:
        if not self._geladen:
            self.laden()
        return self._geladen

    # ------------------------------------------------------------- Definieren
    def definieren(self, name: str, beschreibung: str, parameter: Sequence[str],
                   schritte: Sequence[str], ersetzen: bool = False) -> ToolResult:
        """Legt eine neue Fertigkeit an - nach vollständiger Prüfung."""
        if not self.cfg.self_extension:
            return ToolResult(False, "Selbsterweiterung ist deaktiviert (SELF_EXTENSION=false).")

        name = safe_filename(str(name).strip().lower().replace(" ", "_"))
        if not name:
            return ToolResult(False, "Ungültiger Name für die Fertigkeit.")
        if name in TOOL_NAMES:
            return ToolResult(False, f"'{name}' ist bereits ein eingebautes Werkzeug.")

        # Stilles Ueberschreiben war moeglich: ein zweites definieren() mit
        # demselben Namen ersetzte die bestehende Fertigkeit kommentarlos, samt
        # aller Korrekturen, die von Hand hineingeflossen waren. Vorbild ist
        # notiz_ueberschreiben, das ebenfalls eine ausdrueckliche Bestaetigung
        # verlangt.
        vorhanden = self.ordner / f"{name}.md"
        if vorhanden.exists() and not ersetzen:
            bisher = self.alle.get(name) or self.defekt.get(name)
            return ToolResult(
                False,
                f"Fertigkeit '{name}' gibt es bereits"
                + (f" ({len(bisher.schritte)} Schritte)" if bisher else "")
                + ". Zum Ueberschreiben denselben Aufruf mit ersetzen=true "
                  "wiederholen, oder einen anderen Namen waehlen.",
                {"vorhanden": str(vorhanden.name),
                 "bisherige_schritte": [sch.signatur_zeile() for sch in bisher.schritte]
                                       if bisher else []})
        if not schritte:
            # Diese Meldung war früher "Kein gültiger Schritt übrig." und stand
            # AUCH dann da, wenn schritte gar nicht erst ankam - z. B. weil die
            # Werkzeugargumente des Modells kein gültiges JSON waren und leer
            # verarbeitet wurden. Ohne diesen Fall genannt zu bekommen, sah ein
            # Parsing-Fehler eine Ebene höher aus wie eine inhaltliche
            # Ablehnung der Schritte - beides braucht eine andere Reaktion.
            return ToolResult(
                False,
                "Es wurden keine Schritte übergeben. 'schritte' muss eine nicht-leere "
                "Liste von Aufrufen sein, z. B. "
                '[\'notizen_suchen(frage="{thema}", anzahl=5)\', '
                '\'notiz_erstellen(titel="Übersicht {thema}", inhalt="{ergebnis1}")\'].',
                {"hilfe": MUSTER_HILFE},
            )

        parameter = [re.sub(r"\W", "", str(p)) for p in (parameter or []) if str(p).strip()]

        # Zwei Klassen von Fehlern, und der Unterschied ist der Kern dieser
        # Prüfung:
        #
        #   WEICH  - der Schritt ist für sich genommen unbrauchbar (nicht
        #            lesbar, unbekanntes Werkzeug, Pflichtparameter fehlt).
        #            Er wird verworfen, der Rest bleibt. Das ist die
        #            Teilannahme: bei vier guten und einem vertippten Schritt
        #            soll nicht die ganze Arbeit weg sein.
        #
        #   HART   - die Schritte hängen falsch zusammen (Platzhalter zeigt
        #            ins Leere, nach vorn, oder ein Suchergebnis wird als
        #            Notizname benutzt). Hier ist die ABSICHT falsch
        #            verstanden, nicht ein Schritt vertippt. Wer so etwas
        #            verwirft und den Rest aufnimmt, legt eine Fertigkeit an,
        #            die etwas anderes tut als bestellt - und meldet dabei
        #            Erfolg. Genau das ist passiert. Deshalb: ablehnen.
        weich: List[str] = []
        hart: List[str] = []
        repariert: List[str] = []

        # ------------------------------------------------- Durchgang 1: lesen
        # Erst alle Schritte lesen, dann erst die Platzhalter prüfen. Der Grund
        # ist die Nummerierung: Wird Schritt 2 verworfen, rutscht Schritt 3 auf
        # Platz 2 - und {ergebnis3} in Schritt 4 zeigte danach auf den falschen
        # Schritt, ohne dass irgendwer es merkte. Die Zuordnung alt -> neu wird
        # deshalb mitgeführt und angewendet.
        gelesen: List[Optional[tuple]] = []
        zuordnung: Dict[int, int] = {}
        verworfen: Set[int] = set()
        laufend = 0

        for nummer, roh in enumerate(list(schritte)[: self.cfg.max_skill_steps], 1):
            roh = _schritt_als_text(roh)
            treffer = SCHRITT_RE.match(str(roh).strip())
            if not treffer:
                weich.append(f"Nicht lesbar: {roh}")
                verworfen.add(nummer)
                gelesen.append(None)
                continue
            werkzeug, argumente = treffer.group(1), _argumente_lesen(treffer.group(2))
            if werkzeug not in TOOL_NAMES:
                weich.append(f"Unbekanntes Werkzeug: {werkzeug}")
                verworfen.add(nummer)
                gelesen.append(None)
                continue
            unbekannt = set(argumente) - set(TOOL_PARAMS.get(werkzeug, []))
            if unbekannt:
                weich.append(f"{werkzeug}: unbekannte Parameter {', '.join(sorted(unbekannt))}")
                verworfen.add(nummer)
                gelesen.append(None)
                continue
            fehlt = [p for p in _pflichtparameter(werkzeug) if p not in argumente]
            if fehlt:
                weich.append(f"Schritt {nummer} {werkzeug}: Pflichtparameter fehlen "
                             f"({', '.join(fehlt)})")
                verworfen.add(nummer)
                gelesen.append(None)
                continue
            laufend += 1
            zuordnung[nummer] = laufend
            gelesen.append((nummer, werkzeug, argumente))

        # ------------------------------------------ Durchgang 2: Platzhalter
        geprueft: List[str] = []
        werkzeug_folge: List[str] = []

        for eintrag in gelesen:
            if eintrag is None:
                continue
            alt_nummer, werkzeug, argumente = eintrag
            neu_nummer = zuordnung[alt_nummer]
            umgeschrieben: Dict[str, str] = {}
            for schluessel, wert in argumente.items():
                text, bezugs_probleme = _bezuege_umschreiben(
                    str(wert), zuordnung, verworfen, neu_nummer, werkzeug)
                hart.extend(bezugs_probleme)
                umgeschrieben[schluessel] = text

            umgeschrieben, schritt_probleme, schritt_repariert = _platzhalter_pruefen(
                neu_nummer, werkzeug, umgeschrieben, parameter, werkzeug_folge)
            hart.extend(schritt_probleme)
            repariert.extend(schritt_repariert)
            werkzeug_folge.append(werkzeug)
            geprueft.append(f"{werkzeug}({_argumente_schreiben(umgeschrieben)})")

        if hart:
            return ToolResult(
                False,
                "Fertigkeit abgelehnt – die Schritte greifen nicht ineinander: "
                + "; ".join(hart[:4]),
                {"probleme": hart + weich, "hilfe": MUSTER_HILFE,
                 "verfuegbare_werkzeuge": sorted(TOOL_NAMES)})

        # Nur wenn NICHTS Gültiges übrig bleibt, wird abgelehnt - eine
        # Fertigkeit ohne Schritte ist keine.
        probleme = weich
        if not geprueft:
            return ToolResult(
                False,
                "Kein einziger Schritt war gültig: " + "; ".join(probleme[:4]),
                {"verfuegbare_werkzeuge": sorted(TOOL_NAMES),
                 "probleme": probleme, "hilfe": MUSTER_HILFE})

        # ------------------------------------------------------- Probelauf
        # Ohne diesen Schritt wird eine Fertigkeit angelegt, die beim ersten
        # echten Aufruf scheitert - und bis dahin sieht alles nach Erfolg aus.
        bericht = self.probelauf(geprueft, parameter)
        if not bericht.ok:
            return ToolResult(False, f"Fertigkeit abgelehnt – Probelauf fehlgeschlagen: "
                                     f"{bericht.meldung}",
                              {"probelauf": bericht.daten.get("protokoll", []),
                               "hilfe": MUSTER_HILFE})

        self.ordner.mkdir(parents=True, exist_ok=True)
        pfad = self.ordner / f"{name}.md"
        inhalt = (
            "---\n"
            f"fertigkeit: {name}\n"
            f'beschreibung: "{str(beschreibung)[:200]}"\n'
            f"parameter: [{', '.join(parameter)}]\n"
            "tags:\n  - fertigkeit\n  - selbstgebaut\n"
            "---\n\n"
            f"# Fertigkeit: {name}\n\n{beschreibung}\n\n## Ablauf\n"
            + "\n".join(f"{i}. {s}" for i, s in enumerate(geprueft, 1))
            + "\n"
        )
        if repariert:
            inhalt += ("\n## Korrekturen bei der Aufnahme\n"
                       + "\n".join(f"- {z}" for z in repariert) + "\n")
        if probleme:
            inhalt += ("\n## Verworfene Schritte\n"
                       + "\n".join(f"- {z}" for z in probleme)
                       + "\n\nDiese Schritte wurden NICHT aufgenommen. Die Fertigkeit "
                         "läuft ohne sie; ergänze sie bei Bedarf von Hand oder lege "
                         "die Fertigkeit mit korrigierten Schritten neu an.\n")
        inhalt += ("\n## Probelauf\n"
                   + "\n".join(f"- {z}" for z in bericht.daten.get("protokoll", []))
                   + "\n\nSchreibende Schritte wurden dabei nur simuliert, "
                     "lesende wirklich ausgeführt.\n")
        try:
            pfad.write_text(inhalt, encoding="utf-8")
        except OSError as exc:
            return ToolResult(False, f"Fertigkeit nicht speicherbar: {exc}")

        self.laden()
        # Die Meldung sagt ausdrücklich "angelegt, NICHT ausgeführt" - beim
        # ersten Selbstbau hat das Modell aus "angelegt" ein "durchgeführt"
        # gemacht und Ergebnisse berichtet, die es nie gab.
        meldung = (f"Fertigkeit '{name}' mit {len(geprueft)} Schritten ANGELEGT "
                   f"(noch nicht ausgeführt – dafür: fertigkeit_ausfuehren"
                   f"(name=\"{name}\")).")
        if probleme:
            meldung += (f" {len(probleme)} Schritt(e) wurden dabei VERWORFEN: "
                        + "; ".join(probleme[:3]))
        return ToolResult(
            True, meldung,
            {"pfad": str(pfad.name), "schritte": geprueft, "ausgefuehrt": False,
             "probelauf": bericht.daten.get("protokoll", []),
             "korrekturen": repariert, "verworfen": probleme},
        )

    # -------------------------------------------------------------- Probelauf
    def probelauf(self, schritte: Sequence[str], parameter: Sequence[str]) -> ToolResult:
        """Spielt die Fertigkeit einmal durch, bevor sie aufgenommen wird.

        Lesende Werkzeuge laufen wirklich - so zeigt sich, ob eine Suche
        überhaupt etwas findet. Schreibende werden nur simuliert; ein
        Probelauf darf die Vault nicht anfassen.

        SIMULATIONSGEDÄCHTNIS: Weil schreibende Schritte nur simuliert werden,
        existiert die dabei "angelegte" Notiz auf Platte nie. Ein folgender
        LESENDER Schritt auf genau diese Notiz musste deshalb zwangsläufig
        scheitern - und damit war jedes Muster "erst anlegen, dann darauf
        zugreifen" grundsätzlich unmöglich. Betroffen war ausgerechnet das
        Vorbild aus dem Systemprompt (notizen_suchen → notiz_erstellen →
        verwandte_finden): Das Modell befolgte die Vorlage und wurde dafür
        abgelehnt.

        Deshalb wird jetzt mitgeschrieben, welche Notiznamen ein simulierter
        Schritt angelegt HÄTTE. Zeigt ein späterer Leseschritt auf einen
        solchen Namen, wird er übersprungen statt ausgeführt - er kann hier
        nichts beweisen. Jeder andere Leseschritt läuft weiter echt; die
        Prüfschärfe für alles unabhängig Prüfbare bleibt unverändert.
        """
        beispiel = {p: f"Probe-{p}" for p in parameter}
        ergebnisse: List[ToolResult] = []
        protokoll: List[str] = []
        # Notiznamen, die es nur in der Simulation gibt (klein geschrieben)
        virtuell: Set[str] = set()

        for nummer, roh in enumerate(schritte, 1):
            treffer = SCHRITT_RE.match(str(roh).strip())
            if not treffer:
                return ToolResult(False, f"Schritt {nummer} nicht lesbar.",
                                  {"protokoll": protokoll})
            werkzeug = treffer.group(1)
            schritt = Schritt(werkzeug, _argumente_lesen(treffer.group(2)))
            aufgeloest = schritt.aufloesen(beispiel, ergebnisse)

            offen = schritt.offene_platzhalter(aufgeloest)
            if offen:
                return ToolResult(
                    False,
                    f"Schritt {nummer} ({werkzeug}): {{{'}, {'.join(offen)}}} bleibt "
                    f"unersetzt und würde wörtlich in der Vault landen.",
                    {"protokoll": protokoll})

            if werkzeug in SCHREIBENDE_WERKZEUGE:
                ziel = (aufgeloest.get("name") or aufgeloest.get("titel")
                        or aufgeloest.get("ziel") or "")
                protokoll.append(f"{nummer}. {werkzeug} → simuliert (schreibend)"
                                 + (f", Ziel: {ziel}" if ziel else ""))
                if ziel:
                    virtuell.add(str(ziel).strip().lower())
                # Platzhalter für die Folgeschritte: so tun, als wäre es geglückt
                ergebnisse.append(ToolResult(True, f"[simuliert] {werkzeug}",
                                             {"pfad": f"{ziel or 'Probe'}.md"}))
                continue

            # Hängt dieser Leseschritt an einer nur simuliert angelegten Notiz?
            # Dann kann er hier nichts beweisen - überspringen statt scheitern.
            bezug = next((str(aufgeloest[p]).strip().lower()
                          for p in BEZEICHNER_PARAMETER
                          if str(aufgeloest.get(p, "")).strip()), "")
            if bezug and bezug in virtuell:
                protokoll.append(f"{nummer}. {werkzeug} → übersprungen "
                                 f"(hängt an simulierter Notiz '{bezug}')")
                ergebnisse.append(ToolResult(True, f"[übersprungen] {werkzeug}",
                                             {"pfad": f"{bezug}.md"}))
                continue

            ergebnis = self.tools.ausfuehren(werkzeug, aufgeloest)
            ergebnisse.append(ergebnis)
            protokoll.append(f"{nummer}. {werkzeug} → echt ausgeführt, "
                             f"{'ok' if ergebnis.ok else 'FEHLER'}: {ergebnis.meldung[:120]}")
            if not ergebnis.ok:
                return ToolResult(False, f"Schritt {nummer} ({werkzeug}) scheitert: "
                                         f"{ergebnis.meldung}",
                                  {"protokoll": protokoll})

        return ToolResult(True, f"Probelauf über {len(schritte)} Schritte bestanden.",
                          {"protokoll": protokoll})

    # -------------------------------------------------------------- Ausführen
    def ausfuehren(self, name: str, argumente: Optional[dict] = None) -> ToolResult:
        fertigkeit = self.alle.get(name)
        if not fertigkeit:
            kaputt = self.defekt.get(name)
            if kaputt:
                # Nicht "unbekannt" melden - der Grund ist bekannt und behebbar.
                return ToolResult(
                    False,
                    f"Fertigkeit '{name}' ist fehlerhaft und deshalb gesperrt: "
                    + "; ".join(kaputt.probleme[:3]),
                    {"probleme": kaputt.probleme, "hilfe": MUSTER_HILFE,
                     "datei": str(kaputt.pfad.name) if kaputt.pfad else ""})
            return ToolResult(False, f"Fertigkeit '{name}' ist nicht bekannt.",
                              {"vorhanden": sorted(self.alle)})
        werte = {k: str(v) for k, v in (argumente or {}).items()}
        fehlend = [p for p in fertigkeit.parameter if p not in werte]
        if fehlend:
            return ToolResult(False, f"Fehlende Parameter: {', '.join(fehlend)}")

        ergebnisse: List[ToolResult] = []
        protokoll: List[str] = []
        abbruch = ""

        # Ganz oder gar nicht. Vorher blieben die Schreibvorgänge der Schritte
        # 1-2 stehen, wenn Schritt 3 scheiterte - eine halb ausgeführte
        # Fertigkeit hinterließ einen Zustand, den niemand bestellt hatte und
        # den man von Hand suchen musste. VaultTools.transaktion() nimmt bei
        # einer Ausnahme alles zurück, was in ihr geschrieben wurde.
        try:
            with self.tools.transaktion(f"Fertigkeit {name}", "fertigkeit_ausfuehren"):
                for nummer, schritt in enumerate(fertigkeit.schritte, 1):
                    aufgeloest = schritt.aufloesen(werte, ergebnisse)
                    offen = schritt.offene_platzhalter(aufgeloest)
                    if offen:
                        # Lieber abbrechen als "{ergebnis}" wörtlich in eine Notiz schreiben.
                        protokoll.append(f"{nummer}. {schritt.werkzeug} → ABBRUCH: "
                                         f"unersetzte Platzhalter {', '.join(offen)}")
                        abbruch = (f"Schritt {nummer}: "
                                   f"{{{'}, {'.join(offen)}}} konnte nicht gefüllt werden")
                        raise _SchrittFehlschlag(abbruch)
                    ergebnis = self.tools.ausfuehren(schritt.werkzeug, aufgeloest)
                    ergebnisse.append(ergebnis)
                    protokoll.append(f"{nummer}. {schritt.werkzeug} → "
                                     f"{'ok' if ergebnis.ok else 'FEHLER'}: {ergebnis.meldung}")
                    if not ergebnis.ok:
                        abbruch = f"Schritt {nummer} ({schritt.werkzeug}): {ergebnis.meldung}"
                        raise _SchrittFehlschlag(abbruch)
        except _SchrittFehlschlag:
            return ToolResult(
                False,
                f"Fertigkeit '{name}' abgebrochen – {abbruch}. Bereits erfolgte "
                f"Schreibvorgänge wurden zurückgenommen, die Vault ist unverändert.",
                {"protokoll": protokoll, "zurueckgerollt": True,
                 "hilfe": MUSTER_HILFE if "gefüllt" in abbruch else ""})

        return ToolResult(True, f"Fertigkeit '{name}' vollständig ausgeführt.",
                          {"protokoll": protokoll})

    # ------------------------------------------------------------- Verwaltung
    # Bis hierher konnte man Fertigkeiten nur anlegen. Aendern, loeschen oder
    # umbenennen ging ausschliesslich von Hand im Dateisystem - der Agent
    # konnte seine eigenen Fehlgriffe also nicht selbst wieder aufraeumen.

    def zeigen(self) -> ToolResult:
        """Alle Fertigkeiten mit Zustand - auch die gesperrten, mit Grund."""
        self.laden()
        eintraege = []
        for f in self.alle.values():
            eintraege.append({"name": f.name, "zustand": "einsatzbereit",
                              "parameter": f.parameter,
                              "beschreibung": f.beschreibung,
                              "schritte": [sch.signatur_zeile() for sch in f.schritte]})
        for f in self.defekt.values():
            eintraege.append({"name": f.name, "zustand": "gesperrt",
                              "parameter": f.parameter,
                              "beschreibung": f.beschreibung,
                              "probleme": f.probleme,
                              "schritte": [sch.signatur_zeile() for sch in f.schritte]})
        if not eintraege:
            return ToolResult(True, "Es gibt noch keine eigenen Fertigkeiten.",
                              {"fertigkeiten": [], "hilfe": MUSTER_HILFE})
        meldung = (f"{len(self.alle)} einsatzbereit, {len(self.defekt)} gesperrt. "
                   + self.pruefbericht())
        return ToolResult(True, meldung, {"fertigkeiten": eintraege})

    def loeschen(self, name: str, grund: str = "") -> ToolResult:
        """Verschiebt eine Fertigkeit in den Papierkorb - nicht endgueltig."""
        if not self.cfg.self_extension:
            return ToolResult(False, "Selbsterweiterung ist deaktiviert (SELF_EXTENSION=false).")
        name = str(name or "").strip().lower().replace(" ", "_")
        pfad = self._datei(name)
        if not pfad:
            return ToolResult(False, f"Fertigkeit '{name}' ist nicht bekannt.",
                              {"vorhanden": sorted(set(self.alle) | set(self.defekt))})
        # Ueber notiz_loeschen, damit Papierkorb, Journal und /rueckgaengig
        # genauso greifen wie bei jeder anderen Notiz.
        rel = pfad.relative_to(self.tools.root).as_posix()
        ergebnis = self.tools.ausfuehren("notiz_loeschen", {"name": rel})
        if not ergebnis.ok:
            ergebnis = self.tools.ausfuehren("notiz_loeschen", {"name": pfad.stem})
        if not ergebnis.ok:
            return ToolResult(False, f"Fertigkeit '{name}' nicht loeschbar: {ergebnis.meldung}")
        self.laden()
        zusatz = f" Grund: {grund}" if str(grund).strip() else ""
        return ToolResult(True, f"Fertigkeit '{name}' in den Papierkorb verschoben."
                                f"{zusatz} Rueckholbar mit /rueckgaengig.",
                          {"datei": pfad.name, "grund": grund})

    def umbenennen(self, alt: str, neu: str) -> ToolResult:
        """Benennt eine Fertigkeit um, ohne sie neu definieren zu muessen."""
        if not self.cfg.self_extension:
            return ToolResult(False, "Selbsterweiterung ist deaktiviert (SELF_EXTENSION=false).")
        alt = str(alt or "").strip().lower().replace(" ", "_")
        neu_name = safe_filename(str(neu or "").strip().lower().replace(" ", "_"))
        if not neu_name:
            return ToolResult(False, "Kein gueltiger neuer Name angegeben.")
        if neu_name in TOOL_NAMES:
            return ToolResult(False, f"'{neu_name}' ist bereits ein eingebautes Werkzeug.")
        quelle = self._datei(alt)
        if not quelle:
            return ToolResult(False, f"Fertigkeit '{alt}' ist nicht bekannt.",
                              {"vorhanden": sorted(set(self.alle) | set(self.defekt))})
        ziel = self.ordner / f"{neu_name}.md"
        if ziel.exists():
            return ToolResult(False, f"Es gibt bereits eine Fertigkeit '{neu_name}'.")
        try:
            roh = quelle.read_text(encoding="utf-8")
            # Der Name steht an drei Stellen: im Dateinamen, im Frontmatter und
            # in der Ueberschrift. Bleibt eine davon stehen, laedt die Datei
            # spaeter wieder unter dem alten Namen.
            roh = re.sub(r"(?m)^fertigkeit:\s*.*$", f"fertigkeit: {neu_name}", roh, count=1)
            roh = re.sub(r"(?m)^# Fertigkeit:.*$", f"# Fertigkeit: {neu_name}", roh, count=1)
            ziel.write_text(roh, encoding="utf-8")
            quelle.unlink()
        except OSError as exc:
            return ToolResult(False, f"Umbenennen fehlgeschlagen: {exc}")
        self.laden()
        return ToolResult(True, f"Fertigkeit '{alt}' heisst jetzt '{neu_name}'.",
                          {"datei": ziel.name, "alt": alt, "neu": neu_name})

    def _datei(self, name: str) -> Optional[Path]:
        """Pfad einer Fertigkeit - egal ob sie intakt oder gesperrt ist."""
        self.laden()
        eintrag = self.alle.get(name) or self._defekt.get(name)
        if eintrag and eintrag.pfad and eintrag.pfad.exists():
            return eintrag.pfad
        kandidat = self.ordner / f"{safe_filename(name)}.md"
        return kandidat if kandidat.exists() else None

    def uebersicht(self) -> str:
        if not self.alle:
            return "(noch keine eigenen Fertigkeiten)"
        return "\n".join(f"- {f.signatur()}" for f in self.alle.values())

    def pruefbericht(self) -> str:
        """Was ist intakt, was ist gesperrt und warum - für /fertigkeiten."""
        zeilen: List[str] = []
        if self.alle:
            zeilen.append("**Einsatzbereit**")
            for f in self.alle.values():
                zeilen.append(f"- ✔ {f.signatur()}")
        else:
            zeilen.append("(noch keine einsatzbereiten Fertigkeiten)")
        if self.defekt:
            zeilen.append("")
            zeilen.append("**Gesperrt – werden nicht ausgeführt**")
            for f in self.defekt.values():
                datei = f.pfad.name if f.pfad else f.name
                zeilen.append(f"- ✖ {f.name}  `{datei}`")
                for problem in f.probleme[:3]:
                    zeilen.append(f"    - {problem}")
            zeilen.append("")
            zeilen.append(MUSTER_HILFE)
            zeilen.append(f"Datei im Ordner `{self.cfg.skills_folder}` korrigieren oder "
                          f"löschen, dann greift sie wieder.")
        return "\n".join(zeilen)


def _bezuege_umschreiben(text: str, zuordnung: Dict[int, int], verworfen: Set[int],
                         neu_nummer: int, werkzeug: str):
    """Zieht {ergebnisN} auf die neue Schrittnummer nach.

    Nötig, weil verworfene Schritte die Zählung verschieben. Drei Fälle:
      N ist ein aufgenommener Schritt   -> auf seine neue Nummer umschreiben
      N ist ein VERWORFENER Schritt     -> harter Fehler; das Ergebnis, auf das
                                           sich der Schritt stützt, gibt es nie
      N ist gar kein Schritt            -> unverändert lassen, _platzhalter_pruefen
                                           meldet den Vorgriff mit seiner Formulierung
    """
    probleme: List[str] = []

    def ersetzen(treffer: "re.Match") -> str:
        alt = int(treffer.group(1))
        if alt in zuordnung:
            return "{ergebnis" + str(zuordnung[alt]) + "}"
        if alt in verworfen:
            probleme.append(
                f"Schritt {neu_nummer} {werkzeug}: {{ergebnis{alt}}} bezieht sich auf "
                f"Schritt {alt}, der verworfen werden musste - dieses Ergebnis wird es "
                f"nie geben")
        return treffer.group(0)

    return ERGEBNIS_NUMMER_RE.sub(ersetzen, text), probleme


def _pflichtparameter(werkzeug: str) -> List[str]:
    return list(TOOL_REQUIRED.get(werkzeug, []))


def _argumente_schreiben(argumente: Dict[str, str]) -> str:
    """Argumente wieder in die Textform bringen - nach der Reparatur."""
    teile = []
    for schluessel, wert in argumente.items():
        wert = str(wert)
        if re.fullmatch(r"-?\d+(\.\d+)?", wert) or wert.lower() in ("true", "false"):
            teile.append(f"{schluessel}={wert}")
        else:
            teile.append(f'{schluessel}="{wert}"')
    return ", ".join(teile)


def _platzhalter_pruefen(nummer: int, werkzeug: str, argumente: Dict[str, str],
                         parameter: Sequence[str],
                         bisherige_werkzeuge: Sequence[str]):
    """Prüft jeden Platzhalter im Schritt - und repariert, was eindeutig ist.

    Drei Fälle aus dem echten Fehlversuch:
      {neue_fertigkeit}  - frei erfunden          -> Ablehnung (war schon so)
      {ergebnis}         - ohne Nummer            -> Reparatur auf den Vorschritt
      {ergebnis1} in name= nach einer Suche       -> Ablehnung, war vorher erlaubt
    """
    probleme: List[str] = []
    repariert: List[str] = []
    ausgabe = dict(argumente)

    for schluessel, wert in argumente.items():
        text = str(wert)
        for platzhalter in PLATZHALTER_RE.findall(text):
            if platzhalter in parameter:
                continue
            treffer = ERGEBNIS_RE.match(platzhalter)
            if not treffer:
                probleme.append(f"Schritt {nummer} {werkzeug}: Platzhalter "
                                f"{{{platzhalter}}} ist nicht in der Parameterliste")
                continue

            ziffern = treffer.group(1)
            if not ziffern:
                # {ergebnis} ohne Nummer: eindeutig, wenn es einen Vorschritt gibt
                if nummer <= 1:
                    probleme.append(f"Schritt {nummer} {werkzeug}: {{ergebnis}} im ERSTEN "
                                    f"Schritt – es gibt noch kein Ergebnis, auf das es "
                                    f"sich beziehen könnte")
                    continue
                text = text.replace("{ergebnis}", "{ergebnis" + str(nummer - 1) + "}")
                ausgabe[schluessel] = text
                repariert.append(f"Schritt {nummer}: {{ergebnis}} → "
                                 f"{{ergebnis{nummer - 1}}} (auf den Vorschritt bezogen)")
                ziffern = str(nummer - 1)

            quelle = int(ziffern)
            if quelle < 1 or quelle >= nummer:
                probleme.append(f"Schritt {nummer} {werkzeug}: {{ergebnis{quelle}}} verweist "
                                f"auf Schritt {quelle}, der zu diesem Zeitpunkt noch kein "
                                f"Ergebnis hat")
                continue

            quell_werkzeug = (bisherige_werkzeuge[quelle - 1]
                              if quelle - 1 < len(bisherige_werkzeuge) else "")
            if schluessel in BEZEICHNER_PARAMETER and quell_werkzeug not in LIEFERT_NOTIZ:
                probleme.append(
                    f"Schritt {nummer} {werkzeug}: {schluessel}=\"{{ergebnis{quelle}}}\" – "
                    f"Schritt {quelle} ({quell_werkzeug or '?'}) liefert keinen Notiznamen, "
                    f"sondern Text. Als Notizname gesucht, findet sich das nie")

    return ausgabe, probleme, repariert


def _bezeichner_aus(ergebnis: ToolResult) -> str:
    """Der Notizname aus einem Werkzeugergebnis - für name/titel/ziel."""
    pfad = ergebnis.daten.get("pfad") or ergebnis.daten.get("nachher") or ""
    if pfad:
        return Path(str(pfad)).stem
    return str(ergebnis.daten.get("name") or ergebnis.daten.get("titel") or "").strip()


def _inhalt_aus(ergebnis: ToolResult) -> str:
    """Der Text aus einem Werkzeugergebnis - für inhalt/text.

    Trefferlisten werden lesbar aufbereitet statt als roher dict-Abdruck
    eingesetzt; sonst steht Python-Syntax in der Notiz.
    """
    inhalt = ergebnis.daten.get("inhalt")
    if inhalt:
        return str(inhalt)[:4000]
    treffer = ergebnis.daten.get("treffer") or ergebnis.daten.get("notizen")
    if isinstance(treffer, list) and treffer:
        zeilen = []
        for eintrag in treffer[:20]:
            if isinstance(eintrag, dict):
                titel = (eintrag.get("titel") or eintrag.get("name")
                         or eintrag.get("pfad") or "")
                # Ein Wikilink zeigt auf den Notiznamen, nicht auf den Pfad:
                # [[Lernen/Kontrolle.md]] wäre in Obsidian ein toter Link.
                titel = Path(str(titel)).stem or str(titel)
                auszug = " ".join(str(eintrag.get("auszug") or eintrag.get("text")
                                      or "").split())
                zeilen.append(f"- [[{titel}]]" + (f" – {auszug[:200]}" if auszug else ""))
            else:
                zeilen.append(f"- {eintrag}")
        return "\n".join(zeilen)[:4000]
    return str(ergebnis.daten.get("pfad") or ergebnis.meldung)[:4000]


def _schritt_als_text(roh: object) -> object:
    """Ein Schritt als Aufruf-Text - für 'schritte' liefern Modelle oft
    Objekte statt Zeichenketten, z. B.

        {"werkzeug": "notiz_erstellen", "argumente": {"titel": "X"}}

    statt

        'notiz_erstellen(titel="X")'

    - weil das dem übrigen JSON-Stil der Antwort entspricht. Genau das kam
    im Protokoll vom 03.09. vor und wurde bis hierhin als "Nicht lesbar"
    verworfen. Beide Formen führen ab hier zum selben internen String, damit
    die eigentliche Prüfung (SCHRITT_RE, TOOL_NAMES, Platzhalter) unverändert
    bleibt - es wird nur das Eingabeformat erweitert, keine Regel gelockert.
    """
    if not isinstance(roh, dict):
        return roh
    werkzeug = str(roh.get("werkzeug") or roh.get("tool") or roh.get("name") or "").strip()
    argumente = (roh.get("argumente") or roh.get("arguments")
                or roh.get("parameter") or roh.get("args") or {})
    if not isinstance(argumente, dict):
        argumente = {}
    teile = []
    for schluessel, wert in argumente.items():
        # Der Zeilen-Parser unten (_argumente_lesen/_teile) kennt kein \" -
        # ein doppeltes Anführungszeichen im Wert wird deshalb ersetzt statt
        # escaped, damit der erzeugte String für ihn sicher lesbar bleibt.
        text = str(wert).replace('"', "'")
        teile.append(f'{schluessel}="{text}"')
    return f"{werkzeug}({', '.join(teile)})"


def _argumente_lesen(roh: str) -> Dict[str, str]:
    """Zerlegt `titel="X", anzahl=3` - respektiert Anführungszeichen."""
    argumente: Dict[str, str] = {}
    for teil in _teile(roh):
        if "=" not in teil:
            continue
        schluessel, _, wert = teil.partition("=")
        schluessel = schluessel.strip()
        wert = wert.strip()
        if len(wert) >= 2 and wert[0] == wert[-1] and wert[0] in "\"'":
            wert = wert[1:-1]
        if re.fullmatch(r"\w+", schluessel):
            argumente[schluessel] = wert
    return argumente


def _teile(roh: str) -> List[str]:
    teile, aktuell, quote, tiefe = [], [], "", 0
    for zeichen in roh:
        if quote:
            if zeichen == quote:
                quote = ""
            aktuell.append(zeichen)
        elif zeichen in "\"'":
            quote = zeichen
            aktuell.append(zeichen)
        elif zeichen in "[({":
            tiefe += 1
            aktuell.append(zeichen)
        elif zeichen in "])}":
            tiefe -= 1
            aktuell.append(zeichen)
        elif zeichen == "," and tiefe == 0:
            teile.append("".join(aktuell))
            aktuell = []
        else:
            aktuell.append(zeichen)
    if aktuell:
        teile.append("".join(aktuell))
    return [t.strip() for t in teile if t.strip()]
