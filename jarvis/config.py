"""Zentrale Konfiguration - alles kommt aus der .env, mit sinnvollen Defaults.

Die .env wird robust gesucht: Arbeitsverzeichnis, Projektordner und alle
Elternordner. Gefundener Pfad steht in ENV_FILE und wird beim Start angezeigt -
damit "warum zieht meine Einstellung nicht?" nie wieder Rätselraten ist.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

ENV_FILE: Optional[Path] = None
ENV_SEARCHED: List[Path] = []


def _candidate_env_files() -> List[Path]:
    here = Path(__file__).resolve().parent          # .../jarvis
    roots: List[Path] = [Path.cwd(), here.parent, here]
    roots.extend(here.parents)
    seen, out = set(), []
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        # .env.txt: Windows blendet Endungen aus, "Neue Textdatei" wird schnell zu .env.txt
        for name in (".env", ".env.txt", "jarvis.env"):
            out.append(root / name)
    return out


def _load_env() -> None:
    global ENV_FILE, ENV_SEARCHED
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    explicit = os.getenv("JARVIS_ENV")
    candidates = ([Path(explicit).expanduser()] if explicit else []) + _candidate_env_files()
    ENV_SEARCHED = candidates
    for path in candidates:
        try:
            if path.is_file():
                load_dotenv(path, override=False, encoding="utf-8-sig")
                ENV_FILE = path
                return
        except OSError:
            continue


_load_env()


def _clean(value: str) -> str:
    """Entfernt Anführungszeichen, Kommentare am Zeilenende und Leerraum."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value.strip()


def _s(key: str, default: str) -> str:
    raw = os.getenv(key)
    return _clean(raw) if raw is not None else default


def _p(key: str, default: str) -> Path:
    """Pfad aus der .env: Anfuehrungszeichen weg, %VARS% aufloesen, ~ expandieren.

    Und AUFGELOEST. Das ist unter Windows kein Schoenheitsschritt: Fuer
    Pfade unter einem Benutzernamen mit Punkt liefert das System an
    manchen Stellen die 8.3-Kurzform (JB454~1.NIE statt j.nievelstein).
    Wer die Vault ueber eine solche Kurzform angibt - oder ueber eine
    Umgebungsvariable, die sie enthaelt -, bekommt zwei Schreibweisen
    desselben Ordners.

    `Path.relative_to()` kennt diesen Zusammenhang nicht und wirft einen
    ValueError - und zwar mitten in `_backup()`, also bei JEDER aendernden
    Operation. Gemessen am 21.09. an einer Vault unter einem Kurznamen:
    `notiz_loeschen` scheiterte mit "is not in the subpath of", die
    Aufgabe ging in die Neuplanung, fand keinen anderen Weg und landete
    als toter Brief in der Ablage - wegen einer Schreibweise.

    `resolve()` macht aus beiden Formen dieselbe. Schlaegt es fehl (ein
    Netzlaufwerk, das gerade nicht da ist), bleibt der Pfad wie er war:
    Ein nicht aufloesbarer Pfad ist kein Grund, gar nicht erst zu starten.
    """
    raw = _s(key, default)
    raw = os.path.expandvars(raw)
    pfad = Path(raw).expanduser()
    try:
        return pfad.resolve()
    except OSError:
        return pfad


def _b(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return _clean(raw).lower() in ("1", "true", "yes", "ja", "on", "an")


def _i(key: str, default: int) -> int:
    try:
        return int(_s(key, str(default)))
    except ValueError:
        return default


def _f(key: str, default: float) -> float:
    try:
        return float(_s(key, str(default)).replace(",", "."))
    except ValueError:
        return default


# Tippfehler, die beim Abschreiben einer Adresse entstehen. 'tp://localhost'
# ist keine gültige URL - requests wirft dann einen kryptischen
# InvalidSchema-Fehler, und man sucht die Ursache beim Server statt in der .env.
_SCHEMA_TIPPFEHLER = ("tp://", "ttp://", "htp://", "htt://", "hhttp://", "http//")


def url_normieren(roh: str) -> str:
    """Eine Serveradresse geradebiegen: Schema ergänzen, Tippfehler heilen.

    Absichtlich nachsichtig. Eine Adresse ohne Schema ('192.168.1.5:11434')
    ist gemeint, aber unbrauchbar; ein verrutschtes 'tp://' ebenso. Beides
    stillschweigend zu reparieren ist besser, als den Benutzer mit einem
    Verbindungsfehler auf die falsche Fährte zu schicken.
    """
    text = str(roh or "").strip().strip('"').strip("'").rstrip("/")
    if not text:
        return ""
    for falsch in _SCHEMA_TIPPFEHLER:
        if text.lower().startswith(falsch):
            text = "http://" + text[len(falsch):]
            break
    if "://" not in text:
        text = "http://" + text
    return text.rstrip("/")


def _url(key: str, default: str) -> str:
    return url_normieren(_s(key, default))


@dataclass
class Settings:
    # --- Pfade ---
    vault_path: Path = field(default_factory=lambda: _p("OBSIDIAN_VAULT", "./obsidian"))
    store_path: Path = field(default_factory=lambda: _p("VECTOR_STORE_PATH", "./vector_store"))
    inbox_folder: str = field(default_factory=lambda: _s("INBOX_FOLDER", "00_Inbox"))

    # --- Ollama ---
    host: str = field(default_factory=lambda: _url("OLLAMA_HOST", "http://localhost:11434"))
    llm_model: str = field(default_factory=lambda: _s("LLM_MODEL", "llama3"))
    # Ausweichserver, falls der erste nicht antwortet. Typischer Fall: ein
    # eigener KI-Rechner im Netz, der nicht immer läuft - dann soll Zeus auf
    # das lokale Ollama zurückfallen statt gar nicht zu starten.
    host_alternative: str = field(
        default_factory=lambda: _url("OLLAMA_HOST_ALTERNATIVE", ""))
    # Das kleinere Modell für den Ausweichserver. Der große Server trägt
    # vielleicht ein 27b-Modell, der Laptop nicht.
    llm_model_alternative: str = field(
        default_factory=lambda: _s("LLM_MODEL_ALTERNATIVE", ""))
    # Kleines Modell für Routinearbeit: Pläne, Ordnerwahl, JSON-Entscheidungen,
    # Lektionsformulierung. Bei langsamer Hardware der größte Einzelgewinn -
    # diese Aufrufe brauchen kein großes Modell. Leer = überall das große nehmen.
    fast_model: str = field(default_factory=lambda: _s("FAST_MODEL", ""))
    embedding_model: str = field(default_factory=lambda: _s("EMBEDDING_MODEL", "nomic-embed-text"))
    # Anzahl auf die GPU auszulagernder Schichten. 0 = Ollama entscheidet,
    # 99 = so viel wie möglich erzwingen.
    num_gpu: int = field(default_factory=lambda: _i("NUM_GPU", 0))
    # Gesamtbudget je Anfrage (Wanduhr). Greift auch beim Streaming.
    request_timeout: int = field(default_factory=lambda: _i("REQUEST_TIMEOUT", 600))
    # Abbruch, wenn SO LANGE kein Token mehr kommt. Das ist der eigentliche
    # Hänger-Schutz - eine laufende Generierung läuft nie hier hinein.
    idle_timeout: int = field(default_factory=lambda: _i("IDLE_TIMEOUT", 90))
    num_ctx: int = field(default_factory=lambda: _i("NUM_CTX", 8192))
    # Obergrenze erzeugter Token: kappt Wiederholungsschleifen, bevor sie den
    # ganzen Kontext vollschreiben. -1 = unbegrenzt (nicht empfohlen).
    num_predict: int = field(default_factory=lambda: _i("NUM_PREDICT", 2048))
    repeat_penalty: float = field(default_factory=lambda: _f("REPEAT_PENALTY", 1.15))
    keep_alive: str = field(default_factory=lambda: _s("KEEP_ALIVE", "30m"))
    warmup: bool = field(default_factory=lambda: _b("WARMUP", True))
    # Denkmodelle (qwen3, deepseek-r1) verbrauchen ihr Token-Budget fürs
    # Nachdenken und antworten dann nicht mehr. "aus" schaltet das Denken ab,
    # "an" erzwingt es, "auto" überlässt es dem Modell.
    denken: str = field(default_factory=lambda: _s("DENKEN", "auto"))
    temperature: float = field(default_factory=lambda: _f("TEMPERATURE", 0.3))
    embed_workers: int = field(default_factory=lambda: _i("EMBED_WORKERS", 4))

    # --- Retrieval ---
    chunk_size: int = field(default_factory=lambda: _i("CHUNK_SIZE", 900))
    chunk_overlap: int = field(default_factory=lambda: _i("CHUNK_OVERLAP", 120))
    top_k: int = field(default_factory=lambda: _i("TOP_K", 6))
    fetch_k: int = field(default_factory=lambda: _i("FETCH_K", 24))
    max_context_chars: int = field(default_factory=lambda: _i("MAX_CONTEXT_CHARS", 14000))
    history_turns: int = field(default_factory=lambda: _i("HISTORY_TURNS", 6))
    # Notizen unter dieser Größe kommen IMMER vollständig in den Kontext,
    # größere werden abschnittsweise (ganze Kapitel, nie mitten im Satz) geliefert.
    full_note_chars: int = field(default_factory=lambda: _i("FULL_NOTE_CHARS", 4500))
    section_max_chars: int = field(default_factory=lambda: _i("SECTION_MAX_CHARS", 6000))

    # --- Werkzeuge / Schreibzugriff ---
    tools_enabled: bool = field(default_factory=lambda: _b("TOOLS_ENABLED", True))
    native_tools: str = field(default_factory=lambda: _s("NATIVE_TOOLS", "auto"))  # auto|ja|nein
    max_tool_steps: int = field(default_factory=lambda: _i("MAX_TOOL_STEPS", 8))
    # Triage im Gespraech (gespraech/triage.py): Darf der Aufgabenbewerter bei
    # echter Unklarheit das kleine Modell fragen? Vorgabe nein - die Regeln
    # entscheiden, und im Zweifel gilt der Weg VAULT (quelle=rueckfall).
    # Ein Modellaufruf kostet hier gemessene 10 s je Eingabe.
    triage_modell: bool = field(default_factory=lambda: _b("TRIAGE_MODELL", False))
    # Schleifenschutz (gespraech/schleifenschutz.py). Ab dieser Wortmengen-
    # Aehnlichkeit (Jaccard) gilt eine Suche als Wiederholung einer frueheren
    # derselben Runde und wird VOR der Ausfuehrung abgewiesen. Warum 0.6:
    # Beispiele aus dem Testsatz stehen im Modulkopf.
    such_aehnlichkeit: float = field(default_factory=lambda: _f("SUCH_AEHNLICHKEIT", 0.6))
    # Abweisungen kosten keinen Werkzeugschritt - deshalb ein eigener Deckel,
    # sonst waere die kostenlose Abweisung die naechste Endlosschleife. Ist er
    # erreicht, folgt sofort die Schlussrunde ohne Werkzeuge.
    max_abweisungen: int = field(default_factory=lambda: _i("MAX_ABWEISUNGEN", 3))
    confirm_writes: bool = field(default_factory=lambda: _b("CONFIRM_WRITES", False))
    backups_enabled: bool = field(default_factory=lambda: _b("BACKUPS_ENABLED", True))
    trash_folder: str = field(default_factory=lambda: _s("TRASH_FOLDER", "_Papierkorb"))
    link_section: str = field(default_factory=lambda: _s("LINK_SECTION", "Verwandte Notizen"))
    # Obergrenze fuer automatische Verweise JE NOTIZ, in beide Richtungen.
    # Ohne die sammelte eine beliebte Notiz neun "thematisch verwandt"-Zeilen ein.
    max_links_pro_notiz: int = field(default_factory=lambda: _i("MAX_LINKS_PRO_NOTIZ", 8))

    # --- Betrieb / Sicherheit ---
    language: str = field(default_factory=lambda: _s("LANGUAGE", "Deutsch"))
    write_retries: int = field(default_factory=lambda: _i("WRITE_RETRIES", 3))
    max_files_per_run: int = field(default_factory=lambda: _i("MAX_FILES_PER_RUN", 25))
    max_note_chars: int = field(default_factory=lambda: _i("MAX_NOTE_CHARS", 60000))
    protected_folders: str = field(default_factory=lambda: _s("PROTECTED_FOLDERS", ""))
    # Der Regelordner: was dort steht, gilt als Vorgabe für die Arbeitsweise
    # und wird in JEDEN Systemprompt gehoben. Er ist SCHREIBGESCHÜTZT - der
    # Agent darf seine eigenen Regeln lesen, aber nicht ändern. Leer = aus.
    regel_ordner: str = field(default_factory=lambda: _s("REGEL_ORDNER",
                                                         "_System/Grundregeln"))
    # Obergrenze für die Regeln im Prompt. Der Kontext ist ein Nullsummenspiel:
    # Jede Zeile Regel verdrängt eine Zeile Notiz.
    regel_max_zeichen: int = field(default_factory=lambda: _i("REGEL_MAX_ZEICHEN", 6000))
    dry_run: bool = field(default_factory=lambda: _b("DRY_RUN", False))
    log_enabled: bool = field(default_factory=lambda: _b("LOG_ENABLED", True))
    planner_enabled: bool = field(default_factory=lambda: _b("PLANNER_ENABLED", True))
    plan_confirm: bool = field(default_factory=lambda: _b("PLAN_CONFIRM", False))
    content_temperature: float = field(default_factory=lambda: _f("CONTENT_TEMPERATURE", 0.4))
    # Zielumfang je erzeugter Notiz. Bei langsamer Hardware der direkteste Hebel:
    # halb so viele Wörter = halb so lange.
    note_target_words: int = field(default_factory=lambda: _i("NOTE_TARGET_WORDS", 350))

    @property
    def content_num_predict(self) -> int:
        """Token-Obergrenze für Notizinhalte, abgeleitet aus der Zielwortzahl."""
        aus_woertern = int(self.note_target_words * 2.2) + 250
        return max(256, min(self.num_predict * 2, aus_woertern))

    # --- Selbstinspektion: eigener Quellcode ---
    # Verzeichnisse, aus denen der Agent LESEN darf (kommagetrennt).
    # Leer = der Projektordner selbst. Geschrieben wird dort NIE.
    code_roots: str = field(default_factory=lambda: _s("CODE_ROOTS", ""))
    # Vault-Ordner, in den importierter Quellcode als Notizen abgelegt wird
    code_folder: str = field(default_factory=lambda: _s("CODE_FOLDER", "_System/Code"))
    max_code_files: int = field(default_factory=lambda: _i("MAX_CODE_FILES", 60))

    @property
    def code_root_list(self) -> List[Path]:
        """Erlaubte Lesewurzeln. Ohne Angabe der Ordner des Programms selbst."""
        if self.code_roots.strip():
            wurzeln = []
            for teil in self.code_roots.split(","):
                teil = teil.strip().strip('"').strip("'")
                if teil:
                    try:
                        wurzeln.append(Path(os.path.expandvars(teil)).expanduser().resolve())
                    except OSError:
                        continue
            return wurzeln
        return [Path(__file__).resolve().parent.parent]

    # --- Lernen / Selbsterweiterung ---
    # Jede abgeschlossene Aenderung bekommt einen Commit in der Vault.
    # Beantwortet die Frage, die bei unbeaufsichtigtem Betrieb als erste
    # kommt: was hat Zeus letzten Dienstag geaendert? Ohne Git installiert
    # oder ohne Repository passiert nichts - gemeldet, nicht geworfen.
    # Zugriff auf das Internet (web_suchen, web_lesen). Bis zum 21.09. gab es
    # das gar nicht - der Agent tat bei einer Recherchebitte so, als suche er
    # online, und durchsuchte in Wahrheit die Vault. Abschalten macht ihn
    # wieder rein lokal; er SAGT es dann, statt auszuweichen.
    web_enabled: bool = field(default_factory=lambda: _b("WEB_ENABLED", True))
    # Schluessel fuer eine ECHTE Suchmaschine (Brave Search API). Leer =
    # Zeus schlaegt nur nach (Kurzantworten, Wikipedia) und sagt dem Nutzer,
    # wenn das fuer seine Frage nicht reicht. Ohne Konto gibt es keine
    # allgemeine Websuche - das am 21.09. an vier Anbietern nachgemessen.
    web_such_schluessel: str = field(
        default_factory=lambda: _s("WEB_SUCH_SCHLUESSEL", ""))
    vault_git: bool = field(default_factory=lambda: _b("VAULT_GIT", True))
    learning_enabled: bool = field(default_factory=lambda: _b("LEARNING_ENABLED", True))
    learning_folder: str = field(default_factory=lambda: _s("LEARNING_FOLDER", "_Lernen"))
    skills_folder: str = field(default_factory=lambda: _s("SKILLS_FOLDER", "_Fertigkeiten"))
    lessons_in_prompt: int = field(default_factory=lambda: _i("LESSONS_IN_PROMPT", 6))
    self_extension: bool = field(default_factory=lambda: _b("SELF_EXTENSION", True))
    max_skill_steps: int = field(default_factory=lambda: _i("MAX_SKILL_STEPS", 12))

    # --- Betriebssystem-Kern ---
    # Der Kern laeuft mit, sobald KERN_AKTIV steht: Module, Ereignisbus,
    # Auftragsplaner. Ohne ihn verhaelt sich Zeus genau wie vorher - das ist
    # Absicht, damit ein Fehler im Kern nicht das Gespraech mitnimmt.
    kern_aktiv: bool = field(default_factory=lambda: _b("KERN_AKTIV", True))
    # Wie viele Auftraege gleichzeitig. Schreibende werden ohnehin
    # serialisiert; mehr als vier bringt nichts, weil dann alle auf Ollama
    # warten statt auf die Vault.
    kern_arbeiter: int = field(default_factory=lambda: _i("KERN_ARBEITER", 2))
    # Was Zeus darf. 0 lesen, 1 schreiben (Standard), 2 unbeaufsichtigt
    # arbeiten, 3 eigenen Code aendern. Nur hier zu aendern - Zeus kann sich
    # die Stufe nicht selbst geben, und das ist der Punkt.
    rechte_stufe: int = field(default_factory=lambda: _i("RECHTE_STUFE", 1))
    # Wie oft die Hintergrundwartung laeuft (Sekunden). 0 schaltet sie ab.
    # Greift nur ab Rechtestufe 2.
    wartung_alle: int = field(default_factory=lambda: _i("WARTUNG_ALLE", 0))

    # --- Persistente Aufgaben (Ziele, Warteschlange, Termine, Ablaeufe) ---
    # Der Aufgabenstapel laeuft mit, sobald AUFGABEN_AKTIV steht. Ohne ihn
    # gibt es keine Ziele, keine Wiederaufnahme nach einem Absturz und keine
    # Termine - Zeus verhaelt sich dann wie vor dem Umbau.
    aufgaben_aktiv: bool = field(default_factory=lambda: _b("AUFGABEN_AKTIV", True))
    # Wie viele Aufgaben gleichzeitig bearbeitet werden. Einer reicht fuer
    # den Anfang: Schreibende Schritte werden ohnehin serialisiert, und zwei
    # Arbeiter, die beide auf dasselbe Modell warten, sind nicht schneller.
    aufgaben_arbeiter: int = field(default_factory=lambda: _i("AUFGABEN_ARBEITER", 1))
    # Ab welchem Risiko eine unbeaufsichtigte Handlung eine Freigabe braucht:
    # gering, mittel oder hoch. 'hoch' fragt nur bei Loeschen, Ueberschreiben
    # und Zusammenfuehren; 'mittel' auch bei Verschieben und Ersetzen.
    freigabe_ab_risiko: str = field(
        default_factory=lambda: _s("FREIGABE_AB_RISIKO", "hoch"))

    # --- Budgets je Aufgabe (Master-Prompt §30) ---
    # 0 heisst "nichts erlaubt", eine NEGATIVE Zahl heisst "unbegrenzt".
    # Die Unterscheidung ist Absicht: Eine auf null gesetzte Schranke soll
    # greifen und nicht stillschweigend zur Voreinstellung werden.
    budget_modellaufrufe: int = field(
        default_factory=lambda: _i("BUDGET_MODELLAUFRUFE", 40))
    budget_werkzeugaufrufe: int = field(
        default_factory=lambda: _i("BUDGET_WERKZEUGAUFRUFE", 60))
    budget_laufzeit: int = field(default_factory=lambda: _i("BUDGET_LAUFZEIT", 900))
    budget_versuche: int = field(default_factory=lambda: _i("BUDGET_VERSUCHE", 3))
    budget_neuplanungen: int = field(
        default_factory=lambda: _i("BUDGET_NEUPLANUNGEN", 2))
    budget_zeichen: int = field(default_factory=lambda: _i("BUDGET_ZEICHEN", 400000))
    # Wie viel Platz der zusammengestellte Kontext eines Schritts bekommt.
    # Kleiner als PROMPT_CHAR_BUDGET, weil Werkzeugliste und Anweisung noch
    # dazukommen. Was nicht hineinpasst, faellt nach Rang weg - und wird
    # gemeldet, statt still zu verschwinden (kern/kontext.py).
    kontext_platz: int = field(default_factory=lambda: _i("KONTEXT_PLATZ", 6000))

    # --- Sitzungsgedächtnis ---
    # Der Schalter legt Lesen UND Schreiben still, sonst kann der Messstand
    # keinen A/B-Vergleich fahren.
    session_memory: bool = field(default_factory=lambda: _b("SESSION_MEMORY", True))
    session_folder: str = field(default_factory=lambda: _s("SESSION_FOLDER", "_Sitzungen"))
    session_summary_every: int = field(
        default_factory=lambda: _i("SESSION_SUMMARY_EVERY", 10))
    session_recall_chars: int = field(
        default_factory=lambda: _i("SESSION_RECALL_CHARS", 1500))
    session_facts_in_prompt: int = field(
        default_factory=lambda: _i("SESSION_FACTS_IN_PROMPT", 5))

    # --- Episodengedächtnis und Arbeitsgedächtnis (episoden.py, kontextwahl.py) ---
    # Jeder Gesprächszug wörtlich in <Vault>/.jarvis/episoden.jsonl - mit Zeit,
    # Herkunft, Bezügen und Vorgaben. Damit ist erreichbar, was aus dem Verlauf
    # (HISTORY_TURNS) gefallen und noch nicht verdichtet ist. Aus = nichts wird
    # abgelegt und kein Arbeitsgedächtnis gebaut; Zeus verhält sich wie vorher.
    episoden_gedaechtnis: bool = field(
        default_factory=lambda: _b("EPISODEN_GEDAECHTNIS", True))
    # So viele Züge bleiben in der Datei; ältere fallen beim Verdichten weg.
    episoden_behalten: int = field(default_factory=lambda: _i("EPISODEN_BEHALTEN", 2000))
    # Höchstens so viele frühere Gesprächsstellen je Frage im Prompt.
    episoden_im_prompt: int = field(default_factory=lambda: _i("EPISODEN_IM_PROMPT", 4))
    # Höchstens so viele geltende Vorgaben dieser Sitzung im Prompt.
    episoden_vorgaben: int = field(default_factory=lambda: _i("EPISODEN_VORGABEN", 8))
    # Deterministische Prüfung der Antwort: Rechnungen, Grenzen aus Vorgaben,
    # [[Quellen]], Herkunft bei fehlenden Belegen (gespraech/pruefung.py).
    antwort_pruefen: bool = field(default_factory=lambda: _b("ANTWORT_PRUEFEN", True))

    @property
    def sprach_kuerzel(self) -> str:
        """'Deutsch' -> 'de', 'English' -> 'en'. Für Stimmenauswahl und Texte."""
        wert = (self.language or "").strip().lower()
        tabelle = {
            "de": ("deutsch", "german", "de", "de-de"),
            "en": ("englisch", "english", "en", "en-us", "en-gb", "us", "uk"),
            "fr": ("französisch", "franzoesisch", "french", "fr"),
            "es": ("spanisch", "spanish", "es"),
            "it": ("italienisch", "italian", "it"),
        }
        for kuerzel, namen in tabelle.items():
            if wert in namen or wert.startswith(kuerzel):
                return kuerzel
        return "de"

    @property
    def protected_list(self) -> List[str]:
        """Ordner, in die NICHT geschrieben werden darf.

        Der Regelordner steht immer mit drin, ohne dass ihn jemand eintragen
        muss. Eine Regel, die der Regulierte selbst umschreiben kann, ist
        keine - und ein Agent, der beim Aufräumen versehentlich seine eigenen
        Vorgaben überschreibt, verliert sie unbemerkt.
        """
        eintraege = [p.strip().strip("/") for p in self.protected_folders.split(",")
                     if p.strip()]
        regeln = (self.regel_ordner or "").strip().strip("/")
        if regeln and regeln not in eintraege:
            eintraege.append(regeln)
        return eintraege

    @property
    def system_ordner(self) -> List[str]:
        """Ordner, die der Agent selbst führt.

        Sie sind bewusst unverlinkt - eine Sitzungsnotiz oder eine Lektion hat
        keinen Platz im Wissensgraphen. Ohne diese Liste meldete `--wartung` sie
        jeden Lauf aufs Neue als 'verwaist', und der eine echte Fund ging in
        dreißig erwarteten unter.
        """
        namen = [self.learning_folder, self.skills_folder, self.session_folder,
                 self.trash_folder, self.code_folder, self.regel_ordner]
        return [n.strip().strip("/") for n in namen if (n or "").strip()]

    def ist_systemordner(self, ordner: str) -> bool:
        pfad = (ordner or "").strip().strip("/").replace("\\", "/")
        for name in self.system_ordner:
            if pfad == name or pfad.startswith(name + "/"):
                return True
        return False

    @property
    def regel_pfad(self) -> Optional[Path]:
        ordner = (self.regel_ordner or "").strip().strip("/")
        return (self.vault_path / ordner) if ordner else None

    @property
    def prompt_char_budget(self) -> int:
        """Wie viele Zeichen der Prompt höchstens haben darf.

        Faustregel: ~3,3 Zeichen je Token bei deutschem Text. Vom Kontextfenster
        bleiben 35 % für die Antwort reserviert. Ein Prompt, der num_ctx sprengt,
        wird von Ollama stillschweigend abgeschnitten - genau dann fehlen dem
        Modell die Anweisungen und es fängt an zu schwafeln oder zu schleifen.
        """
        aus_kontext = int(self.num_ctx * 3.3 * 0.65)
        return max(2000, min(self.max_context_chars, aus_kontext))

    # --- Persona ---
    # auto = selbst entscheiden, an/aus = erzwingen. Bei zerrissener Darstellung
    # im alten cmd.exe hilft LIVE_VIEW=aus.
    live_view: str = field(default_factory=lambda: _s("LIVE_VIEW", "auto"))
    # Aufgabenliste (von Hand gepflegt, siehe vorhaben.py). Leer = suchen.
    aufgaben_datei: str = field(default_factory=lambda: _s("AUFGABEN_DATEI", ""))
    # Nach wie vielen Sekunden ohne Eingabe in die Aufgabenliste gesehen wird.
    # 0 = nie. Bewusst aus: Eine Oberfläche, die von selbst etwas vorschlägt,
    # während man nachdenkt, ist eine Zumutung, wenn man sie nicht bestellt hat.
    leerlauf_sekunden: int = field(default_factory=lambda: _i("LEERLAUF_SEKUNDEN", 0))
    # Obergrenze für die Zeichenbreite. 0 = volle Fensterbreite. Ein Wert um
    # 120 hilft auf sehr breiten Bildschirmen der Lesbarkeit - und er begrenzt
    # den Schaden beim Verkleinern des Fensters, weil bereits gedruckte Zeilen
    # dann seltener über den neuen Rand hinausragen.
    ui_max_breite: int = field(default_factory=lambda: _i("UI_MAX_WIDTH", 0))
    assistant_name: str = field(default_factory=lambda: _s("ASSISTANT_NAME", "JARVIS"))
    user_title: str = field(default_factory=lambda: _s("USER_TITLE", "Sir"))

    # --- Sprache ---
    tts_enabled: bool = field(default_factory=lambda: _b("TTS_ENABLED", True))
    tts_engine: str = field(default_factory=lambda: _s("TTS_ENGINE", "auto"))
    # Teil des Stimmennamens ("David", "Ryan") ODER leer für automatische Wahl
    tts_voice: str = field(default_factory=lambda: _s("TTS_VOICE", ""))
    # maennlich | weiblich | egal - steuert die automatische Wahl
    tts_gender: str = field(default_factory=lambda: _s("TTS_GENDER", "egal"))
    tts_rate: int = field(default_factory=lambda: _i("TTS_RATE", 175))
    # Begrüßung beim Start vorlesen
    speak_greeting: bool = field(default_factory=lambda: _b("SPEAK_GREETING", True))
    # Eigener Begrüßungstext; leer = automatisch aus Sprache und Notizzahl
    greeting_text: str = field(default_factory=lambda: _s("GREETING_TEXT", ""))
    # --- piper: lokale, natürlich klingende Stimmen (pip install piper-tts) ---
    # Stimmenkennung, z. B. de_DE-thorsten-high. LEER = automatisch die zur
    # eingestellten LANGUAGE passende Stimme. Eine feste englische Vorgabe hier
    # führte dazu, dass bei deutscher Ausgabe eine englische Stimme vorlas.
    piper_voice: str = field(default_factory=lambda: _s("PIPER_VOICE", ""))
    # Nur für Mehr-Sprecher-Modelle wie de_DE-thorsten_emotional-medium
    piper_speaker: int = field(default_factory=lambda: _i("PIPER_SPEAKER", -1))
    # Ordner mit den .onnx-Dateien. Leer = "stimmen" neben dem Programm.
    piper_data_dir: str = field(default_factory=lambda: _s("PIPER_DATA_DIR", ""))
    # Nur für ältere Installationen mit separatem piper-Programm
    piper_binary: str = field(default_factory=lambda: _s("PIPER_BINARY", "piper"))
    piper_model: str = field(default_factory=lambda: _s("PIPER_MODEL", ""))
    # Tempo-/Klangfeinschliff für piper (1.0 = Originaltempo, größer = langsamer)
    piper_length_scale: float = field(default_factory=lambda: _f("PIPER_LENGTH_SCALE", 1.0))
    piper_noise_scale: float = field(default_factory=lambda: _f("PIPER_NOISE_SCALE", 0.667))
    piper_noise_w: float = field(default_factory=lambda: _f("PIPER_NOISE_W", 0.8))

    @property
    def piper_data_path(self) -> Path:
        """Ordner der Stimmdateien. Standard: 'stimmen' neben dem Programm."""
        if self.piper_data_dir.strip():
            return Path(os.path.expandvars(self.piper_data_dir.strip())).expanduser()
        return Path(__file__).resolve().parent.parent / "stimmen"

    stt_enabled: bool = field(default_factory=lambda: _b("STT_ENABLED", True))
    stt_model: str = field(default_factory=lambda: _s("STT_MODEL", "base"))
    stt_language: str = field(default_factory=lambda: _s("STT_LANGUAGE", "de"))
    stt_device: str = field(default_factory=lambda: _s("STT_DEVICE", "auto"))
    stt_compute_type: str = field(default_factory=lambda: _s("STT_COMPUTE_TYPE", ""))
    stt_samplerate: int = field(default_factory=lambda: _i("STT_SAMPLERATE", 16000))
    stt_silence_seconds: float = field(default_factory=lambda: _f("STT_SILENCE_SECONDS", 1.6))
    stt_max_seconds: float = field(default_factory=lambda: _f("STT_MAX_SECONDS", 45.0))

    # --- Verhalten ---
    auto_sync_on_start: bool = field(default_factory=lambda: _b("AUTO_SYNC_ON_START", True))
    autosave_answers: bool = field(default_factory=lambda: _b("AUTOSAVE_ANSWERS", False))

    # ------------------------------------------------------------------ URLs
    @property
    def chat_url(self) -> str:
        return f"{self.host}/api/chat"

    @property
    def embed_url(self) -> str:
        return f"{self.host}/api/embed"

    @property
    def embed_url_legacy(self) -> str:
        return f"{self.host}/api/embeddings"

    @property
    def tags_url(self) -> str:
        return f"{self.host}/api/tags"

    @property
    def manifest_path(self) -> Path:
        return self.store_path / "manifest.json"

    @property
    def work_dir(self) -> Path:
        """Arbeitsordner in der Vault: Backups und Änderungsjournal.

        Beginnt mit einem Punkt und wird deshalb vom Scanner ignoriert -
        taucht also weder in Obsidian noch im Index auf.
        """
        return self.vault_path / ".jarvis"

    # ------------------------------------------------------------ Diagnose
    @property
    def env_file(self) -> Optional[Path]:
        return ENV_FILE

    @property
    def vault_ok(self) -> bool:
        try:
            return self.vault_path.is_dir()
        except OSError:
            return False

    def diagnose(self) -> List[Tuple[str, bool, str]]:
        """Liste aus (Titel, ok?, Hinweis) für /doktor und den Start."""
        checks: List[Tuple[str, bool, str]] = []

        if ENV_FILE:
            checks.append((".env geladen", True, str(ENV_FILE)))
        else:
            searched = "\n      ".join(str(p) for p in ENV_SEARCHED[:6])
            checks.append((
                ".env geladen", False,
                "keine .env gefunden – es gelten die Standardwerte.\n"
                f"      Gesucht wurde u. a. in:\n      {searched}\n"
                "      Tipp: Windows blendet Dateiendungen aus – aus '.env' wird schnell '.env.txt'.",
            ))

        checks.append((
            "Vault-Ordner", self.vault_ok,
            str(self.vault_path) if self.vault_ok else f"{self.vault_path} existiert nicht",
        ))

        if self.vault_ok:
            md = sum(1 for _ in self.vault_path.rglob("*.md"))
            checks.append(("Markdown-Dateien", md > 0, f"{md} .md-Dateien gefunden"))

        try:
            self.store_path.mkdir(parents=True, exist_ok=True)
            writable = os.access(self.store_path, os.W_OK)
        except OSError as exc:
            checks.append(("Vektor-Speicher beschreibbar", False, str(exc)))
        else:
            checks.append((
                "Vektor-Speicher beschreibbar", writable,
                str(self.store_path) if writable else f"keine Schreibrechte: {self.store_path}",
            ))
        return checks

    def persist(self, key: str, value: str) -> Optional[Path]:
        """Schreibt einen Wert dauerhaft in die .env (legt sie bei Bedarf an)."""
        global ENV_FILE
        target = ENV_FILE or (Path.cwd() / ".env")
        lines: List[str] = []
        if target.is_file():
            lines = target.read_text(encoding="utf-8-sig").splitlines()
        replaced = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{key}=") or line.strip().startswith(f"{key} ="):
                lines[i] = f"{key}={value}"
                replaced = True
                break
        if not replaced:
            lines.append(f"{key}={value}")
        try:
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError:
            return None
        ENV_FILE = target
        os.environ[key] = value
        return target


settings = Settings()


def reload_settings() -> Settings:
    """Einstellungen neu einlesen - in-place, damit bestehende Referenzen gültig bleiben."""
    from dataclasses import fields

    fresh = Settings()
    for f in fields(fresh):
        setattr(settings, f.name, getattr(fresh, f.name))
    return settings
