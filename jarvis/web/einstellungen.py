"""Welche .env-Werte die Oberfläche anbietet - und was sie bedeuten.

Absichtlich eine handgeschriebene Liste und keine Ableitung aus dem
Settings-Dataclass. Zwei Gründe:

1. **Nicht jedes Feld gehört in eine Oberfläche.** `chunk_overlap` einzustellen,
   ohne zu wissen, dass danach neu indexiert werden muss, richtet mehr Schaden
   an als Nutzen.
2. **Die Erklärung ist der eigentliche Wert.** Ein Eingabefeld "NUM_PREDICT"
   hilft niemandem; "Obergrenze erzeugter Token - zu klein, und ein Denkmodell
   verbraucht sein Budget beim Nachdenken" schon.

Jeder Eintrag nennt außerdem, ob eine Änderung sofort greift oder einen
Neustart braucht. Ein stiller Wert, der erst beim nächsten Start wirkt, ist
sonst der nächste "hab ich doch eingestellt"-Fall.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

# (Schlüssel, Beschriftung, Art, Erklärung, Auswahl, Neustart nötig)
# Art: text | zahl | komma | schalter | auswahl | pfad
Feld = Tuple[str, str, str, str, Optional[List[str]], bool]

GRUPPEN: List[Tuple[str, str, List[Feld]]] = [
    ("server", "Server & Modelle", [
        ("OLLAMA_HOST", "Hauptserver", "text",
         "Adresse des Ollama-Servers. Fehlt das Schema, wird http:// ergänzt.",
         None, True),
        ("OLLAMA_HOST_ALTERNATIVE", "Ausweichserver", "text",
         "Wird genommen, wenn der Hauptserver nicht antwortet – beim Start und "
         "mitten im Betrieb.", None, True),
        ("LLM_MODEL", "Hauptmodell", "text",
         "Muss GENAU so heißen wie in 'ollama list'. qwen3:32b gilt nicht als "
         "vorhanden, nur weil qwen3:27b dort liegt.", None, True),
        ("LLM_MODEL_ALTERNATIVE", "Ausweichmodell", "text",
         "Modell auf dem Ausweichserver.", None, True),
        ("FAST_MODEL", "Schnelles Modell", "text",
         "Für Pläne, Ordnerwahl und JSON-Entscheidungen. Leer = Hauptmodell.",
         None, True),
        ("EMBEDDING_MODEL", "Einbettungsmodell", "text",
         "ACHTUNG: Ein anderes Modell erzeugt einen anderen Vektorraum. Nach "
         "einer Änderung muss der Index neu gebaut werden (--reindex), sonst "
         "liefert die Suche stillschweigend Unsinn.", None, True),
    ]),
    ("tempo", "Tempo & Grenzen", [
        ("NUM_PREDICT", "Token-Obergrenze", "zahl",
         "Wie viele Token eine Antwort höchstens hat. Denkmodelle (qwen3) "
         "rechnen ihre Überlegung mit ein – ist der Wert zu klein, denken sie "
         "die Antwort weg und schicken leeren Text.", None, False),
        ("DENKEN", "Nachdenken", "auswahl",
         "auto = wie das Modell es vorsieht. aus = keine Denkphase, alle Token "
         "gehen in die Antwort. an = erzwungen, braucht ein hohes NUM_PREDICT.",
         ["auto", "aus", "an"], False),
        ("NUM_CTX", "Kontextfenster", "zahl",
         "Größer = langsamer und speicherhungriger. Der Prompt wird automatisch "
         "auf ~65 % begrenzt, damit Ollama ihn nicht vorn abschneidet.",
         None, True),
        ("IDLE_TIMEOUT", "Leerlauf-Abbruch (s)", "zahl",
         "Abbruch nur, wenn GAR KEIN Token mehr kommt. Gilt zwischen zwei "
         "Token, nicht für die ganze Antwort.", None, False),
        ("REQUEST_TIMEOUT", "Gesamtlimit (s)", "zahl",
         "Harte Obergrenze für eine Anfrage.", None, False),
        ("TEMPERATURE", "Temperatur", "komma",
         "0 = immer dieselbe Antwort, 1 = sprunghaft. 0.3 ist ein guter Wert "
         "für Werkzeugeinsatz.", None, False),
        ("KEEP_ALIVE", "Modell im Speicher halten", "text",
         "z. B. 60m. Sonst wird ein großes Modell vor jeder Anfrage neu geladen.",
         None, True),
        ("MAX_TOOL_STEPS", "Werkzeugschritte je Frage", "zahl",
         "Wie oft der Agent in einer Antwort nacheinander Werkzeuge greifen darf.",
         None, False),
    ]),
    ("vault", "Vault & Sicherheit", [
        ("OBSIDIAN_VAULT", "Vault-Ordner", "pfad",
         "Der Obsidian-Ordner. Nach einer Änderung neu indexieren.", None, True),
        ("PROTECTED_FOLDERS", "Geschützte Ordner", "text",
         "Kommagetrennt. Dort wird nie geschrieben. Der Regelordner ist immer "
         "dabei, auch wenn er hier nicht steht.", None, False),
        ("DRY_RUN", "Trockenlauf", "schalter",
         "An: Alle Schreibvorgänge werden nur angekündigt, nichts wird verändert.",
         None, False),
        ("CONFIRM_WRITES", "Vor dem Schreiben fragen", "schalter",
         "An: Jede Änderung wird mit Vorschau bestätigt.", None, False),
        ("BACKUPS_ENABLED", "Sicherungen anlegen", "schalter",
         "Vor jeder Änderung eine Kopie. Ausschalten spart nichts, kostet aber "
         "den Rückweg.", None, False),
        ("MAX_FILES_PER_RUN", "Dateien je Auftrag", "zahl",
         "Obergrenze pro Auftrag – bremst einen entgleisten Plan.", None, False),
        ("TRASH_FOLDER", "Papierkorb", "text",
         "Gelöschte Notizen landen dort statt im Nichts.", None, False),
    ]),
    ("verhalten", "Verhalten", [
        ("ASSISTANT_NAME", "Name", "text",
         "Wie er sich nennt und wie die Kopfzeile heißt.", None, False),
        ("USER_TITLE", "Deine Anrede", "text",
         "Wie er dich anspricht.", None, False),
        ("LANGUAGE", "Sprache", "text",
         "Zieht die Stimme mit.", None, False),
        ("TOOLS_ENABLED", "Werkzeuge erlauben", "schalter",
         "Aus: Er kann nur noch antworten, nichts mehr tun.", None, False),
        ("PLANNER_ENABLED", "Planer", "schalter",
         "Schreibaufträge laufen über einen festen Ablauf im Code statt über "
         "die Werkzeugdisziplin des Modells.", None, False),
        ("SELF_EXTENSION", "Eigene Fertigkeiten", "schalter",
         "Er darf sich neue Fertigkeiten anlegen.", None, False),
        ("LEARNING_ENABLED", "Lernen", "schalter",
         "Beobachtungen, Lektionen und Reflexion.", None, False),
        ("LEERLAUF_SEKUNDEN", "Leerlauf (s)", "zahl",
         "Kommt so lange nichts, schaut er in die Aufgabenliste. 0 = aus.",
         None, False),
        ("AUFGABEN_DATEI", "Aufgabenliste", "text",
         "Notiz mit deinen '- [ ]'-Aufgaben. Leer = automatisch suchen.",
         None, False),
    ]),
    ("stimme", "Stimme", [
        ("TTS_ENABLED", "Sprachausgabe", "schalter",
         "Vorlesen der Antworten.", None, True),
        ("STT_ENABLED", "Spracheingabe", "schalter",
         "Leere Eingabe schaltet das Mikrofon ein.", None, True),
        ("PIPER_VOICE", "Piper-Stimme", "text",
         "Leer = beste installierte Stimme zur eingestellten Sprache.",
         None, True),
        ("TTS_RATE", "Sprechtempo", "zahl",
         "Wörter je Minute (nur bei der Systemstimme).", None, True),
        ("SPEAK_GREETING", "Begrüßung sprechen", "schalter",
         "", None, True),
    ]),
]

# Werte, die niemals über die Oberfläche geschrieben werden dürfen. Nicht weil
# sie geheim wären, sondern weil ein falscher Wert hier den Index unbrauchbar
# macht, ohne dass es auffällt.
GESPERRT = {"VECTOR_STORE_PATH", "CHUNK_SIZE", "CHUNK_OVERLAP"}

ARTEN = {"text", "zahl", "komma", "schalter", "auswahl", "pfad"}


def felder() -> Dict[str, Feld]:
    """Alle anpassbaren Felder nach Schlüssel."""
    return {f[0]: f for gruppe in GRUPPEN for f in gruppe[2]}


def als_liste(cfg) -> List[dict]:
    """Gruppen mit aktuellen Werten - genau die Form, die die Oberfläche zeigt."""
    import os

    ausgabe: List[dict] = []
    for schluessel, titel, gruppenfelder in GRUPPEN:
        eintraege = []
        for name, beschriftung, art, erklaerung, auswahl, neustart in gruppenfelder:
            eintraege.append({
                "schluessel": name,
                "beschriftung": beschriftung,
                "art": art,
                "erklaerung": erklaerung,
                "auswahl": auswahl or [],
                "neustart": neustart,
                "wert": os.getenv(name, _standard(cfg, name)),
            })
        ausgabe.append({"schluessel": schluessel, "titel": titel, "felder": eintraege})
    return ausgabe


# Für Felder, die noch nicht in der .env stehen: den geltenden Wert aus den
# Einstellungen zeigen statt eines leeren Kastens. Sonst sieht es aus, als wäre
# nichts gesetzt, obwohl ein Standard gilt.
_ABBILDUNG = {
    "OLLAMA_HOST": "host", "OLLAMA_HOST_ALTERNATIVE": "host_alternative",
    "LLM_MODEL": "llm_model", "LLM_MODEL_ALTERNATIVE": "llm_model_alternative",
    "FAST_MODEL": "fast_model", "EMBEDDING_MODEL": "embedding_model",
    "NUM_PREDICT": "num_predict", "DENKEN": "denken", "NUM_CTX": "num_ctx",
    "IDLE_TIMEOUT": "idle_timeout", "REQUEST_TIMEOUT": "request_timeout",
    "TEMPERATURE": "temperature", "KEEP_ALIVE": "keep_alive",
    "MAX_TOOL_STEPS": "max_tool_steps", "OBSIDIAN_VAULT": "vault_path",
    "PROTECTED_FOLDERS": "protected_folders", "DRY_RUN": "dry_run",
    "CONFIRM_WRITES": "confirm_writes", "BACKUPS_ENABLED": "backups_enabled",
    "MAX_FILES_PER_RUN": "max_files_per_run", "TRASH_FOLDER": "trash_folder",
    "ASSISTANT_NAME": "assistant_name", "USER_TITLE": "user_title",
    "LANGUAGE": "language", "TOOLS_ENABLED": "tools_enabled",
    "PLANNER_ENABLED": "planner_enabled", "SELF_EXTENSION": "self_extension",
    "LEARNING_ENABLED": "learning_enabled", "LEERLAUF_SEKUNDEN": "leerlauf_sekunden",
    "AUFGABEN_DATEI": "aufgaben_datei", "TTS_ENABLED": "tts_enabled",
    "STT_ENABLED": "stt_enabled", "PIPER_VOICE": "piper_voice",
    "TTS_RATE": "tts_rate", "SPEAK_GREETING": "speak_greeting",
}


def _standard(cfg, schluessel: str) -> str:
    name = _ABBILDUNG.get(schluessel)
    if not name:
        return ""
    wert = getattr(cfg, name, "")
    if isinstance(wert, bool):
        return "true" if wert else "false"
    return str(wert)


def pruefen(schluessel: str, wert: str) -> Optional[str]:
    """Gibt einen Fehlertext zurück, wenn der Wert nicht taugt - sonst None."""
    if schluessel in GESPERRT:
        return f"{schluessel} lässt sich hier nicht ändern (Index-Sicherheit)."
    feld = felder().get(schluessel)
    if not feld:
        return f"Unbekannte Einstellung: {schluessel}"
    art, auswahl = feld[2], feld[4]
    wert = str(wert).strip()
    if art == "zahl":
        try:
            int(wert)
        except ValueError:
            return f"{feld[1]}: '{wert}' ist keine ganze Zahl."
    elif art == "komma":
        try:
            float(wert.replace(",", "."))
        except ValueError:
            return f"{feld[1]}: '{wert}' ist keine Zahl."
    elif art == "schalter":
        if wert.lower() not in ("true", "false", "1", "0", "ja", "nein", "an", "aus"):
            return f"{feld[1]}: '{wert}' ist kein Schalterwert."
    elif art == "auswahl" and auswahl and wert not in auswahl:
        return f"{feld[1]}: '{wert}' ist nicht erlaubt ({', '.join(auswahl)})."
    return None


def speichern(werte: Dict[str, str], cfg) -> dict:
    """Alle Werte prüfen, dann schreiben. Ein Fehler verhindert ALLE Schreibvorgänge.

    Halb gespeicherte Einstellungen sind schlimmer als gar keine: Der Host
    zeigt auf den neuen Server, das Modell heißt noch wie das alte, und der
    Fehler taucht erst beim nächsten Start auf.
    """
    fehler = [text for schluessel, wert in werte.items()
              if (text := pruefen(schluessel, str(wert))) is not None]
    if fehler:
        return {"ok": False, "fehler": fehler, "gespeichert": []}

    gespeichert, misslungen = [], []
    neustart = False
    for schluessel, wert in werte.items():
        ziel = cfg.persist(schluessel, str(wert).strip())
        if ziel is None:
            misslungen.append(schluessel)
            continue
        gespeichert.append(schluessel)
        feld = felder().get(schluessel)
        if feld and feld[5]:
            neustart = True
    return {
        "ok": not misslungen,
        "fehler": [f"Konnte nicht geschrieben werden: {', '.join(misslungen)}"] if misslungen else [],
        "gespeichert": gespeichert,
        "neustart_noetig": neustart,
        "datei": str(cfg.env_file) if cfg.env_file else "",
    }
