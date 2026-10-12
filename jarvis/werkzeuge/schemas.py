"""Die Werkzeugliste: Namen, Parameter, Pflichtfelder, Schreibrechte.

Diese Datei ist das Verzeichnis, aus dem sich alles andere speist: die
Werkzeugliste fuer das Modell, die Pruefung im Dispatcher, der Probelauf
einer Fertigkeit, die Auswahl des Planers. Ein neues Werkzeug wird hier
eingetragen - und ist damit ueberall bekannt.

TOOL_NAMES, TOOL_PARAMS und TOOL_REQUIRED werden aus TOOL_SCHEMAS
abgeleitet und nicht daneben gepflegt: Zwei Listen desselben Sachverhalts
laufen auseinander, und zwar genau dann, wenn es darauf ankommt.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional


from .ergebnis import ToolResult


def _p(typ: str, beschreibung: str, **extra) -> dict:
    out = {"type": typ, "description": beschreibung}
    out.update(extra)
    return out


TOOL_SCHEMAS: List[dict] = [
    {
        "name": "notizen_suchen",
        "description": "Durchsucht die Vault semantisch und per Stichwort. Immer ZUERST benutzen, "
                       "bevor du etwas behauptest oder schreibst.",
        "parameters": {
            "frage": _p("string", "Suchbegriff oder Frage"),
            "ordner": _p("string", "optional: nur in diesem Ordner suchen"),
            "anzahl": _p("integer", "Anzahl Treffer (Standard 5)"),
        },
        "required": ["frage"],
    },
    {
        "name": "web_suchen",
        "description": "Sucht im INTERNET. Das einzige Werkzeug, das aus der Vault "
                       "heraus nach draußen greift. Nimm es, sobald der Nutzer von "
                       "online, Internet, recherchieren, nachschlagen im Netz oder "
                       "aktuellen Angaben spricht - notizen_suchen durchsucht NUR "
                       "seine eigenen Notizen und beantwortet solche Fragen nicht.",
        "parameters": {
            "frage": _p("string", "Wonach im Internet gesucht werden soll"),
            "anzahl": _p("integer", "Anzahl Treffer (Standard 5, höchstens 10)"),
        },
        "required": ["frage"],
    },
    {
        "name": "web_lesen",
        "description": "Holt eine Internetseite und gibt ihren Text zurück. Nach "
                       "web_suchen auf den passenden Treffer anwenden, wenn der "
                       "Auszug nicht reicht.",
        "parameters": {
            "url": _p("string", "Vollständige Adresse der Seite"),
            "max_zeichen": _p("integer", "Wie viel Text höchstens (Standard 6000)"),
        },
        "required": ["url"],
    },
    {
        "name": "notiz_lesen",
        "description": "Liest eine Notiz vollständig oder einen benannten Abschnitt daraus. "
                       "Benutze das, wenn ein Suchauszug abgeschnitten wirkt.",
        "parameters": {
            "name": _p("string", "Name, Titel oder Pfad der Notiz"),
            "abschnitt": _p("string", "optional: Überschrift, z. B. 'Backpropagation'"),
            "max_zeichen": _p("integer", "maximale Länge (Standard 8000)"),
        },
        "required": ["name"],
    },
    {
        "name": "struktur_anzeigen",
        "description": "Zeigt Ordner und Notizen der Vault; mit 'ordner' den Inhalt eines Ordners.",
        "parameters": {"ordner": _p("string", "optional: konkreter Ordner")},
        "required": [],
    },
    {
        "name": "ordner_anlegen",
        "description": "Legt einen neuen Ordner in der Vault an.",
        "parameters": {"pfad": _p("string", "Ordnerpfad, z. B. 'Deep Learning/Übungen'")},
        "required": ["pfad"],
    },
    {
        "name": "notiz_erstellen",
        "description": "Legt eine NEUE Notiz als Markdown-Datei an. Der Ordner wird automatisch "
                       "passend gewählt, wenn du keinen angibst.",
        "parameters": {
            "titel": _p("string", "Titel der Notiz"),
            "inhalt": _p("string", "vollständiger Markdown-Inhalt"),
            "ordner": _p("string", "optional: Zielordner"),
            "tags": _p("array", "optional: Tags ohne #", items={"type": "string"}),
            "quellen": _p("array", "optional: Notizen, die verlinkt werden sollen",
                          items={"type": "string"}),
        },
        "required": ["titel", "inhalt"],
    },
    {
        "name": "notiz_ergaenzen",
        "description": "Hängt Inhalt an eine bestehende Notiz an - optional unter eine Überschrift.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "inhalt": _p("string", "was ergänzt werden soll (Markdown)"),
            "abschnitt": _p("string", "optional: Überschrift, unter die es gehört"),
        },
        "required": ["name", "inhalt"],
    },
    {
        "name": "notiz_bearbeiten",
        "description": "Ersetzt eine Textstelle in einer Notiz. Vorher notiz_lesen, damit der "
                       "Suchtext exakt stimmt.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "suchtext": _p("string", "exakt vorhandener Text"),
            "ersatztext": _p("string", "neuer Text"),
            "alle": _p("boolean", "alle Vorkommen ersetzen (Standard false)"),
        },
        "required": ["name", "suchtext", "ersatztext"],
    },
    {
        "name": "notiz_ueberschreiben",
        "description": "Schreibt den Inhalt einer Notiz komplett neu. Frontmatter bleibt erhalten.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "inhalt": _p("string", "neuer vollständiger Inhalt"),
        },
        "required": ["name", "inhalt"],
    },
    {
        "name": "notiz_verschieben",
        "description": "Verschiebt und/oder benennt eine Notiz um. Verweise in anderen Notizen "
                       "werden automatisch mitgezogen.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "ziel_ordner": _p("string", "optional: Zielordner"),
            "neuer_name": _p("string", "optional: neuer Dateiname ohne .md"),
        },
        "required": ["name"],
    },
    {
        "name": "notiz_loeschen",
        "description": "Verschiebt eine Notiz in den Papierkorb-Ordner (nicht endgültig).",
        "parameters": {
            "name": _p("string", "Notiz"),
            "grund": _p("string", "kurze Begründung"),
        },
        "required": ["name"],
    },
    {
        "name": "notizen_verknuepfen",
        "description": "Setzt [[Wikilinks]] zwischen zwei Notizen, standardmäßig beidseitig.",
        "parameters": {
            "von": _p("string", "erste Notiz"),
            "nach": _p("string", "zweite Notiz"),
            "beschreibung": _p("string", "optional: warum sie zusammengehören"),
            "beidseitig": _p("boolean", "Standard true"),
        },
        "required": ["von", "nach"],
    },
    {
        "name": "verwandte_finden",
        "description": "Findet inhaltlich verwandte Notizen zu einer Notiz - Grundlage für neue Links.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "anzahl": _p("integer", "Anzahl Vorschläge (Standard 5)"),
        },
        "required": ["name"],
    },
    {
        "name": "listenpunkt_hinzufuegen",
        "description": "Reiht einen Punkt in eine BESTEHENDE Liste ein - an der richtigen "
                       "Stelle, nicht als Absatz am Dateiende. Dafür nehmen, wenn etwas zu "
                       "einer Aufgaben- oder Einkaufsliste hinzukommt; notiz_ergaenzen ist "
                       "für Fließtext.",
        "parameters": {
            "name": _p("string", "Notiz mit der Liste"),
            "text": _p("string", "Der neue Punkt, ohne '- [ ]'"),
            "abschnitt": _p("string", "optional: Überschrift, unter der die Liste steht"),
            "position": _p("string", "optional: 'ende' (Standard) oder 'anfang'"),
            "checkbox": _p("boolean", "optional: Standard true - '- [ ] Text' statt '- Text'"),
        },
        "required": ["name", "text"],
    },
    {
        "name": "listenpunkt_erledigt",
        "description": "Hakt einen VORHANDENEN Listenpunkt ab, streicht ihn durch oder "
                       "entfernt ihn. Ändert die bestehende Zeile, statt einen Kommentar "
                       "dazuzuschreiben. Bei mehreren passenden Punkten wird nicht geraten, "
                       "sondern nachgefragt.",
        "parameters": {
            "name": _p("string", "Notiz mit der Liste"),
            "text": _p("string", "Text des Punkts (Teilstück genügt, muss aber eindeutig sein)"),
            "modus": _p("string", "optional: 'auto' (Standard), 'abhaken', "
                                  "'durchstreichen' oder 'entfernen'"),
            "abschnitt": _p("string", "optional: nur in diesem Abschnitt suchen"),
        },
        "required": ["name", "text"],
    },
    {
        "name": "struktur_zeichnen",
        "description": "Zeichnet einen Ablauf, eine Struktur oder einen Zusammenhang als "
                       "Bild. IMMER benutzen, wenn nach Darstellung, Visualisierung, "
                       "Ablauf, Aufbau oder Übersicht gefragt wird - eine Beschreibung in "
                       "Prosa ist dann die falsche Antwort. Ändert nichts in der Vault.",
        "parameters": {
            "beschreibung": _p(
                "string",
                'Eine Verbindung je Zeile: "A -> B". Form über Klammern: (rund) Anfang '
                'oder Ende, [eckig] Schritt, {geschweift} Entscheidung, <spitz> Ein- '
                'oder Ausgabe. Pfeilbeschriftung nach dem Doppelpunkt: "A -> B: ok". '
                'Ketten sind erlaubt: "A -> B -> C".'),
            "titel": _p("string", "optional: Überschrift über dem Bild"),
            "richtung": _p("string", "optional: 'ab' (Standard) oder 'rechts'"),
        },
        "required": ["beschreibung"],
    },
    {
        "name": "links_pruefen",
        "description": "Findet ins Leere laufende [[Links]] und repariert sie auf Wunsch, "
                       "indem sie auf eine ähnliche vorhandene Notiz umgebogen werden. "
                       "Gibt es keine ähnliche Notiz, bleibt der Link stehen – dann "
                       "'links_bereinigen' verwenden.",
        "parameters": {"reparieren": _p("boolean", "true = automatisch korrigieren")},
        "required": [],
    },
    {
        "name": "links_bereinigen",
        "description": "Entfernt tote [[Links]], für die es keine passende Notiz gibt. "
                       "DAS ist das Werkzeug zum Loswerden toter Verweise – NICHT "
                       "notizen_verknuepfen mit leerem Ziel.",
        "parameters": {
            "name": _p("string", "Notiz; leer = die ganze Vault"),
            "modus": _p("string", "entschaerfen (Standard, wird Klartext) | "
                                  "entfernen | markieren"),
        },
        "required": [],
    },
    # ---- ab hier die in v6 ergänzten Werkzeuge (nichts wurde entfernt) ----
    {
        "name": "abschnitt_ersetzen",
        "description": "Tauscht ein ganzes Kapitel einer Notiz aus, ohne den Rest anzufassen. "
                       "Besser als notiz_ueberschreiben, wenn nur ein Abschnitt veraltet ist.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "abschnitt": _p("string", "Überschrift des Kapitels"),
            "inhalt": _p("string", "neuer Inhalt des Kapitels (ohne Überschrift)"),
        },
        "required": ["name", "abschnitt", "inhalt"],
    },
    {
        "name": "abschnitt_einfuegen",
        "description": "Fügt ein neues Kapitel in eine bestehende Notiz ein - am Ende oder "
                       "direkt hinter einem vorhandenen Abschnitt.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "ueberschrift": _p("string", "Überschrift des neuen Kapitels"),
            "inhalt": _p("string", "Inhalt"),
            "nach_abschnitt": _p("string", "optional: dahinter einfügen"),
            "ebene": _p("integer", "Überschriftenebene, Standard 2"),
        },
        "required": ["name", "ueberschrift", "inhalt"],
    },
    {
        "name": "abschnitt_loeschen",
        "description": "Entfernt ein Kapitel samt Unterabschnitten aus einer Notiz.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "abschnitt": _p("string", "Überschrift des Kapitels"),
        },
        "required": ["name", "abschnitt"],
    },
    {
        "name": "notizen_zusammenfuehren",
        "description": "Führt mehrere Notizen zu einer zusammen (gegen Dubletten). Verweise "
                       "werden umgebogen, die Quellen wandern in den Papierkorb.",
        "parameters": {
            "quellen": _p("array", "Namen der zusammenzuführenden Notizen",
                          items={"type": "string"}),
            "ziel_titel": _p("string", "Titel der neuen Notiz"),
            "ordner": _p("string", "optional: Zielordner"),
            "quellen_loeschen": _p("boolean", "Quellen in den Papierkorb (Standard true)"),
        },
        "required": ["quellen"],
    },
    {
        "name": "notiz_aufteilen",
        "description": "Zerlegt eine überlange Notiz in Kapitelnotizen; das Original wird zur "
                       "Übersicht mit Verweisen auf die Teile.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "ordner": _p("string", "optional: Zielordner der Teile"),
            "ab_ebene": _p("integer", "welche Überschriftenebene trennt (Standard 2)"),
        },
        "required": ["name"],
    },
    {
        "name": "auto_verlinken",
        "description": "Verknüpft inhaltlich verwandte Notizen automatisch. Ohne Ordnerangabe "
                       "über die ganze Vault. Bestehende Links bleiben unangetastet.",
        "parameters": {
            "ordner": _p("string", "optional: nur dieser Ordner"),
            "pro_notiz": _p("integer", "höchstens so viele neue Links je Notiz (Standard 3)"),
            "mindestnaehe": _p("number", "Schwelle 0..1, Standard 0.35"),
        },
        "required": [],
    },
    {
        "name": "uebersichtsnotiz",
        "description": "Legt für einen Ordner eine Einstiegsnotiz an, die alle Notizen darin "
                       "nach Tags gruppiert verlinkt. Vorhandene wird aktualisiert.",
        "parameters": {
            "ordner": _p("string", "Ordner"),
            "titel": _p("string", "optional: Titel der Übersicht"),
        },
        "required": ["ordner"],
    },
    {
        "name": "frontmatter_setzen",
        "description": "Setzt ein beliebiges Frontmatter-Feld, z. B. status, prioritaet, quelle.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "schluessel": _p("string", "Feldname"),
            "wert": _p("string", "Wert"),
        },
        "required": ["name", "schluessel", "wert"],
    },
    {
        "name": "bloecke_anzeigen",
        "description": "Zeigt die Blöcke einer Notiz mit Nummer und Vorschau "
                       "(Absätze, Listen, Codeblöcke, Überschriften). IMMER "
                       "zuerst aufrufen, bevor block_ersetzen benutzt wird – "
                       "eine geratene Blocknummer trifft den falschen Absatz.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "ab": _p("integer", "optional: ab welcher Blocknummer"),
            "anzahl": _p("integer", "optional: wie viele (Standard 40)"),
        },
        "required": ["name"],
    },
    {
        "name": "block_ersetzen",
        "description": "Ersetzt EINEN Block einer Notiz (Absatz, Liste, "
                       "Codeblock) durch neuen Text. Die Nummer stammt aus "
                       "bloecke_anzeigen. Für ein ganzes Kapitel gibt es "
                       "abschnitt_ersetzen, für Suchen/Ersetzen im Text "
                       "notiz_bearbeiten. Mit nur_vorschau=true wird nur der "
                       "Unterschied gezeigt, ohne zu schreiben.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "block": _p("integer", "Blocknummer aus bloecke_anzeigen"),
            "inhalt": _p("string", "der neue Text des Blocks"),
            "nur_vorschau": _p("boolean", "true: nur den Unterschied zeigen"),
        },
        "required": ["name", "block", "inhalt"],
    },
    {
        "name": "notiz_wiederherstellen",
        "description": "Holt eine Notiz aus dem Papierkorb zurück.",
        "parameters": {
            "name": _p("string", "Name der Notiz"),
            "ordner": _p("string", "optional: Zielordner"),
        },
        "required": ["name"],
    },
    {
        # Die Funktion gab es von Anfang an, nur konnte das Modell sie nicht
        # aufrufen - sie stand in keinem Schema. Die Architekturprüfung hat
        # genau diese Lücke aufgedeckt: Aufgabenfeld "Zurücknehmen" mit einem
        # halben Werkzeug. Ein Agent, der schreibt, aber nicht zurücknehmen
        # kann, muss jeden Fehler vom Benutzer aufräumen lassen.
        "name": "rueckgaengig",
        "description": "Macht die LETZTE Änderung an der Vault rückgängig (die vorige "
                       "Fassung der betroffenen Dateien wird zurückgespielt). Nach einem "
                       "Fehlgriff verwenden – etwa wenn eine Notiz falsch überschrieben "
                       "oder am falschen Ort angelegt wurde. Wirkt nur auf die letzte "
                       "Änderung, nicht auf eine ganze Sitzung.",
        "parameters": {},
        "required": [],
    },
    {
        "name": "ordner_umbenennen",
        "description": "Benennt einen Ordner um und zieht alle enthaltenen Notizen mit.",
        "parameters": {
            "alt": _p("string", "bisheriger Ordner"),
            "neu": _p("string", "neuer Name"),
        },
        "required": ["alt", "neu"],
    },
    {
        "name": "codeblock_hinzufuegen",
        "description": "Fügt Code als sauberen Markdown-Codeblock in eine Notiz ein. "
                       "Dafür statt notiz_ergaenzen verwenden, wenn es um Code geht.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "code": _p("string", "der Code selbst, ohne Backticks"),
            "sprache": _p("string", "python, javascript, sql … (Standard python)"),
            "abschnitt": _p("string", "optional: unter welche Überschrift"),
            "beschreibung": _p("string", "optional: ein Satz davor"),
        },
        "required": ["name", "code"],
    },
    {
        "name": "code_auslesen",
        "description": "Gibt die Codeblöcke einer Notiz zurück - zum Prüfen oder Überarbeiten.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "sprache": _p("string", "optional: nur diese Sprache"),
        },
        "required": ["name"],
    },
    {
        "name": "datei_importieren",
        "description": "Liest eine Datei von der Festplatte und legt sie als Notiz ab "
                       "(Quellcode, Konfiguration, Log, CSV, Markdown).",
        "parameters": {
            "pfad": _p("string", "vollständiger Pfad zur Datei"),
            "titel": _p("string", "optional: Titel der Notiz (Standard: Dateiname)"),
            "ordner": _p("string", "optional: Zielordner"),
            "als_code": _p("boolean", "Markdown als Code einbetten statt übernehmen"),
        },
        "required": ["pfad"],
    },
    {
        "name": "code_dateien",
        "description": "Listet die Quelldateien des eigenen Systems (nur lesend). "
                       "Einstieg, wenn es um den Code des Agenten selbst geht.",
        "parameters": {
            "muster": _p("string", "Dateimuster, Standard *.py"),
            "unterordner": _p("string", "optional: nur dieser Unterordner"),
        },
        "required": [],
    },
    {
        "name": "quellcode_lesen",
        "description": "Liest eine Quelldatei des Systems mit Zeilennummern - ganz oder "
                       "einen Bereich. Nur lesend; der Agent kann seinen Code nicht ändern.",
        "parameters": {
            "datei": _p("string", "Dateiname oder Pfad, z. B. jarvis/planner.py"),
            "von_zeile": _p("integer", "erste Zeile (Standard 1)"),
            "bis_zeile": _p("integer", "letzte Zeile (0 = bis zum Ende)"),
        },
        "required": ["datei"],
    },
    {
        "name": "code_suchen",
        "description": "Sucht eine Zeichenkette im Quellcode und nennt Datei und Zeile.",
        "parameters": {
            "begriff": _p("string", "Suchbegriff"),
            "muster": _p("string", "Dateimuster, Standard *.py"),
            "anzahl": _p("integer", "maximale Fundstellen"),
        },
        "required": ["begriff"],
    },
    {
        "name": "codebasis_importieren",
        "description": "Übernimmt den Quellcode als Notizen in die Vault, damit er "
                       "durchsuchbar wird. Große Dateien werden in nummerierte Teile "
                       "zerlegt, inhaltsgleiche Doppel übersprungen. Bleibt wegen des "
                       "Schreibbudgets etwas offen, sagt die Antwort das – dann denselben "
                       "Aufruf wiederholen, er setzt fort.",
        "parameters": {
            "muster": _p("string", "Dateimuster, Standard *.py"),
            "ordner": _p("string", "Zielordner, Standard _System/Code"),
            "erneuern": _p("boolean", "true: auch bereits importierte Dateien neu ablegen"),
        },
        "required": [],
    },
    {
        "name": "vault_wartung",
        "description": "Wartungslauf: Index abgleichen, tote Links reparieren, verwaiste und "
                       "doppelte Notizen melden.",
        "parameters": {"reparieren": _p("boolean", "Links automatisch korrigieren (Standard true)")},
        "required": [],
    },
    {
        "name": "tags_setzen",
        "description": "Setzt oder ergänzt die Tags einer Notiz im Frontmatter.",
        "parameters": {
            "name": _p("string", "Notiz"),
            "tags": _p("array", "Tags ohne #", items={"type": "string"}),
            "modus": _p("string", "'ergaenzen' (Standard) oder 'ersetzen'"),
        },
        "required": ["name", "tags"],
    },
]

TOOL_NAMES = {t["name"] for t in TOOL_SCHEMAS}
TOOL_PARAMS = {t["name"]: list(t["parameters"].keys()) for t in TOOL_SCHEMAS}
TOOL_REQUIRED = {t["name"]: list(t.get("required") or []) for t in TOOL_SCHEMAS}

# Werkzeuge, die die Vault verändern. Maßgebliche Liste - der Probelauf einer
# Fertigkeit simuliert genau diese und führt alles andere wirklich aus.
# 'codebasis_importieren' und 'vault_wartung' stehen bewusst mit drin: beide
# legen Notizen an bzw. reparieren Links, auch wenn der Name harmlos klingt.
SCHREIBENDE_WERKZEUGE = {
    "notiz_erstellen", "notiz_ergaenzen", "notiz_bearbeiten", "notiz_ueberschreiben",
    "notiz_verschieben", "notiz_loeschen", "notiz_wiederherstellen",
    "notizen_verknuepfen", "notizen_zusammenfuehren", "notiz_aufteilen",
    "abschnitt_ersetzen", "abschnitt_einfuegen", "abschnitt_loeschen",
    "auto_verlinken", "uebersichtsnotiz", "tags_setzen", "frontmatter_setzen",
    "ordner_anlegen", "ordner_umbenennen", "codeblock_hinzufuegen",
    "datei_importieren", "codebasis_importieren", "vault_wartung",
    "block_ersetzen", "listenpunkt_hinzufuegen", "listenpunkt_erledigt",
    # Verändert die Vault ebenfalls - es spielt Dateien zurück.
    "rueckgaengig",
}
NUR_LESENDE_WERKZEUGE = TOOL_NAMES - SCHREIBENDE_WERKZEUGE
_INT_PARAMS = {"anzahl", "max_zeichen", "ebene", "ab_ebene", "pro_notiz",
               "von_zeile", "bis_zeile", "block", "ab"}
_BOOL_PARAMS = {"alle", "beidseitig", "reparieren", "quellen_loeschen", "als_code",
                "erneuern", "nur_vorschau"}
_LIST_PARAMS = {"tags", "quellen"}
_FLOAT_PARAMS = {"mindestnaehe"}


def ollama_tool_definitions() -> List[dict]:
    """Schemata im Format der Ollama-/OpenAI-Funktionsaufrufe."""
    out = []
    for tool in TOOL_SCHEMAS:
        out.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": {
                    "type": "object",
                    "properties": tool["parameters"],
                    "required": tool["required"],
                },
            },
        })
    return out


def tool_reference() -> str:
    """Kompakte Textbeschreibung für Modelle ohne native Werkzeug-Unterstützung."""
    lines = []
    for tool in TOOL_SCHEMAS:
        params = ", ".join(
            f"{k}{'*' if k in tool['required'] else ''}" for k in tool["parameters"]
        )
        lines.append(f"- {tool['name']}({params}): {tool['description']}")
    return "\n".join(lines)


# Feldnamen, die Modelle statt der erwarteten schreiben. Die Tabelle gab es
# bisher NUR im Planer - rief das Modell ein Werkzeug direkt auf (nativ oder
# per JSON-Protokoll) und schrieb title/content, verwarf _clean_params beide
# stillschweigend, und der Aufruf lief danach in einen nackten TypeError. Aus
# "missing 2 required positional arguments" kann ein Modell weder ablesen,
# welches Feld fehlt, noch wie es richtig heißt - es probierte dasselbe erneut.
#
# Der Planer behält seine eigene, etwas weitere Tabelle; diese hier ist die
# Notübersetzung im Dispatcher und greift nur bei ungültigen Namen.
PARAM_SYNONYME = {
    "title": "titel", "note": "name", "notiz": "name", "file": "name",
    "filename": "name", "dateiname": "name", "note_name": "name",
    "content": "inhalt", "body": "inhalt",
    "folder": "ordner", "verzeichnis": "ordner", "directory": "ordner",
    "target": "ziel", "destination": "ziel",
    "to": "nach", "from": "von", "source": "von",
    "new_name": "neuer_name", "neuer_titel": "neuer_name", "rename_to": "neuer_name",
    "section": "abschnitt", "heading": "ueberschrift",
    "sources": "quellen", "notes": "quellen", "labels": "tags", "schlagworte": "tags",
    "search": "suchtext", "old": "suchtext", "old_text": "suchtext",
    "replace": "ersatztext", "new": "ersatztext", "new_text": "ersatztext",
    "language": "sprache", "path": "pfad", "key": "schluessel", "value": "wert",
    "query": "frage", "question": "frage", "suche": "frage", "suchbegriff": "frage",
    "count": "anzahl", "limit": "anzahl",
    "item": "text", "task": "text", "eintrag": "text", "punkt": "text",
    "description": "beschreibung", "mode": "modus",
}


def _uebersetzen(werkzeug: str, parameter: dict) -> dict:
    """Ungültige Feldnamen auf die gültigen ziehen - und nur die ungültigen.

    Die Gegenrichtung ist genauso wichtig: Ein Name, der für DIESES Werkzeug
    gültig ist, wird nie übersetzt. 'datei' heißt bei quellcode_lesen wirklich
    'datei' und darf nicht zu 'name' werden, 'text' bei
    listenpunkt_hinzufuegen nicht zu 'inhalt'.
    """
    erlaubt = set(TOOL_PARAMS.get(werkzeug, []))
    vorhanden = set(parameter or {})
    out: Dict[str, object] = {}
    for key, value in (parameter or {}).items():
        if key in erlaubt:
            out[key] = value
            continue
        ziel = PARAM_SYNONYME.get(str(key).strip().lower())
        # Nicht überschreiben, was ausdrücklich mitgegeben wurde.
        if ziel and ziel in erlaubt and ziel not in vorhanden and ziel not in out:
            out[ziel] = value
    return out


def _pflicht_pruefen(werkzeug: str, parameter: dict) -> Optional[ToolResult]:
    """Fehlt ein Pflichtfeld, sagt es das - statt es Python sagen zu lassen."""
    pflicht = list(TOOL_REQUIRED.get(werkzeug, []))
    if not pflicht:
        return None
    fehlt = [p for p in pflicht
             if p not in parameter or not str(parameter.get(p, "")).strip()]
    if not fehlt:
        return None
    return ToolResult(
        False,
        f"'{werkzeug}' fehlt: {', '.join(fehlt)}. Pflichtfelder sind "
        f"{', '.join(pflicht)}. Rufe es mit diesen Feldern erneut auf.",
        {"fehlt": fehlt, "pflicht": pflicht,
         "erlaubt": TOOL_PARAMS.get(werkzeug, []),
         "uebergeben": sorted(parameter or {})})


def _clean_params(werkzeug: str, parameter: dict) -> dict:
    """Modelle liefern gern Strings statt Zahlen/Listen - hier geradegezogen."""
    erlaubt = set(TOOL_PARAMS.get(werkzeug, []))
    out: Dict[str, object] = {}
    for key, value in (parameter or {}).items():
        if key not in erlaubt:
            continue
        if key in _INT_PARAMS:
            try:
                out[key] = int(str(value).strip())
            except (TypeError, ValueError):
                continue
        elif key in _BOOL_PARAMS:
            out[key] = str(value).strip().lower() in ("1", "true", "ja", "yes", "wahr")
        elif key in _FLOAT_PARAMS:
            try:
                out[key] = float(str(value).replace(",", "."))
            except (TypeError, ValueError):
                continue
        elif key in _LIST_PARAMS:
            if isinstance(value, str):
                out[key] = [v.strip() for v in re.split(r"[,\n]", value) if v.strip()]
            elif isinstance(value, (list, tuple)):
                out[key] = [str(v).strip() for v in value if str(v).strip()]
        else:
            out[key] = "" if value is None else str(value)
    return out
