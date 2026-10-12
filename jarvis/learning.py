"""Systemlernen: beobachten, bewerten, korrigieren – über Sitzungen hinweg.

Dein Ansatz, übersetzt in Mechanik:

* **Beobachtung** – jede Aktion erzeugt ein messbares Ergebnis (hat der Schreib-
  vorgang funktioniert? wurden Quellen gefunden? hat der Nutzer widersprochen?).
  Ohne Beobachtung kein Lernen; die Qualität der Rückmeldung begrenzt alles.
* **Lektion** – eine Korrektur wird zu einem dauerhaften Satz in der Vault.
  Lektionen werden bei jeder neuen Frage semantisch gesucht und in den
  Systemprompt gelegt. Das ist der Teil, der über die Sitzung hinaus wirkt:
  nicht die Gewichte ändern sich, sondern das System um das Modell herum.
* **Wissenslücke** – Fragen ohne brauchbare Quelle werden gesammelt. Daraus
  entsteht die Themenliste, an der der Agent als Nächstes arbeiten kann.
* **Reflexion** – periodischer Rückblick auf die Beobachtungen mit konkreten
  Vorschlägen. Bericht, keine stille Änderung.

Alles landet als Markdown in der Vault - lesbar, versionierbar, und für den
Agenten selbst wieder auffindbar.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from .config import settings

KORREKTUR_MUSTER = re.compile(
    r"\b(falsch|stimmt nicht|nicht korrekt|fehler|das ist nicht|nein,|"
    r"hast du nicht|funktioniert nicht|klappt nicht|schon wieder|"
    r"nicht was ich|meinte ich nicht|dagegen|korrigier\w*)\b",
    re.IGNORECASE,
)
LOB_MUSTER = re.compile(r"\b(perfekt|genau so|richtig so|passt|danke, das war|sehr gut|gut gemacht)\b",
                        re.IGNORECASE)


@dataclass
class Beobachtung:
    """Ein Zug: Was wurde gefragt, was getan, was kam dabei heraus."""
    zeit: str
    frage: str
    art: str                       # rag | plan | struktur | fehler
    quellen: List[str] = field(default_factory=list)
    werkzeuge: List[str] = field(default_factory=list)
    erfolge: int = 0
    fehlschlaege: int = 0
    dauer: float = 0.0
    abgebrochen: str = ""
    bewertung: str = "offen"       # offen | positiv | negativ

    @property
    def geglueckt(self) -> bool:
        return self.fehlschlaege == 0 and not self.abgebrochen and self.bewertung != "negativ"


VORLAEUFIG = "vorlaeufig"
BESTAETIGT = "bestaetigt"
VERWORFEN = "verworfen"

# Wann eine Hypothese zur Regel wird und wann sie fliegt. Bewusst träge:
# Deine Notiz (§4.2) nennt das die Lernrate - zu hoch, und das System
# überreagiert auf einen einzelnen verrauschten Datenpunkt.
MINDEST_VERWENDUNGEN = 3
BESTAETIGUNGSQUOTE = 0.6
VERWERFUNGSQUOTE = 0.3
MINDEST_FUER_VERWURF = 5

_ZAEHLER_RE = re.compile(
    r"status:\s*(\w+)\s*·\s*verwendet:\s*(\d+)\s*·\s*bewaehrt:\s*(\d+)")


@dataclass
class Bewaehrungszeile:
    """Eine Markdown-Zeile, die ihre eigene Bewährungsbilanz mitführt.

    Gemeinsame Grundlage von Lektionen und Sitzungsfakten. Beide brauchen
    dieselbe Frage - hat sich das hier je bewährt? - und dürfen sie nicht
    zweimal verschieden beantworten. Das Dateiformat der Lektionen bleibt
    davon unberührt: Die Marke wird hier genauso gebaut wie vorher.
    """

    text: str
    status: str = VORLAEUFIG
    verwendet: int = 0
    bewaehrt: int = 0
    notiz: str = ""

    @property
    def quote(self) -> float:
        return self.bewaehrt / self.verwendet if self.verwendet else 0.0

    @property
    def brauchbar(self) -> bool:
        return self.status != VERWORFEN

    def bewerten(self) -> bool:
        """Status neu bestimmen. True, wenn er sich geändert hat."""
        vorher = self.status
        if (self.verwendet >= MINDEST_FUER_VERWURF
                and self.quote < VERWERFUNGSQUOTE):
            self.status = VERWORFEN
        elif (self.verwendet >= MINDEST_VERWENDUNGEN
                and self.quote >= BESTAETIGUNGSQUOTE):
            self.status = BESTAETIGT
        return self.status != vorher

    # ------------------------------------------------------------ Die Marke
    def marke(self) -> str:
        return (f"status: {self.status} · verwendet: {self.verwendet} "
                f"· bewaehrt: {self.bewaehrt}")

    def kommentar(self) -> str:
        """Inhalt des HTML-Kommentars: Marke, dahinter der freie Rest."""
        rest = _ZAEHLER_RE.sub("", self.notiz).strip(" ·")
        return self.marke() + (f" · {rest}" if rest else "")

    def marke_lesen(self, kommentar: str) -> None:
        """Zähler aus einem gefundenen Kommentar übernehmen."""
        self.notiz = (kommentar or "").strip()
        treffer = _ZAEHLER_RE.search(self.notiz)
        if treffer:
            self.status = treffer.group(1)
            self.verwendet = int(treffer.group(2))
            self.bewaehrt = int(treffer.group(3))


def zeile_zerlegen(zeile: str) -> tuple:
    """'- Text  <!-- marke -->' -> ('Text', 'marke'). Ohne Kommentar: ('Text', '')."""
    roh = zeile[2:] if zeile.startswith("- ") else zeile
    kommentar = re.search(r"<!--(.*?)-->", roh)
    text = re.sub(r"<!--.*?-->", "", roh).strip()
    return text, (kommentar.group(1).strip() if kommentar else "")


@dataclass
class Lektion(Bewaehrungszeile):
    """Eine gelernte Regel samt Bewährungsbilanz.

    Vorher war eine Lektion nur eine Textzeile. Damit war nicht feststellbar,
    ob sie je geholfen hat - und eine schädliche Regel blieb für immer im
    Kontext stehen.
    """

    @classmethod
    def aus_zeile(cls, zeile: str) -> "Lektion":
        """Liest neue UND alte Zeilen - alte gelten als unbewährt."""
        text, kommentar = zeile_zerlegen(zeile)
        eintrag = cls(text=text)
        if kommentar:
            eintrag.marke_lesen(kommentar)
        return eintrag

    def als_zeile(self) -> str:
        return f"- {self.text}  <!-- {self.kommentar()} -->"


class Lernwerk:
    """Verwaltet Beobachtungen, Lektionen, Wissenslücken und Kennzahlen."""

    def __init__(self, agent, cfg=settings):
        self.agent = agent
        self.cfg = cfg
        self.beobachtungen: List[Beobachtung] = []
        self._lektionen_cache: Optional[List[str]] = None
        # Letzter verschluckter Schreibfehler. Lernen ist optional und darf den
        # Agenten nie anhalten - aber lautlos scheitern soll es auch nicht.
        self.letzter_fehler: str = ""

    # ------------------------------------------------------------- Ablageorte
    @property
    def ordner(self) -> Path:
        return self.agent.structure.vault_root / self.cfg.learning_folder

    def _datei(self, name: str) -> Path:
        return self.ordner / name

    def _anhaengen(self, name: str, zeile: str, kopf: str) -> None:
        """Schreibt direkt - bewusst ohne Werkzeugebene, damit Lernen nie
        am Datei-Limit oder an einem Trockenlauf scheitert."""
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            pfad = self._datei(name)
            if not pfad.exists():
                pfad.write_text(kopf, encoding="utf-8")
            with open(pfad, "a", encoding="utf-8") as fh:
                fh.write(zeile.rstrip() + "\n")
        except OSError as exc:
            # Stillschweigen hier hieße: Lektionen gehen verloren und niemand
            # merkt es. Der Grund gehört wenigstens in den Zustand.
            self.letzter_fehler = f"Lernnotiz '{name}' nicht schreibbar: {exc}"

    # ---------------------------------------------------------- Beobachtungen
    def beobachten(self, frage: str, antwort, dauer: float = 0.0,
                   abgebrochen: str = "") -> Beobachtung:
        werkzeuge = [c.werkzeug for c in getattr(antwort, "tool_calls", [])]
        erfolge = sum(1 for c in getattr(antwort, "tool_calls", []) if c.ergebnis.ok)
        beobachtung = Beobachtung(
            zeit=dt.datetime.now().isoformat(timespec="seconds"),
            frage=frage[:300],
            art=getattr(antwort, "kind", "rag"),
            quellen=list(getattr(antwort, "sources", []))[:8],
            werkzeuge=werkzeuge,
            erfolge=erfolge,
            fehlschlaege=len(werkzeuge) - erfolge,
            dauer=round(dauer, 1),
            abgebrochen=abgebrochen,
        )
        self.beobachtungen.append(beobachtung)
        self._protokollieren(beobachtung)

        # Frage ohne jede Quelle und ohne Werkzeugeinsatz = Wissenslücke
        if not beobachtung.quellen and not werkzeuge and beobachtung.art == "rag":
            self.wissensluecke(frage)
        return beobachtung

    def _protokollieren(self, beobachtung: Beobachtung) -> None:
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            with open(self.ordner / ".beobachtungen.jsonl", "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(beobachtung), ensure_ascii=False) + "\n")
        except OSError as exc:
            self.letzter_fehler = f"Beobachtung nicht protokollierbar: {exc}"

    # --------------------------------------------------------------- Feedback
    def rueckmeldung_erkennen(self, text: str) -> Optional[str]:
        """Erkennt, ob die Nachricht eine Korrektur der letzten Antwort ist."""
        if not self.beobachtungen:
            return None
        if KORREKTUR_MUSTER.search(text):
            return "negativ"
        if LOB_MUSTER.search(text):
            return "positiv"
        return None

    def rueckmeldung_verarbeiten(self, text: str, bewertung: str) -> Optional[str]:
        """Macht aus einer Korrektur eine dauerhafte Lektion."""
        letzte = self.beobachtungen[-1]
        letzte.bewertung = bewertung
        if bewertung != "negativ":
            return None

        lektion = self._lektion_formulieren(letzte, text)
        if not lektion:
            return None
        self.lektion_speichern(lektion, anlass=letzte.frage)
        return lektion

    def _lektion_formulieren(self, beobachtung: Beobachtung, kritik: str) -> str:
        prompt = f"""Der Nutzer hat eine Antwort bemängelt. Formuliere daraus EINE
allgemeingültige Regel für künftiges Verhalten - kein Bezug auf diesen Einzelfall,
keine Entschuldigung, keine Erklärung.

URSPRÜNGLICHE FRAGE: {beobachtung.frage}
EINGESETZTE WERKZEUGE: {', '.join(beobachtung.werkzeuge) or 'keine'}
KRITIK DES NUTZERS: {kritik}

Antworte mit einem einzigen Satz auf {self.cfg.language}, im Imperativ.
Beispiel: "Prüfe vor dem Anlegen einer Notiz mit notizen_suchen, ob es sie schon gibt."""
        try:
            satz = self.agent.llm.chat([{"role": "user", "content": prompt}],
                                       temperature=0.1, num_predict=120, schnell=True)
        except Exception:
            return ""
        satz = satz.strip().split("\n")[0].strip(' "-•')
        return satz if 15 < len(satz) < 300 else ""

    def lektion_speichern(self, lektion: str, anlass: str = "") -> None:
        for vorhandene in self.lektionen():
            if _aehnlich(vorhandene, lektion):
                return
        zeitpunkt = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        self._anhaengen(
            "Lektionen.md",
            f"- {lektion}  <!-- {zeitpunkt}"
            + (f" · Anlass: {anlass[:80]}" if anlass else "") + " -->",
            kopf=(
                "---\ntags:\n  - lernen\n  - systemlernen\n---\n\n"
                "# Lektionen\n\n"
                "Regeln, die aus Korrekturen entstanden sind. Sie werden bei jeder Frage\n"
                "gesucht und in den Systemprompt gelegt – hier zu löschen heißt vergessen.\n\n"
            ),
        )
        self._lektionen_cache = None

    def lektionen(self, nur_brauchbare: bool = True) -> List[str]:
        """Die Lektionstexte. Verworfene bleiben in der Datei, aber draußen.

        'nur_brauchbare' filtert, was sich nachweislich nicht bewährt hat -
        sonst füllt sich der Kontext mit Regeln, die die Trefferquote senken.
        Genau davor warnt deine eigene Notiz (Teil 4, §4.3).
        """
        return [e.text for e in self.lektionen_voll()
                if not (nur_brauchbare and e.status == VERWORFEN)]

    def lektionen_voll(self) -> List["Lektion"]:
        if self._lektionen_cache is not None:
            return self._lektionen_cache
        pfad = self._datei("Lektionen.md")
        eintraege: List[Lektion] = []
        if pfad.exists():
            try:
                roh = pfad.read_text(encoding="utf-8")
            except OSError as exc:
                self.letzter_fehler = f"Lektionen nicht lesbar: {exc}"
                roh = ""
            for zeile in roh.splitlines():
                if zeile.startswith("- "):
                    eintraege.append(Lektion.aus_zeile(zeile))
        self._lektionen_cache = eintraege
        return eintraege

    # ------------------------------------------------- Bewährung der Lektionen
    def lektion_verwendet(self, texte: Sequence[str]) -> None:
        """Merkt, welche Lektionen in den Prompt gewandert sind."""
        self._offen = [t for t in texte if t]

    def lektionen_bewerten(self, geglueckt: bool) -> None:
        """Nach der Aufgabe: Haben die eingesetzten Lektionen geholfen?

        Das ist der Tiefpass aus deiner Notiz (§4.2): Eine einzelne Korrektur
        darf keine harte Regel erzeugen - sonst ist die "Lernrate" zu hoch und
        das System schwingt auf einem verrauschten Datenpunkt. Erst nach
        mehrfacher Bewährung wird aus 'vorläufig' ein 'bestätigt'.
        """
        offen = getattr(self, "_offen", None)
        if not offen:
            return
        self._offen = []
        alle = self.lektionen_voll()
        geaendert = False
        for eintrag in alle:
            if eintrag.text not in offen:
                continue
            eintrag.verwendet += 1
            if geglueckt:
                eintrag.bewaehrt += 1
            neuer = eintrag.bewerten()
            geaendert = True
            if neuer:
                self.agent.tools.on_event(
                    "lernen", f"Lektion ist jetzt '{eintrag.status}': {eintrag.text[:60]}")
        if geaendert:
            self._lektionen_schreiben(alle)

    def _lektionen_schreiben(self, eintraege: Sequence["Lektion"]) -> bool:
        pfad = self._datei("Lektionen.md")
        kopf = (
            "---\ntags:\n  - lernen\n  - systemlernen\n---\n\n"
            "# Lektionen\n\n"
            "Regeln, die aus Korrekturen entstanden sind. Sie werden bei jeder Frage\n"
            "gesucht und in den Systemprompt gelegt – hier zu löschen heißt vergessen.\n\n"
            "Status: `vorlaeufig` = Hypothese, `bestaetigt` = hat sich bewährt,\n"
            "`verworfen` = hat mehrfach nicht geholfen und wird nicht mehr benutzt.\n\n"
        )
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            pfad.write_text(kopf + "\n".join(e.als_zeile() for e in eintraege) + "\n",
                            encoding="utf-8")
            self._lektionen_cache = list(eintraege)
            return True
        except OSError as exc:
            self.letzter_fehler = f"Lektionen nicht schreibbar: {exc}"
            return False

    def lektionsbilanz(self) -> str:
        alle = self.lektionen_voll()
        if not alle:
            return "(noch keine Lektionen)"
        nach_status: Dict[str, List[Lektion]] = {}
        for eintrag in alle:
            nach_status.setdefault(eintrag.status, []).append(eintrag)
        zeilen = []
        for status in (BESTAETIGT, VORLAEUFIG, VERWORFEN):
            gruppe = nach_status.get(status) or []
            if not gruppe:
                continue
            zeilen.append(f"**{status}** ({len(gruppe)})")
            for eintrag in gruppe:
                zeilen.append(f"- {eintrag.text}  ·  {eintrag.bewaehrt}/{eintrag.verwendet}")
            zeilen.append("")
        return "\n".join(zeilen).rstrip()

    def relevante_lektionen_voll(self, frage: str,
                                 anzahl: Optional[int] = None) -> List["Lektion"]:
        """Wie `relevante_lektionen`, aber mit Status und Bilanz - und OHNE
        die Verwendung zu buchen.

        Der Aufrufer entscheidet erst nach der Budgetierung, welche Lektionen
        wirklich im Prompt landen; nur die dürfen als 'verwendet' zählen.
        Vorher bekam eine Lektion auch dann einen Strich, wenn das Kürzen sie
        hinterher wieder herauswarf - das verfälscht ihre Bilanz.
        """
        from .index import tokens

        anzahl = anzahl or self.cfg.lessons_in_prompt
        brauchbar = [e for e in self.lektionen_voll() if e.status != VERWORFEN]
        if not brauchbar:
            return []

        gefragt = set(tokens(frage))
        # Bestätigte gehen vor vorläufigen: eine bewährte Regel ist mehr wert
        # als eine Hypothese, auch wenn die Hypothese wörtlich besser passt.
        bewertet = sorted(
            brauchbar,
            key=lambda e: (0 if e.status == BESTAETIGT else 1,
                           -len(gefragt & set(tokens(e.text))),
                           -e.quote),
        )
        return bewertet[:anzahl]

    def relevante_lektionen(self, frage: str, anzahl: Optional[int] = None) -> List[str]:
        """Die passendsten Lektionen für diese Frage - simpel per Wortüberlappung.

        Bewusst ohne Embedding-Aufruf: Lektionen sind kurz und wenige, und der
        Systemprompt darf keine zusätzliche Latenz kosten.
        """
        auswahl = [e.text for e in self.relevante_lektionen_voll(frage, anzahl)]
        self.lektion_verwendet(auswahl)
        return auswahl

    def lektionen_konsolidieren(self) -> str:
        """Fasst inhaltsgleiche Lektionen zusammen.

        Die Duplikatsprüfung beim Speichern ist bewusst streng: Zwei Regeln rein
        lexikalisch zu verschmelzen, ohne "vor dem Anlegen" und "vor dem Löschen"
        zu verwechseln, ist nicht sicher möglich - und eine fälschlich gelöschte
        Regel wiegt schwerer als eine doppelte. Deshalb passiert das Zusammenfassen
        hier, explizit und mit Modellurteil, statt heimlich beim Schreiben.
        """
        alle = self.lektionen()
        if len(alle) < 2:
            return "Nichts zu konsolidieren."
        prompt = f"""Hier sind Verhaltensregeln für einen Agenten. Fasse inhaltlich
gleiche Regeln zu je einer zusammen, lasse unterschiedliche unangetastet.
Ändere den Sinn nicht und erfinde nichts dazu.

REGELN:
{chr(10).join('- ' + l for l in alle)}

Antworte als JSON: {{"regeln": ["...", "..."]}}"""
        try:
            roh = self.agent.llm.chat([{"role": "user", "content": prompt}],
                                      temperature=0.0, format_json=True, schnell=True)
            neu = json.loads(roh).get("regeln") or []
        except Exception as exc:
            return f"Konsolidierung fehlgeschlagen: {exc}"

        neu = [str(r).strip() for r in neu if 15 < len(str(r).strip()) < 300]
        if not neu or len(neu) >= len(alle):
            return f"Keine Zusammenfassung nötig ({len(alle)} Regeln)."

        kopf = ("---\ntags:\n  - lernen\n  - systemlernen\n---\n\n# Lektionen\n\n"
                "Regeln, die aus Korrekturen entstanden sind.\n\n")
        zeitpunkt = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        try:
            self._datei("Lektionen.md").write_text(
                kopf + "\n".join(f"- {r}  <!-- konsolidiert {zeitpunkt} -->" for r in neu) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            return f"Nicht speicherbar: {exc}"
        self._lektionen_cache = None
        return f"{len(alle)} Regeln zu {len(neu)} zusammengefasst."

    # ---------------------------------------------------------- Wissenslücken
    def wissensluecke(self, frage: str) -> None:
        zeitpunkt = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        self._anhaengen(
            "Wissensluecken.md",
            f"- [ ] {frage.strip()[:200]}  <!-- {zeitpunkt} -->",
            kopf=(
                "---\ntags:\n  - lernen\n  - luecke\n---\n\n"
                "# Wissenslücken\n\n"
                "Fragen, zu denen die Vault nichts hergab. Abgehakt = bearbeitet.\n\n"
            ),
        )

    def offene_luecken(self) -> List[str]:
        pfad = self._datei("Wissensluecken.md")
        if not pfad.exists():
            return []
        return [
            re.sub(r"<!--.*?-->", "", z[6:]).strip()
            for z in pfad.read_text(encoding="utf-8").splitlines()
            if z.startswith("- [ ] ")
        ]

    # -------------------------------------------------------------- Kennzahlen
    def kennzahlen(self) -> Dict[str, object]:
        gesamt = len(self.beobachtungen)
        geglueckt = sum(1 for b in self.beobachtungen if b.geglueckt)
        werkzeuge: Dict[str, int] = {}
        for b in self.beobachtungen:
            for w in b.werkzeuge:
                werkzeuge[w] = werkzeuge.get(w, 0) + 1
        dauern = [b.dauer for b in self.beobachtungen if b.dauer]
        return {
            "zuege": gesamt,
            "geglueckt": geglueckt,
            "quote": round(100 * geglueckt / gesamt) if gesamt else 0,
            "abbrueche": sum(1 for b in self.beobachtungen if b.abgebrochen),
            "mittlere_dauer": round(sum(dauern) / len(dauern), 1) if dauern else 0.0,
            "werkzeuge": werkzeuge,
            "lektionen": len(self.lektionen()),
            "offene_luecken": len(self.offene_luecken()),
        }

    # ---------------------------------------------------------------- Reflexion
    def reflektieren(self, schreiben: bool = True) -> str:
        """Rückblick auf die Sitzung mit konkreten Vorschlägen."""
        if not self.beobachtungen:
            return "Noch keine Beobachtungen in dieser Sitzung."
        kennzahlen = self.kennzahlen()
        protokoll = "\n".join(
            f"- [{b.art}] {b.frage[:120]} → Quellen: {len(b.quellen)}, "
            f"Werkzeuge: {', '.join(b.werkzeuge) or '–'}, "
            f"Fehlschläge: {b.fehlschlaege}, Bewertung: {b.bewertung}"
            for b in self.beobachtungen[-25:]
        )
        prompt = f"""Analysiere die Arbeit dieses Agenten und leite Verbesserungen ab.

KENNZAHLEN: {json.dumps(kennzahlen, ensure_ascii=False)}

BEOBACHTUNGEN:
{protokoll}

OFFENE WISSENSLÜCKEN:
{chr(10).join('- ' + l for l in self.offene_luecken()[:15]) or '- keine'}

BISHERIGE LEKTIONEN:
{chr(10).join('- ' + l for l in self.lektionen()[:15]) or '- keine'}

Schreibe auf {self.cfg.language} in Markdown, kurz und konkret:
## Was gut lief
## Wo es hakte
## Konkrete Verbesserungen (max. 5, umsetzbar)
## Vorschläge für die Vault-Struktur
Keine Wiederholung der Zahlen, keine Floskeln."""
        try:
            bericht = self.agent.llm.chat(
                [{"role": "system", "content": f"Du analysierst nüchtern auf {self.cfg.language}."},
                 {"role": "user", "content": prompt}],
                temperature=0.2, num_predict=900,
            )
        except Exception as exc:
            return f"Reflexion nicht möglich: {exc}"

        if schreiben:
            heute = dt.datetime.now().strftime("%Y-%m-%d_%H%M")
            try:
                self.ordner.mkdir(parents=True, exist_ok=True)
                (self.ordner / f"Reflexion_{heute}.md").write_text(
                    "---\ntags:\n  - lernen\n  - reflexion\n---\n\n"
                    f"# Reflexion {heute}\n\n"
                    f"```json\n{json.dumps(kennzahlen, ensure_ascii=False, indent=1)}\n```\n\n"
                    f"{bericht}\n",
                    encoding="utf-8",
                )
            except OSError as exc:
                self.letzter_fehler = f"Reflexionsbericht nicht ablegbar: {exc}"
        return bericht


def _aehnlich(a: str, b: str) -> bool:
    from .vault import similarity

    return similarity(a, b) > 0.88
