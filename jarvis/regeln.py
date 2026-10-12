"""Der Regelordner: verbindliche Vorgaben, die der Agent liest und nicht ändert.

Warum ein ORDNER IN DER VAULT und nicht Code
--------------------------------------------
Alles, was der Agent über seine Arbeitsweise wissen muss, stand bisher in
Python-Zeichenketten in `agent.py`. Das hat zwei Nachteile: Josh kann es nicht
ändern, ohne den Code anzufassen, und der Agent kann es nicht lesen. In der
Vault ist beides gelöst - die Regeln sind eine Notiz wie jede andere, in
Obsidian bearbeitbar, und stehen dem Agenten zur Verfügung.

Warum SCHREIBGESCHÜTZT
----------------------
Eine Regel, die der Regulierte selbst umschreiben kann, ist keine. Der Ordner
steht deshalb automatisch in `protected_list` - dieselbe Sperre, die schon
für `PROTECTED_FOLDERS` gilt und in `VaultTools._guard()` greift, also VOR
jedem Schreibvorgang und unabhängig davon, welches Werkzeug ihn auslöst.
Gelesen werden darf er uneingeschränkt.

Was hier NICHT passiert
-----------------------
Die Regeln werden nicht ausgelegt, nicht zusammengefasst und nicht bewertet.
Sie werden gelesen, nach Dateinamen sortiert und wörtlich in den Systemprompt
gehoben. Jede Verdichtung wäre eine Auslegung durch dasselbe Modell, das sich
daran halten soll.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from .config import settings
from .vault import strip_frontmatter


@dataclass
class Regelwerk:
    ordner: Optional[Path] = None
    dateien: List[Path] = field(default_factory=list)
    text: str = ""
    gekuerzt: bool = False
    zeichen: int = 0

    @property
    def vorhanden(self) -> bool:
        return bool(self.text.strip())

    def uebersicht(self) -> str:
        if self.ordner is None:
            return "Kein Regelordner eingestellt (REGEL_ORDNER in der .env)."
        if not self.dateien:
            return (f"Regelordner `{self.ordner}` ist leer oder fehlt.\n"
                    f"`/regeln anlegen` schreibt eine Startfassung hinein.")
        zeilen = [f"Regelordner: `{self.ordner}`  ·  schreibgeschützt",
                  f"{len(self.dateien)} Datei(en), {self.zeichen} Zeichen im Prompt",
                  ""]
        zeilen += [f"- {p.name}" for p in self.dateien]
        if self.gekuerzt:
            zeilen += ["", "⚠ Die Regeln überschreiten REGEL_MAX_ZEICHEN und wurden "
                           "für den Prompt gekürzt. Kürzen ist hier kein Komfort, "
                           "sondern Notwehr: Jede Zeile Regel verdrängt eine Zeile "
                           "Notiz aus dem Kontext."]
        return "\n".join(zeilen)


def laden(cfg=settings) -> Regelwerk:
    """Alle Markdown-Dateien des Regelordners einlesen, nach Namen sortiert.

    Die Sortierung ist der Grund, warum die Vorlage mit `10_`, `20_`, `30_`
    beginnt: So bestimmt Josh die Reihenfolge, in der die Regeln im Prompt
    stehen, ohne dass hier eine Rangfolge erfunden wird.
    """
    werk = Regelwerk(ordner=cfg.regel_pfad)
    if werk.ordner is None or not werk.ordner.is_dir():
        return werk

    try:
        dateien = sorted(p for p in werk.ordner.rglob("*.md") if p.is_file())
    except OSError:
        return werk

    teile: List[str] = []
    grenze = max(500, int(getattr(cfg, "regel_max_zeichen", 6000) or 6000))
    laenge = 0
    for pfad in dateien:
        try:
            roh = pfad.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _, rumpf = strip_frontmatter(roh)
        rumpf = rumpf.strip()
        if not rumpf:
            continue
        werk.dateien.append(pfad)
        block = f"--- {pfad.stem} ---\n{rumpf}"
        if laenge + len(block) > grenze:
            rest = grenze - laenge
            if rest > 200:
                teile.append(block[:rest].rstrip())
                laenge = grenze
            werk.gekuerzt = True
            break
        teile.append(block)
        laenge += len(block) + 2

    werk.text = "\n\n".join(teile).strip()
    werk.zeichen = len(werk.text)
    return werk


def promptblock(cfg=settings) -> str:
    """Der Textbaustein für den Systemprompt - leer, wenn es keine Regeln gibt."""
    werk = laden(cfg)
    if not werk.vorhanden:
        return ""
    return (
        "\nVERBINDLICHE ARBEITSREGELN (aus dem Regelordner deiner Vault). "
        "Sie gehen allem anderen in diesem Prompt vor. Widerspricht eine "
        "Anweisung hier einer allgemeinen Gepflogenheit, gilt die Regel:\n"
        f"{werk.text}\n"
    )


# ===================================================================== Vorlage
# Bewusst als Startfassung und nicht als endgültige Wahrheit: Was hier steht,
# stammt aus den Fehlern, die im Betrieb wirklich aufgetreten sind. Josh soll
# es ändern - das ist der Zweck des Ordners.
VORLAGEN = {
"00_Lies_mich.md": """---
titel: "Grundregeln – lies mich"
tags:
  - system
  - regeln
---

# Grundregeln

Was in diesem Ordner steht, ist für Zeus verbindlich und wird bei **jeder**
Anfrage in den Systemprompt gehoben. Der Ordner ist **schreibgeschützt**: Zeus
darf ihn lesen, aber nicht verändern – eine Regel, die der Regulierte selbst
umschreiben kann, ist keine.

## Wie du damit arbeitest

- Bearbeite die Dateien in Obsidian wie jede andere Notiz.
- Die Reihenfolge im Prompt ist die alphabetische Reihenfolge der Dateinamen.
  Deshalb die Nummern: `10_`, `20_`, `30_`.
- `/regeln` zeigt, was gerade gilt und wie viel Platz es im Kontext belegt.

## Der Preis

Der Kontext ist ein Nullsummenspiel. Jede Zeile Regel verdrängt eine Zeile
aus deinen Notizen. `REGEL_MAX_ZEICHEN` begrenzt das (Vorgabe 6000); wird die
Grenze überschritten, kürzt Zeus und sagt es in `/regeln`.

Kurze, konkrete Regeln wirken besser als lange Erklärungen. Eine Regel, die
nie zutrifft, kostet trotzdem Platz.
""",

"10_Werkzeuge.md": """---
titel: "Regeln: Werkzeuge richtig anwenden"
tags:
  - system
  - regeln
---

# Werkzeuge richtig anwenden

## Namen werden nicht geraten

Ein Notizname muss **buchstabengetreu** aus der Vault stammen. Vor jedem
schreibenden Zugriff auf eine bestehende Notiz: `struktur_anzeigen` oder
`notizen_suchen`, und den Namen von dort übernehmen.

Ein ungefähr passender Name wird bei schreibenden Werkzeugen abgelehnt. Das
ist Absicht: Eine Änderung an der falschen Notiz sieht aus wie Erfolg.

## Erst lesen, dann ändern

- Bevor du einen Block ersetzt: `bloecke_anzeigen` – die Nummer kommt von dort,
  nie aus einer Schätzung.
- Bevor du zusammenführst: `notizen_suchen`, und beide Namen prüfen.
- Bevor du ergänzt: `notiz_lesen`, damit du nichts wiederholst, was schon
  dasteht.

## Das passende Werkzeug

| Was du willst | Werkzeug |
|---|---|
| Etwas Neues anlegen | `notiz_erstellen` |
| Etwas anhängen | `notiz_ergaenzen` |
| Ein Kapitel austauschen | `abschnitt_ersetzen` |
| Einen einzelnen Absatz ändern | `block_ersetzen` |
| Text suchen und ersetzen | `notiz_bearbeiten` |
| Tote Verweise entfernen | `links_bereinigen` |
| Einen Verweis setzen | `notizen_verknuepfen` |
| Einen wiederkehrenden Ablauf merken | `fertigkeit_definieren` |

`notizen_verknuepfen` kann keine Links entfernen. Dafür gibt es
`links_bereinigen`.

## Wiederholung ist keine Lösung

Derselbe Aufruf mit denselben Parametern liefert dasselbe Ergebnis. Scheitert
etwas zweimal, ändere die Parameter oder nimm ein anderes Werkzeug – oder sag,
dass es nicht geht.

## Was du nicht behaupten darfst

- „Angelegt" heißt nicht „ausgeführt". Eine definierte Fertigkeit ist noch
  nicht gelaufen.
- Es gibt keinen Zeitplaner. „Wird ab jetzt regelmäßig ausgeführt" ist immer
  falsch.
- Ein Werkzeug, das `ok=false` meldet, hat nichts getan. Melde das so.
""",

"20_Daten_anlegen.md": """---
titel: "Regeln: Daten anlegen und verarbeiten"
tags:
  - system
  - regeln
---

# Daten anlegen und verarbeiten

## Der mitgelieferte Text ist die Quelle

Wenn im Auftrag Inhalt steht – ein Ablauf, eine Fehlermeldung, eine
Spezifikation –, dann ist **das** das Material. Suche nicht nach Notizen, die
es schon geben müsste, und erfinde nichts dazu. Arbeite den gelieferten Text
aus: gliedere ihn, ergänze das Naheliegende, behalte jede konkrete Angabe.

Konkrete Angaben sind unantastbar: Gerätebezeichnungen, Dateinamen,
Tastenkombinationen, Versionsnummern, Fehlercodes. Sie werden übernommen, nicht
umformuliert.

## Neu heißt neu

Ein Auftrag „erstelle eine Datei/Notiz über X" verlangt **eine neue Notiz**.
Vorhandene Notizen sind dabei höchstens Kontext, nie Voraussetzung. Fehlt eine
Notiz, auf die du verweisen wolltest, lass den Verweis weg – lege die Notiz
trotzdem an.

## Der Ordner ergibt sich aus dem Thema

Nennt der Auftrag einen Ordner („unter Windows-Problemen"), gilt der. Gibt es
ihn noch nicht, lege ihn an. Sonst wähle den Ordner, in dem thematisch
Verwandtes liegt.

## Aufbau einer neuen Notiz

1. **Kurzfassung** – zwei bis vier Sätze: worum es geht und was das Ergebnis war.
2. **Einordnung** – Gerät, System, Fehlerbild, Rahmenbedingungen.
3. **Vorgehen** – nummerierte Schritte, in der Reihenfolge, in der sie
   auszuführen sind. Genau die Schritte aus dem Auftrag.
4. **Was schiefgehen kann** – die Fallstricke, die im Auftrag genannt sind.
5. **Verwandte Notizen** – `[[Links]]`, aber nur auf Notizen, die es gibt.

## Ehrlichkeit vor Vollständigkeit

Lieber ein kurzer Abschnitt, der stimmt, als ein langer, der plausibel klingt.
Was du nicht weißt, lässt du weg oder schreibst es als offene Frage hin.
""",
}


def vorlage_schreiben(cfg=settings, ueberschreiben: bool = False,
                      fehler: Optional[List[str]] = None) -> List[Path]:
    """Startfassung der Regeln anlegen. Gibt die geschriebenen Dateien zurück.

    Geht bewusst NICHT über die Werkzeuge: Der Ordner ist schreibgeschützt,
    und das soll er auch beim Anlegen bleiben. Diese Funktion wird nur von
    Hand angestoßen (`/regeln anlegen`), nie vom Modell.
    """
    ordner = cfg.regel_pfad
    if fehler is None:
        fehler = []
    if ordner is None:
        fehler.append("Kein Regelordner eingestellt (REGEL_ORDNER).")
        return []
    geschrieben: List[Path] = []
    try:
        ordner.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fehler.append(f"Ordner nicht anlegbar: {exc}")
        return []
    for name, inhalt in VORLAGEN.items():
        ziel = ordner / name
        if ziel.exists() and not ueberschreiben:
            continue
        try:
            ziel.write_text(inhalt, encoding="utf-8")
        except OSError as exc:
            # Nicht verschlucken: Wer die Regeln anlegen will und nur eine
            # halbe Liste zurückbekommt, muss erfahren, welche Datei fehlt.
            # Sonst gilt eine Regel als gesetzt, die nie auf der Platte stand.
            fehler.append(f"{name}: {exc}")
            continue
        geschrieben.append(ziel)
    return geschrieben
