"""Terminal-Interface im JARVIS-Stil: Streaming-Chat, Slash-Befehle, Sprache."""
from __future__ import annotations

import os
import shutil
import sys
import time
from typing import List, Optional, Tuple

from pathlib import Path

from .agent import Answer, JarvisAgent
from .befehle import hilfetabelle
from .config import reload_settings, settings
from .eingabe import Echosperre, Eingabeleser, datei_lesen
from .leerlauf import Leerlauf
from .voice import Listener, Speaker

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    RICH = True
except ImportError:  # pragma: no cover
    RICH = False
    Console = None  # type: ignore

# Schmaler als das wird nicht gezeichnet - darunter ist jeder Rahmen Unsinn,
# und Rich rechnet mit negativen Innenbreiten.
MIN_BREITE = 40

# Rückgabe von _read_input(), wenn nichts kam und der Leerlauf greifen soll.
LEERLAUF_MARKE = object()

BANNER = r"""
      ██  █████  ██████  ██    ██ ██ ███████
      ██ ██   ██ ██   ██ ██    ██ ██ ██
      ██ ███████ ██████  ██    ██ ██ ███████
 ██   ██ ██   ██ ██   ██  ██  ██  ██      ██
  █████  ██   ██ ██   ██   ████   ██ ███████
"""

# Die Hilfe wird aus jarvis/befehle.py ERZEUGT, nicht daneben gepflegt.
# Vorher stand sie als Text genau hier - und die Weboberflaeche hatte
# gar keine, weil sie diesen Text nicht kannte. Eine Liste an zwei
# Stellen wird irgendwann nur an einer gepflegt.
HELP = hilfetabelle("terminal")


def _vt_aktivieren() -> bool:
    """Windows-Konsole auf ANSI-Sequenzen umstellen.

    Ohne das ignoriert das klassische cmd.exe Farb- und Positionierbefehle -
    Rich zeichnet dann an Stellen, die es nicht kontrollieren kann, und die
    Ausgabe schiebt sich über den alten Bildschirminhalt.
    """
    if os.name != "nt":
        return True
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
        erfolg = False
        for handle_id in (-11, -12):          # stdout, stderr
            handle = kernel32.GetStdHandle(handle_id)
            modus = ctypes.c_uint32()
            if kernel32.GetConsoleMode(handle, ctypes.byref(modus)):
                kernel32.SetConsoleMode(
                    handle, modus.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING)
                erfolg = True
        return erfolg
    except Exception:
        return False


def _utf8_ausgabe() -> None:
    """Umlaute und Kästchen-Zeichen sauber ausgeben, auch bei Codepage 850."""
    for strom in (sys.stdout, sys.stderr):
        try:
            strom.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except Exception:
            pass


def bildschirm_leeren() -> None:
    """Wirklich leeren - console.clear() reicht auf altem cmd.exe nicht."""
    try:
        os.system("cls" if os.name == "nt" else "clear")
    except Exception:
        print("\n" * 5)


class JarvisConsole:
    def __init__(self, cfg=settings, agent=None, ausgabe=None,
                 eingabe: bool = True, terminal: bool = True):
        """Die Textoberfläche.

        agent:   Einen vorhandenen Agenten weiterbenutzen, statt einen zweiten
                 zu bauen. Die grafische Oberfläche braucht das - sonst läge
                 der Index zweimal im Speicher und die beiden Gesprächsverläufe
                 liefen auseinander.
        ausgabe: Eine fertige Rich-Konsole. Damit lassen sich ALLE Befehle
                 unverändert ausführen und ihr Ergebnis abgreifen, statt sie
                 für die grafische Oberfläche ein zweites Mal zu schreiben -
                 zwei Fassungen desselben Befehls driften garantiert
                 auseinander. Ohne Rich bleibt sie None; dann druckt out()
                 schlicht, und der Aufrufer fängt stdout ab.
        eingabe: False lässt den stdin-Lesethread weg. Im Fenstermodus gehört
                 die Tastatur der Oberfläche, nicht dem Terminal dahinter.
        terminal: False heißt "es gibt kein Terminal" - keine ANSI-Umschaltung,
                 keine Breitenmessung, keine mitlaufende Anzeige.
        """
        self.cfg = cfg
        self.eigenes_terminal = terminal
        if terminal:
            # Reihenfolge zählt: erst die Konsole vorbereiten, dann Rich
            # erzeugen - Rich prüft beim Anlegen, was das Terminal kann.
            self.ansi = _vt_aktivieren()
            _utf8_ausgabe()
            # soft_wrap=False: Rich bricht selbst um, statt es dem Terminal zu
            # überlassen. Nur so passt der Umbruch zur Rahmenbreite.
            self.console = Console(soft_wrap=False) if RICH else None
        else:
            self.ansi = False
            self.console = ausgabe
        self._letzte_groesse: Tuple[int, int] = (0, 0)
        self._fenster_pruefen()
        self.agent = agent or JarvisAgent(cfg)
        self.speaker = Speaker(cfg)
        self.listener = Listener(cfg)
        self.speaking = self.speaker.available
        self.last_answer: Optional[Answer] = None
        # Sammelt eingefügte Zeilen zu einer Eingabe (siehe eingabe.py)
        self.leser = Eingabeleser() if eingabe else None
        # Aufgabenliste und Leerlauf (siehe leerlauf.py, vorhaben.py)
        self.leerlauf = Leerlauf(self.agent, cfg)
        # Rückfrage vor einer Änderung - nur aktiv bei CONFIRM_WRITES=true.
        self.agent.tools.bestaetigung = self._aenderung_bestaetigen

    @property
    def live_moeglich(self) -> bool:
        """Taugt dieses Terminal für die mitlaufende Anzeige?

        Altes cmd.exe ohne ANSI und sehr niedrige Fenster vertragen sie nicht -
        dort wird schlicht mitgeschrieben statt neu gezeichnet.
        """
        einstellung = (self.cfg.live_view or "auto").lower()
        if einstellung in ("aus", "off", "nein", "false"):
            return False
        if not self.console:
            return False
        if einstellung in ("an", "on", "ja", "true"):
            return True
        if getattr(self.console, "legacy_windows", False) or not self.ansi:
            return False
        return (self.console.size.height or 24) >= 20

    # ------------------------------------------------------------ Fenstergröße
    def _fenster_pruefen(self) -> bool:
        """Fenstergröße neu vermessen. True, wenn sie sich geändert hat.

        Der Grund ist ein Fehler, der sich beim Verkleinern des Fensters zeigt:
        Rich merkt sich die Breite und zeichnet weiter in der alten. Die Rahmen
        laufen dann über den rechten Rand, das Terminal bricht sie um, und die
        Kästen schieben sich ineinander.

        Was NICHT zu retten ist: bereits gedruckte Zeilen. Die gehören dem
        Terminal, und ein umgebrochener Rahmen im Rückblättern bleibt kaputt -
        cmd.exe kann das nicht, Windows Terminal deutlich besser. Alles, was
        AB JETZT gedruckt wird, sitzt aber wieder richtig.
        """
        # Eine geliehene Konsole hat ihre Breite vom Aufrufer und darf nicht
        # nach der Terminalgröße verstellt werden - im Fenstermodus gibt es
        # gar kein Terminal, dessen Breite etwas bedeuten würde.
        if not self.console or not getattr(self, "eigenes_terminal", True):
            return False
        try:
            groesse = shutil.get_terminal_size(fallback=(100, 30))
        except (OSError, ValueError):
            return False
        breite = max(MIN_BREITE, groesse.columns - 1)   # eine Spalte Luft
        if self.cfg.ui_max_breite:
            breite = min(breite, self.cfg.ui_max_breite)
        hoehe = max(10, groesse.lines)
        if (breite, hoehe) == self._letzte_groesse:
            return False
        self._letzte_groesse = (breite, hoehe)
        # Zuweisen erzwingt die Neuvermessung. Ohne das bleibt Rich bei dem
        # Wert, den es beim Anlegen der Konsole ermittelt hat.
        self.console.width = breite
        self.console.height = hoehe
        return True

    @property
    def breite(self) -> int:
        self._fenster_pruefen()
        return self._letzte_groesse[0] or 100

    # ------------------------------------------------------------------ Print
    def out(self, renderable) -> None:
        if self.console:
            self._fenster_pruefen()
            self.console.print(renderable)
        else:
            print(renderable)

    def info(self, text: str) -> None:
        self.out(f"[dim]{text}[/dim]" if self.console else text)

    def warn(self, text: str) -> None:
        self.out(f"[yellow]⚠ {text}[/yellow]" if self.console else f"! {text}")

    # ------------------------------------------------------------------ Start
    def _kern_hochfahren(self) -> None:
        """Faehrt den Betriebssystem-Kern hoch - und meldet, was fehlt.

        Scheitert der Kern, laeuft die Sitzung trotzdem: Zeus verhaelt sich
        dann wie vor dem Umbau. Ein Betriebssystem, das das Gespraech
        verhindert, waere schlechter als gar keins.
        """
        if not getattr(self.cfg, "kern_aktiv", False):
            return
        try:
            kern = self.agent.kern_starten()
        except Exception as exc:
            self.warn(f"Der Betriebssystem-Kern kam nicht hoch "
                      f"({type(exc).__name__}: {exc}). Zeus arbeitet wie bisher.")
            return
        krank = [m for m in kern.status()["module"]
                 if not m["ok"] and m["zustand"] != "angehalten"]
        for modul in krank:
            # Nicht verschweigen und nicht dramatisieren: benennen.
            self.warn(f"{modul['name']}: {modul['meldung']}")
        antrag = kern.rechte.antrag()
        if antrag:
            self.info(f"Zeus beantragt Rechtestufe {antrag['auf']} "
                      f"({antrag['geglueckt']}/{antrag['laeufe']} geglueckt) – "
                      f"anzusehen mit /rechte antrag")

    def boot(self) -> bool:
        env_line = (
            f"Konfig: [cyan]{self.cfg.env_file}[/cyan]"
            if self.cfg.env_file
            else "Konfig: [yellow]keine .env gefunden – Standardwerte aktiv[/yellow]"
        )
        bildschirm_leeren()
        if self.console:
            self.console.print(Text(BANNER, style="bold cyan"))
            self.console.print(
                Panel(
                    f"[bold]{self.cfg.assistant_name}[/bold] – Second Brain von {self.cfg.user_title}\n"
                    f"Vault: [cyan]{self.cfg.vault_path}[/cyan]\n"
                    f"Modell: [cyan]{self.cfg.llm_model}[/cyan] · Embeddings: [cyan]{self.cfg.embedding_model}[/cyan]\n"
                    + env_line,
                    border_style="cyan",
                )
            )
        else:
            print(BANNER)

        # Serverwahl VOR allem anderen: Ist der KI-Rechner aus, wird jetzt auf
        # den Ausweichserver umgeschaltet - dann stimmen Modellprüfung,
        # Warmlauf und Index-Sync bereits auf den richtigen Server.
        if not self.agent.llm.verbindung_pruefen(melden=self.info):
            if self.agent.llm.umschaltgrund:
                self.warn(self.agent.llm.umschaltgrund)
        if self.agent.llm.auf_ausweich:
            self.warn(f"Ausweichbetrieb: {self.cfg.host} · {self.cfg.llm_model}. "
                      f"Zurück mit /server haupt, sobald der Hauptserver läuft.")

        if not self._preflight():
            return False

        if self.cfg.warmup and self.agent.llm.is_alive():
            # Ohne Warmlauf trifft die erste echte Frage auf ein kaltes Modell.
            # Bei einem 32b-Modell sind das Minuten, die wie ein Absturz wirken.
            if self.console:
                with self.console.status(
                    f"[cyan]Lade {self.cfg.llm_model} in den Speicher …", spinner="dots"
                ):
                    fehler = self.agent.llm.warmup()
            else:
                print(f"Lade {self.cfg.llm_model} …")
                fehler = self.agent.llm.warmup()
            if fehler:
                self.warn(fehler)

        warnings = self.agent.llm.check_models()
        for w in warnings:
            self.warn(w)

        if self.cfg.auto_sync_on_start:
            self._sync(force=False)
        else:
            self.agent.index.refresh_structure()
            self.info("Automatischer Sync deaktiviert – /sync bei Bedarf.")

        s = self.agent.structure
        self.info(
            f"Struktur erfasst: {s.note_count()} Notizen, "
            f"{len(s.folder_paths)} Ordner ({', '.join(s.folder_paths[:6]) or 'keine'})."
        )
        if self.cfg.tools_enabled:
            self.agent.detect_capabilities()
            self.info(
                "Schreibzugriff aktiv – ich kann Notizen anlegen, ändern, verschieben und "
                "verknüpfen. Rückgängig mit /rueckgaengig."
            )
            if not self.agent.native_tools:
                self.warn(
                    f"'{self.cfg.llm_model}' kennt keine Werkzeug-Schnittstelle. Schreibaufträge "
                    f"laufen über den Planer (funktioniert), freies Werkzeug-Denken ist aber "
                    f"eingeschränkt. Empfehlung: ollama pull qwen2.5:14b"
                )
        # Der Kern NACH dem Index-Abgleich: Das Gedächtnismodul fragt den
        # Bestand ab, und ein Modul, das eine leere Vault meldet, weil noch
        # nichts eingelesen war, wäre eine Falschmeldung.
        self._kern_hochfahren()

        if self.cfg.dry_run:
            self.warn("TROCKENLAUF aktiv (DRY_RUN=true) – es wird nichts wirklich geschrieben.")
        if self.speaker.available:
            self.info(f"Sprachausgabe: {self.speaker.status}")
        else:
            self.info(f"Sprachausgabe aus: {self.speaker.status}")
        self.info(f"Spracheingabe: {self.listener.status}")
        self.info("Tippen Sie /hilfe für alle Befehle.")
        greeting = self._begruessung(s.note_count())
        self.out(f"\n[bold cyan]{self.cfg.assistant_name}:[/bold cyan] {greeting}" if self.console else greeting)
        if self.speaker.available and self.cfg.speak_greeting:
            self.speaking = True
            self.speaker.say(greeting)
        return True

    def _begruessung(self, anzahl: int) -> str:
        """Begrüßung in der eingestellten Sprache - sie wird auch vorgelesen."""
        if self.cfg.greeting_text:
            return self.cfg.greeting_text.replace("{anzahl}", str(anzahl))
        anrede = self.cfg.user_title
        vorlagen = {
            "en": f"All systems ready, {anrede}. {anzahl} notes standing by.",
            "de": f"Systeme bereit, {anrede}. {anzahl} Notizen liegen an.",
            "fr": f"Systèmes prêts, {anrede}. {anzahl} notes disponibles.",
            "es": f"Sistemas listos, {anrede}. {anzahl} notas disponibles.",
            "it": f"Sistemi pronti, {anrede}. {anzahl} note disponibili.",
        }
        return vorlagen.get(self.cfg.sprach_kuerzel, vorlagen["de"])

    # -------------------------------------------------------------- Preflight
    def _preflight(self) -> bool:
        """Prüft Konfiguration, bevor irgendetwas knallt. False = Abbruch."""
        if not self.cfg.env_file:
            self.warn(
                "Es wurde keine .env gefunden – deshalb gelten die Standardwerte "
                "(Vault './obsidian', Modell 'llama3')."
            )
            self.info("Windows blendet Dateiendungen aus: aus '.env' wird schnell '.env.txt'.")
            self.info("Prüfen mit: dir /a  →  die Datei muss exakt '.env' heißen.")

        while not self.cfg.vault_ok:
            self.warn(f"Vault-Ordner existiert nicht: {self.cfg.vault_path}")
            try:
                answer = input(
                    "  Pfad zur Obsidian-Vault eingeben (Enter = abbrechen): "
                ).strip().strip('"').strip("'")
            except (KeyboardInterrupt, EOFError):
                return False
            if not answer:
                self.info("Abgebrochen. Trage OBSIDIAN_VAULT in die .env ein und starte neu.")
                return False
            if not self._set_vault(answer):
                continue
        return True

    def _set_vault(self, raw: str) -> bool:
        path = Path(raw.strip().strip('"').strip("'")).expanduser()
        if not path.is_dir():
            self.warn(f"'{path}' ist kein Ordner.")
            return False
        target = self.cfg.persist("OBSIDIAN_VAULT", str(path))
        reload_settings()
        self.agent.index.store = None
        self.info(f"Vault gesetzt: {path}")
        if target:
            self.info(f"In {target} gespeichert – beim nächsten Start automatisch aktiv.")
        else:
            self.warn("Konnte die .env nicht schreiben – bitte den Pfad manuell eintragen.")
        return True

    def _doctor(self) -> None:
        rows = list(self.cfg.diagnose())
        alive = self.agent.llm.is_alive()
        rows.append(("Ollama erreichbar", alive, self.cfg.host))
        if alive:
            models = self.agent.llm.available_models()
            for label, name in (("LLM-Modell", self.cfg.llm_model),
                                ("Embedding-Modell", self.cfg.embedding_model)):
                ok = name in models or name.split(":")[0] in {m.split(":")[0] for m in models}
                rows.append((label, ok, name if ok else f"'{name}' fehlt → ollama pull {name}"))
            caps = self.agent.detect_capabilities()
            rows.append((
                "Werkzeug-Unterstützung des Modells", self.agent.native_tools,
                ", ".join(caps) or "unbekannt" if self.agent.native_tools
                else "keine – Schreiben läuft über den Planer; besser: qwen2.5:14b",
            ))
            rows.append(("Schreibpfad", True,
                         f"Planer {'an' if self.cfg.planner_enabled else 'aus'}, "
                         f"Retries {self.cfg.write_retries}, "
                         f"Limit {self.cfg.max_files_per_run} Dateien/Lauf"))
        lines = [f"{'✅' if ok else '❌'} {title}: {note}" for title, ok, note in rows]
        text = "\n".join(lines)
        self.out(Panel(text, title="Diagnose", border_style="blue") if self.console else text)

    # ------------------------------------------------------------------- Sync
    def _sync(self, force: bool) -> None:
        try:
            if self.console:
                with self.console.status("[cyan]Synchronisiere Vault …", spinner="dots") as status:
                    report = self.agent.sync(force=force, progress=lambda m: status.update(f"[cyan]{m}"))
            else:
                report = self.agent.sync(force=force, progress=print)
        except FileNotFoundError as exc:
            self.warn(str(exc))
            self.info("Mit /vault <Pfad> korrigieren oder OBSIDIAN_VAULT in der .env prüfen.")
            return
        except ModuleNotFoundError as exc:
            self.warn(f"Paket fehlt: {exc.name}")
            self.info("Installieren mit: pip install -r requirements.txt")
            return
        except Exception as exc:  # z. B. Ollama aus, Modell fehlt
            self.warn(f"Indexierung fehlgeschlagen: {exc}")
            self.info("Prüfe mit /doktor, ob Ollama läuft und die Modelle vorhanden sind.")
            return
        self.info(report.summary())

    # ------------------------------------------------------------------ Loop
    def run(self) -> None:
        if self.boot() is False:
            return
        try:
            self._schleife()
        finally:
            # Der Kern muss herunter, egal wie die Sitzung endet - sonst
            # laufen Arbeiterthreads weiter, und die letzte Sitzung wird nicht
            # verdichtet. Ein 'finally' ist hier keine Vorsicht, sondern die
            # einzige Stelle, die JEDEN Ausgang erwischt: Strg-C, EOF, /ende
            # und eine durchgeschlagene Ausnahme.
            try:
                self.agent.kern_anhalten()
            except Exception:
                pass

    def _schleife(self) -> None:
        while True:
            try:
                raw = self._read_input()
            except (KeyboardInterrupt, EOFError):
                self.out("\nBis später, " + self.cfg.user_title + ".")
                self.speaker.stop()
                return
            if raw is None:
                continue
            if raw is self.LEERLAUF:
                try:
                    self._leerlauf_schritt()
                except KeyboardInterrupt:
                    self.warn("Abgebrochen.")
                except Exception as exc:
                    self._befehl_fehler("/weiter", exc)
                continue
            text = raw.strip()
            if not text:
                continue
            if text.startswith("/"):
                # Ein einzelner Befehl darf die Sitzung nicht beenden. /tempo
                # ist mit einem Traceback aus dem Chat geflogen, weil unter
                # Windows 11 `wmic` fehlt - und damit war das ganze Gespräch weg.
                try:
                    if self._command(text):
                        return
                except KeyboardInterrupt:
                    self.warn("Abgebrochen.")
                except Exception as exc:
                    self._befehl_fehler(text, exc)
                continue
            try:
                self._answer(text)
            except KeyboardInterrupt:
                self.warn("Abgebrochen.")
            except Exception as exc:
                self._befehl_fehler(text, exc)

    def _befehl_fehler(self, eingabe: str, exc: Exception) -> None:
        """Fehler zeigen, festhalten – aber weiterlaufen."""
        import traceback

        self.warn(f"'{eingabe}' ist fehlgeschlagen: {type(exc).__name__}: {exc}")
        spur = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        try:
            ziel = self.cfg.work_dir / "fehler.log"
            ziel.parent.mkdir(parents=True, exist_ok=True)
            with open(ziel, "a", encoding="utf-8") as fh:
                fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')}  {eingabe}\n{spur}")
            self.info(f"Einzelheiten in {ziel}")
        except OSError as schreibfehler:
            self.warn(f"Fehlerprotokoll nicht schreibbar: {schreibfehler}")
        try:
            # Auch das ist ein offener Punkt - er gehört in die Liste.
            self.agent.aufgaben.eintragen(
                "befehl", eingabe, f"Befehl schlug fehl: {type(exc).__name__}")
        except Exception:
            # Die Aufgabenliste ist Beiwerk; sie darf die Fehlermeldung nicht
            # ihrerseits mit einem Fehler überschreiben.
            pass

    def _read_input(self) -> Optional[str]:
        prompt = f"\n{self.cfg.user_title} › "
        if self.console:
            # Vor der Eingabeaufforderung nachmessen: Wurde das Fenster
            # verändert, während der Benutzer nachgedacht hat, stünde die
            # Aufforderung sonst in der alten Breite - genau daher kam das
            # abgeschnittene "Us" statt "User" am Zeilenanfang.
            self._fenster_pruefen()
            self.console.print(f"[bold green]{prompt.strip()}[/bold green] ", end="")
        else:
            print(prompt, end="", flush=True)

        # Eingefügter Text kommt zeilenweise an. Ohne Sammeln beantwortet der
        # Agent jede Zeile eines Anweisungsblatts einzeln - die erste also
        # ohne die Regeln, die in Zeile fünf stehen.
        if self.leser is not None and self.leser.aktiv:
            # Kommt eine Weile nichts, schaut Zeus in die Aufgabenliste. Das
            # Zeitlimit gilt nur, wenn der Leerlauf eingeschaltet ist -
            # sonst wird wie bisher unbegrenzt gewartet.
            raw = self.leser.block_lesen(self._leerlauf_zeit())
            if raw is None:                     # EOF: Strg+Z bzw. Strg+D
                return None
            if raw is Eingabeleser.LEERLAUF:
                return self.LEERLAUF
        else:
            try:
                raw = input()
            except EOFError:
                return None

        if raw.strip():
            if "\n" in raw:
                zeilen = raw.count("\n") + 1
                # Der Zeilenumbruch VOR der Meldung ist nicht Kosmetik: Die
                # Eingabeaufforderung wurde ohne Umbruch gedruckt, und wohin
                # der Cursor nach einem eingefügten Block zeigt, weiß das
                # Terminal allein. Ohne ihn stand da "User › ?11 Zeilen als
                # EINE Eingabe übernommen." - Aufforderung und Meldung in
                # einer Zeile verklebt.
                if self.console:
                    self.console.print()
                else:
                    print(flush=True)
                self.info(f"{zeilen} Zeilen als EINE Eingabe übernommen.")
            return raw
        # Leere Eingabe = Mikrofon
        if self.listener.available:
            return self._voice_input()
        return None

    def _voice_input(self) -> Optional[str]:
        self.speaker.stop()
        self.info("🎙  Ich höre … (einfach sprechen, Pause beendet die Aufnahme)")
        try:
            text = self.listener.listen()
        except Exception as exc:
            self.warn(f"Mikrofon-Problem: {exc}")
            return None
        if not text:
            self.info("Nichts verstanden.")
            return None
        self.out(f"[green]{self.cfg.user_title} (gesprochen):[/green] {text}" if self.console else f"> {text}")
        return text

    # --------------------------------------------------------------- Antworten
    def _answer(self, question: str) -> None:
        self.speaker.stop()
        buffer: List[str] = []
        aktionen: List[str] = []
        title = f"{self.cfg.assistant_name}"

        def beschreibe(record) -> str:
            symbol = "✔" if record.ergebnis.ok else "✖"
            farbe = "green" if record.ergebnis.ok else "red"
            return f"[{farbe}]{symbol}[/{farbe}] [bold]{record.werkzeug}[/bold] – {record.ergebnis.meldung}"

        if self.console and self.live_moeglich:
            # transient=True: Die Fortschrittsanzeige verschwindet am Ende wieder,
            # danach wird die fertige Antwort EINMAL sauber gedruckt.
            # vertical_overflow="crop": Wird der Text höher als das Fenster,
            # schneidet Rich ihn ab, statt über den Bildschirm hinauszuschreiben -
            # genau das erzeugte sonst die ineinandergeschobenen Zeilen.
            #
            # Echosperre: Solange hier neu gezeichnet wird, schreibt das
            # Terminal getippte Zeichen nicht mehr in die Ausgabe hinein.
            # Ohne sie entstand "jarvis_·ok, dann erstelle sietem/Code" -
            # Eingabe und Ausgabe in derselben Zeile.
            vorher = self.leser.wartet() if self.leser is not None else 0
            with Echosperre(), Live(console=self.console, refresh_per_second=6,
                                    transient=True, vertical_overflow="crop") as live:

                def render():
                    # Bei JEDEM Neuzeichnen nachmessen. Zieht der Benutzer das
                    # Fenster schmaler, während der Agent arbeitet, zeichnete
                    # Rich sonst weiter in der alten Breite - die Rahmen liefen
                    # über den Rand und schoben sich ineinander. Das war der
                    # sichtbare Fehler.
                    geaendert = self._fenster_pruefen()
                    breite, fensterhoehe = self._letzte_groesse
                    if geaendert:
                        # Die alte, zu breite Zeichnung steht noch da. Sie
                        # gehört nicht mehr uns, also wird sie weggeräumt,
                        # bevor die neue kommt.
                        try:
                            live.refresh()
                        except Exception:
                            pass

                    teile = []
                    if aktionen:
                        # Wenig Platz: weniger Werkzeugzeilen, sonst bleibt für
                        # den Text nichts übrig und beides wird abgeschnitten.
                        zeilen = 8 if fensterhoehe >= 30 else 3
                        teile.append(Panel("\n".join(aktionen[-zeilen:]), title="Werkzeuge",
                                           border_style="magenta", width=breite))
                    inhalt = "".join(buffer)
                    if inhalt:
                        # Nur das Ende zeigen - es ist eine Fortschrittsanzeige,
                        # nicht das Ergebnis.
                        hoehe = max(3, fensterhoehe - 12)
                        sichtbar = "\n".join(inhalt.splitlines()[-hoehe:])
                        teile.append(Panel(Text(sichtbar, overflow="fold"),
                                           title=f"{title} schreibt …",
                                           border_style="cyan", width=breite))
                    else:
                        teile.append(Panel(Text("arbeitet …", style="dim"), title=title,
                                           border_style="cyan", width=breite))
                    return Group(*teile)

                live.update(render())

                def on_token(token: str) -> None:
                    buffer.append(token)
                    live.update(render())

                def on_tool(record) -> None:
                    aktionen.append(beschreibe(record))
                    live.update(render())

                answer = self.agent.ask(question, stream_cb=on_token, on_tool=on_tool)

            # Endgültige Ausgabe, einmal und vollständig
            if aktionen:
                self.console.print(Panel("\n".join(aktionen), title="Werkzeuge",
                                         border_style="magenta"))
            self.console.print(Panel(Markdown(answer.text or "…"), title=title,
                                     border_style="cyan"))
            # Wurde währenddessen getippt? Die Zeichen sind in der Warteschlange
            # sicher, aber auf dem Bildschirm womöglich zerhackt. Also sagen,
            # dass etwas angekommen ist - dann weiß der Benutzer, dass er es
            # nicht noch einmal tippen muss.
            if self.leser is not None:
                dazu = self.leser.wartet() - vorher
                if dazu > 0:
                    self.info(f"Während der Antwort eingegeben: {dazu} Zeile(n) – "
                              f"wird als Nächstes bearbeitet.")
        elif self.console:
            # Schmales oder altes Terminal: schlicht mitschreiben, kein Neuzeichnen.
            self.console.print(f"[bold cyan]{title}:[/bold cyan]")

            def on_tool_schlicht(record) -> None:
                self.console.print("  " + beschreibe(record))

            answer = self.agent.ask(
                question,
                stream_cb=lambda t: self.console.print(t, end="", markup=False, highlight=False),
                on_tool=on_tool_schlicht,
            )
            self.console.print()
        else:
            def on_tool_plain(record) -> None:
                print(("[ok] " if record.ergebnis.ok else "[!!] ")
                      + f"{record.werkzeug}: {record.ergebnis.meldung}")

            answer = self.agent.ask(question, stream_cb=lambda t: sys.stdout.write(t),
                                    on_tool=on_tool_plain)
            print()

        self.last_answer = answer
        if answer.changed_files:
            self.info("Geändert: " + ", ".join(answer.changed_files))
        self._show_sources(answer)
        if self.speaking and answer.text:
            self.speaker.say(answer.text)

    def _show_sources(self, answer: Answer) -> None:
        if not answer.hits:
            return
        if not self.console:
            print("Quellen: " + ", ".join(answer.sources))
            return
        table = Table(show_header=True, header_style="dim", box=None, padding=(0, 2, 0, 0))
        table.add_column("Quelle", style="cyan")
        table.add_column("Ordner", style="magenta")
        table.add_column("Treffer", style="dim", justify="right")
        seen = set()
        for hit in answer.hits:
            rel = hit.metadata.get("rel_path", "")
            if rel in seen:
                continue
            seen.add(rel)
            if rel.startswith("__struktur__"):
                table.add_row("Vault-Struktur", "–", f"{hit.score:.2f}")
                continue
            table.add_row(
                hit.metadata.get("stem", "?"),
                hit.metadata.get("folder") or "Wurzel",
                f"{hit.score:.2f} · {hit.origin[:3]}",
            )
        self.console.print(table)

    # --------------------------------------------------------------- Befehle
    def _command(self, line: str) -> bool:
        parts = line[1:].split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("ende", "exit", "quit", "q"):
            self.speaker.stop()
            self._sitzung_abschliessen()
            self.out(f"Bis später, {self.cfg.user_title}.")
            return True

        if cmd in ("hilfe", "help", "h", "?"):
            self.out(Markdown(HELP) if self.console else HELP)

        elif cmd == "sync":
            self._sync(force=False)

        elif cmd in ("reindex", "neuaufbau"):
            self._sync(force=True)

        elif cmd in ("baum", "tree"):
            tree = self.agent.structure.tree()
            self.out(Panel(tree, title="Vault", border_style="magenta") if self.console else tree)

        elif cmd in ("struktur", "overview"):
            self.out(self.agent.structure.overview())

        elif cmd == "notiz":
            self._new_note(arg)

        elif cmd in ("merke", "speichern"):
            self._save_last(arg)

        elif cmd == "quellen":
            if self.last_answer:
                self._show_sources(self.last_answer)
            else:
                self.info("Noch keine Antwort vorhanden.")

        elif cmd in ("stimmen", "voices"):
            self._stimmen_zeigen()

        elif cmd in ("stimme", "voice") and arg:
            erstes = arg.split()[0].lower()
            if erstes in ("installieren", "install", "laden", "einrichten", "liste", "list"):
                rest = arg.split(maxsplit=1)[1].strip() if len(arg.split()) > 1 else ""
                self._stimme_installieren("liste" if erstes in ("liste", "list") else rest)
            else:
                gewaehlt = self.speaker.stimme_setzen(arg)
                if gewaehlt:
                    schluessel = ("PIPER_VOICE" if self.speaker.engine_name == "piper"
                                  else "TTS_VOICE")
                    if schluessel == "PIPER_VOICE":
                        self.cfg.piper_voice = gewaehlt
                    else:
                        self.cfg.tts_voice = gewaehlt
                    self.cfg.persist(schluessel, gewaehlt)
                    self.speaking = True
                    self.info(f"Stimme: {gewaehlt} (in der .env gespeichert)")
                    self.speaker.say(self._begruessung(self.agent.structure.note_count()))
                else:
                    self.warn(f"Keine Stimme zu '{arg}' gefunden – /stimmen zeigt die Liste.")

        elif cmd in ("sprache", "language"):
            if arg:
                self.cfg.language = arg.strip().capitalize()
                self.cfg.persist("LANGUAGE", self.cfg.language)
                # Die Stimme MUSS mitziehen. Bleibt sie stehen, liest eine
                # englische Stimme deutschen Text vor - das war der Fall,
                # der diesen Zweig überhaupt nötig gemacht hat.
                self._stimme_zur_sprache()
                self.speaker.say(self._begruessung(self.agent.structure.note_count()))
            else:
                self.info(f"Sprache: {self.cfg.language} ({self.cfg.sprach_kuerzel}) · "
                          f"Nutzung: /sprache English")

        elif cmd in ("sprich", "sprechen"):
            if not self.speaker.available:
                self.warn(f"Keine Sprachausgabe verfügbar: {self.speaker.status}")
            else:
                self.speaking = arg.lower() not in ("aus", "off", "0", "nein")
                self.info(f"Sprachausgabe {'an' if self.speaking else 'aus'}.")
                if not self.speaking:
                    self.speaker.stop()

        elif cmd in ("hoeren", "hören", "mic", "sprechen"):
            text = self._voice_input()
            if text:
                self._answer(text)

        elif cmd in ("rueckgaengig", "rückgängig", "undo"):
            ergebnis = self.agent.tools.rueckgaengig()
            (self.info if ergebnis.ok else self.warn)(ergebnis.meldung)

        elif cmd in ("verlauf", "aenderungen", "änderungen"):
            eintraege = self.agent.tools.letzte_aenderungen(12)
            if not eintraege:
                self.info("Noch keine Änderungen an der Vault.")
            else:
                text = "\n".join(
                    f"{e['zeit'][11:16]}  {e['aktion']:22} {e['beschreibung']}" for e in eintraege
                )
                self.out(Panel(text, title="Änderungsjournal", border_style="magenta")
                         if self.console else text)

        elif cmd in ("werkzeuge", "tools"):
            from .tools import tool_reference

            text = f"Modus: {self.agent.tool_mode}\n\n{tool_reference()}"
            self.out(Panel(text, title="Werkzeuge", border_style="magenta") if self.console else text)

        elif cmd in ("links", "linkcheck"):
            ergebnis = self.agent.tools.links_pruefen(reparieren=arg.lower() in ("reparieren", "fix"))
            self.info(ergebnis.meldung)
            for eintrag in ergebnis.daten.get("kaputt", [])[:10]:
                self.warn(f"[[{eintrag['link']}]] in {eintrag['notiz']}"
                          + (f" → Vorschlag: {eintrag['vorschlag']}" if eintrag["vorschlag"] else ""))

        elif cmd in ("codebasis", "selbstcheck"):
            if self.console:
                with self.console.status("[cyan]Übernehme Quellcode in die Vault …",
                                         spinner="dots"):
                    ergebnis = self.agent.tools.codebasis_importieren(arg or "*.py")
            else:
                ergebnis = self.agent.tools.codebasis_importieren(arg or "*.py")
            (self.info if ergebnis.ok else self.warn)(ergebnis.meldung)
            for eintrag in ergebnis.daten.get("uebersprungen", [])[:5]:
                self.warn(f"{eintrag['datei']}: {eintrag['grund']}")

        elif cmd in ("code", "quellcode"):
            ergebnis = self.agent.tools.code_dateien(arg or "*.py")
            if not ergebnis.ok:
                self.warn(ergebnis.meldung)
            else:
                zeilen = [f"{e['zeilen']:5} Zeilen  {e['datei']}"
                          for e in ergebnis.daten["dateien"]]
                gesamt = sum(e["zeilen"] for e in ergebnis.daten["dateien"])
                text = ("\n".join(zeilen) + f"\n\n{len(zeilen)} Dateien, {gesamt} Zeilen"
                        + "\nFreigegeben: " + ", ".join(ergebnis.daten["wurzeln"]))
                self.out(Panel(text, title="Quellcode", border_style="magenta")
                         if self.console else text)

        elif cmd in ("wartung", "maintenance"):
            self._wartung()

        elif cmd in ("tempo", "speed", "benchmark"):
            self._tempo()

        elif cmd in ("hardware", "gpu"):
            self._hardware()

        elif cmd in ("lernen", "kennzahlen"):
            k = self.agent.lernwerk.kennzahlen()
            text = (
                f"Züge in dieser Sitzung: {k['zuege']}  (geglückt: {k['geglueckt']}, "
                f"{k['quote']} %)\n"
                f"Abbrüche: {k['abbrueche']} · mittlere Dauer: {k['mittlere_dauer']} s\n"
                f"Gelernte Regeln: {k['lektionen']} · offene Wissenslücken: {k['offene_luecken']}\n"
                + self._sitzung_kennzahl()
                + ("Werkzeuge: " + ", ".join(f"{w} ×{n}" for w, n in k["werkzeuge"].items())
                   if k["werkzeuge"] else "Werkzeuge: –")
            )
            luecken = self.agent.lernwerk.offene_luecken()[:8]
            if luecken:
                text += "\n\nOffene Fragen ohne Quelle:\n" + "\n".join(f"  · {l}" for l in luecken)
            self.out(Panel(text, title="Lernstand", border_style="blue") if self.console else text)

        elif cmd in ("reflektieren", "reflexion"):
            if self.console:
                with self.console.status("[cyan]Sehe mir meine Arbeit an …", spinner="dots"):
                    bericht = self.agent.lernwerk.reflektieren()
            else:
                bericht = self.agent.lernwerk.reflektieren()
            self.out(Panel(Markdown(bericht), title="Reflexion", border_style="blue")
                     if self.console else bericht)

        elif cmd in ("lektionen", "regeln"):
            if arg.lower().startswith("konsolid"):
                self.info(self.agent.lernwerk.lektionen_konsolidieren())
            # Mit Bewährungsbilanz: Zahl der Einsätze und wie oft es danach klappte.
            text = self.agent.lernwerk.lektionsbilanz()
            self.out(Panel(Markdown(text), title="Gelernte Regeln (bewährt / eingesetzt)",
                           border_style="magenta") if self.console else text)

        elif cmd in ("prompt", "datei", "einlesen"):
            if not arg:
                self.warn("Nutzung: /prompt <Pfad zur Textdatei>")
            else:
                try:
                    text = datei_lesen(arg)
                except Exception as exc:
                    self.warn(f"Datei nicht lesbar: {exc}")
                else:
                    self.info(f"{text.count(chr(10)) + 1} Zeilen aus der Datei "
                              f"als eine Eingabe übernommen.")
                    self._answer(text)

        elif cmd in ("messen", "eval", "evaluation"):
            self._messen(arg)

        elif cmd in ("aufgaben", "todo", "liste"):
            self._aufgabenliste(arg)
        elif cmd in ("weiter", "leerlauf", "naechste", "nächste"):
            self._weiter(arg)
        elif cmd in ("offene", "offen", "punkte", "fehlschlaege", "fehlschläge"):
            self._aufgaben(arg)

        elif cmd in ("codereview", "codepruefung", "codeprüfung"):
            self._codereview(arg)
        elif cmd in ("architektur", "aufbau"):
            self._architektur()
        elif cmd in ("regeln", "grundregeln"):
            self._regeln(arg)
        elif cmd in ("server", "ollama"):
            self._server(arg)

        elif cmd in ("fertigkeiten", "skills"):
            fert = self.agent.fertigkeiten
            fert.laden()
            bericht = fert.pruefbericht()
            rahmen = "red" if fert.defekt else "magenta"
            titel = ("Eigene Fertigkeiten" if not fert.defekt
                     else f"Eigene Fertigkeiten – {len(fert.defekt)} gesperrt")
            if self.console:
                self.out(Panel(Markdown(bericht), title=titel, border_style=rahmen))
            else:
                self.out(bericht)

        elif cmd in ("kern", "system", "os"):
            self._kern(arg)

        elif cmd in ("ziel", "kuemmere", "kümmere", "auftrag"):
            self._ziel(arg)

        elif cmd in ("ziele", "vorhaben"):
            self._ziele(arg)

        elif cmd in ("arbeiten", "abarbeiten", "los"):
            self._arbeiten(arg)

        elif cmd in ("freigeben", "freigabe", "entscheiden"):
            self._freigeben(arg)

        elif cmd in ("termine", "zeitplan", "plan"):
            self._termine(arg)

        elif cmd in ("ablaeufe", "ablauf", "workflows"):
            self._ablaeufe(arg)

        elif cmd in ("rechte", "stufe"):
            self._rechte(arg)

        elif cmd in ("selbst", "selbstentwicklung"):
            self._selbst(arg)

        elif cmd in ("verhalten", "haltung"):
            verh = self.agent.verhalten
            verh.laden()
            if arg:
                # Mit Satz: nicht auflisten, sondern zeigen, was WIRKLICH
                # greifen würde. Eine Liste sagt nichts darüber, ob die
                # Auslöser taugen - und genau daran scheitern sie.
                treffer = verh.passend(arg)
                if treffer:
                    self.info(f"{len(treffer)} greift bei diesem Satz:")
                    for f in treffer:
                        self.out(f"   ✔ {f.name} – {f.beschreibung}")
                        self.out(f"     Auslöser: {', '.join(f.ausloeser[:8])}")
                else:
                    self.warn("Keine Verhaltensfertigkeit greift bei diesem Satz.")
            else:
                bericht = verh.pruefbericht()
                if self.console:
                    self.out(Panel(Markdown(bericht), title="Verhaltensfertigkeiten",
                                   border_style="red" if verh.defekt else "magenta"))
                else:
                    self.out(bericht)

        elif cmd in ("zeichne", "diagramm") and arg:
            # Semikolon als Zeilentrenner, damit sich ein Ablauf in EINE
            # Befehlszeile tippen lässt.
            ergebnis = self.agent.tools.ausfuehren(
                "struktur_zeichnen", {"beschreibung": arg.replace(";", "\n")})
            if ergebnis.ok:
                self.out(ergebnis.daten.get("zeichnung", ""))
            else:
                self.warn(ergebnis.meldung)
                self.out(str(ergebnis.daten.get("schreibweise", "")))

        elif cmd in ("fertigkeit", "skill") and arg:
            teile = arg.split()
            ergebnis = self.agent.fertigkeiten.ausfuehren(
                teile[0], {} if len(teile) < 2 else {"thema": " ".join(teile[1:])})
            (self.info if ergebnis.ok else self.warn)(ergebnis.meldung)
            for zeile in (ergebnis.daten.get("protokoll") or []):
                self.out(f"   {zeile}")

        elif cmd in ("trocken", "dryrun"):
            self.cfg.dry_run = arg.lower() not in ("aus", "off", "0", "nein")
            self.info(f"Trockenlauf {'an' if self.cfg.dry_run else 'aus'}.")

        elif cmd in ("doktor", "doctor", "diagnose"):
            self._doctor()

        elif cmd == "vault":
            if not arg:
                self.info(f"Aktuelle Vault: {self.cfg.vault_path}")
                self.info("Nutzung: /vault C:\\Users\\...\\Zeus")
            elif self._set_vault(arg):
                self._sync(force=True)

        elif cmd == "sitzung":
            self._sitzung(arg)

        elif cmd in ("sitzungen", "sitzungsnotizen"):
            self._sitzungen()

        elif cmd == "status":
            self._status()

        elif cmd in ("leeren", "clear"):
            self.agent.history.clear()
            self.info("Gesprächsverlauf gelöscht.")

        elif cmd in ("cls", "bildschirm"):
            bildschirm_leeren()

        elif cmd in ("ansicht", "live"):
            if arg:
                self.cfg.live_view = arg.lower()
            self.info(
                f"Live-Ansicht: {self.cfg.live_view} → "
                f"{'aktiv' if self.live_moeglich else 'aus (es wird mitgeschrieben)'}"
            )

        else:
            self.warn(f"Unbekannter Befehl '/{cmd}'. /hilfe zeigt alle.")
        return False

    def _new_note(self, topic: str) -> None:
        if not topic:
            self.info("Nutzung: /notiz <Thema>")
            return
        if self.console:
            with self.console.status(f"[cyan]Bündle Wissen zu '{topic}' …", spinner="dots"):
                content, hits = self.agent.summarize_for_note(topic)
        else:
            content, hits = self.agent.summarize_for_note(topic)

        if not content.strip():
            self.warn("Keine verwertbare Zusammenfassung – nichts gespeichert.")
            return
        self.out(Panel(Markdown(content), title="Entwurf", border_style="cyan") if self.console else content)
        path, decision = self.agent.write_note(topic, content, hits)
        self._report_saved(path, decision)

    def _save_last(self, topic: str) -> None:
        if not self.last_answer or not self.last_answer.text.strip():
            self.info("Es gibt noch keine Antwort zum Speichern.")
            return
        topic = topic or self._derive_topic(self.last_answer)
        path, decision = self.agent.write_note(topic, self.last_answer.text, self.last_answer.hits)
        self._report_saved(path, decision)

    def _derive_topic(self, answer: Answer) -> str:
        for hit in answer.hits:
            stem = hit.metadata.get("stem")
            if stem and not hit.metadata.get("rel_path", "").startswith("__struktur__"):
                return hit.metadata.get("title") or stem
        first = answer.text.strip().splitlines()[0]
        return first.lstrip("# ").strip()[:60] or "Notiz"

    def _report_saved(self, path, decision) -> None:
        rel = path.relative_to(self.agent.structure.vault_root)
        msg = (
            f"Abgelegt unter **{rel.as_posix()}**\n\n"
            f"Ordnerwahl: `{decision.folder or 'Vault-Wurzel'}` "
            f"({decision.reason}, Sicherheit {decision.confidence:.0%})"
            + ("\n\n*Ordner wurde neu angelegt.*" if decision.is_new else "")
        )
        self.out(Panel(Markdown(msg), title="Notiz gespeichert", border_style="green") if self.console else msg)
        if self.speaking:
            self.speaker.say(f"Notiz abgelegt in {decision.folder or 'Vault-Wurzel'}.")

    def _stimmen_zeigen(self) -> None:
        """Alle installierten Stimmen mit Sprache und Geschlecht."""
        from .piper_stimmen import uebersicht

        stimmen = self.speaker.stimmen()
        if not stimmen:
            self.warn(f"Keine Stimmen abrufbar ({self.speaker.status}).")
            self.info("Die Windows-Stimmen klingen blechern. Bessere, lokale Stimmen:\n"
                      "  python jarvis_run.py --stimme-einrichten en_GB-alan-medium\n"
                      "oder hier im Chat:  /stimme installieren")
            self.out(uebersicht(self.cfg))
            return
        beschriftung = {"m": "männlich", "w": "weiblich", "": "?"}
        zeilen = []
        for eintrag in stimmen:
            marke = "→" if eintrag["name"] == self.speaker.voice_name else " "
            zeile = (f"{marke} {eintrag['nummer']:2}. {eintrag['name'][:52]:52} "
                     f"{eintrag['sprache'] or '?':3} "
                     f"{beschriftung.get(eintrag['geschlecht'], '?')}")
            if eintrag.get("klang"):
                zeile += f"\n      {eintrag['klang']}"
            zeilen.append(zeile)
        text = "\n".join(zeilen) + "\n\nWechseln mit: /stimme 3   oder   /stimme alan"
        if self.speaker.engine_name != "piper":
            text += ("\nNatürlicher klingende Stimmen nachladen: /stimme installieren")
        self.out(Panel(text, title="Stimmen", border_style="magenta") if self.console else text)

    def _messen(self, arg: str) -> None:
        """Der Messstand: wird der Agent wirklich besser?"""
        from .evaluation import Messstand, aufgaben_lesen, vorlage_schreiben

        stand = Messstand(self.agent, self.cfg)
        aufgaben = aufgaben_lesen(stand.aufgabendatei)
        befehl = arg.strip().lower()

        if befehl in ("verlauf", "historie"):
            eintraege = stand.verlauf()
            if not eintraege:
                self.info("Noch keine Messung gelaufen.")
                return
            zeilen = ["| Datum | Aufgaben | mit | ohne |", "|---|---|---|---|"]
            for e in eintraege:
                quoten = e.get("quoten", {})
                zeilen.append(
                    f"| {e.get('datum', '')[:16]} | {e.get('aufgaben', '?')} "
                    f"| {quoten.get('mit', float('nan')) * 100:.0f} % "
                    f"| {quoten.get('ohne', float('nan')) * 100:.0f} % |")
            text = "\n".join(zeilen)
            self.out(Panel(Markdown(text), title="Messverlauf", border_style="cyan")
                     if self.console else text)
            return

        if not aufgaben:
            if vorlage_schreiben(stand.aufgabendatei):
                self.warn("Es gibt noch keine Prüfaufgaben – eine Vorlage liegt jetzt in "
                          f"{self.cfg.learning_folder}/Pruefaufgaben.md")
                self.info("Trage dort 20–30 echte Aufgaben aus deinem Alltag ein. "
                          "Ohne echte Aufgaben misst der Messstand nichts Verwertbares.")
            else:
                self.warn("Prüfaufgaben fehlen und die Vorlage ist nicht schreibbar.")
            return

        varianten = ["mit", "ohne"]
        grenze = 0
        runden = 1
        gewaehlt: List[str] = []
        for teil in arg.split():
            klein = teil.lower()
            if teil.isdigit():
                grenze = int(teil)
            # "3x" bzw. "x3": so oft läuft jede Variante. Der Weg gegen die
            # Streuung - eine einzelne Messung kann das Gegenteil der nächsten
            # sagen, und beide sähen gleich überzeugend aus.
            elif klein.endswith("x") and klein[:-1].isdigit():
                runden = max(1, min(10, int(klein[:-1])))
            elif klein.startswith("x") and klein[1:].isdigit():
                runden = max(1, min(10, int(klein[1:])))
            elif klein in ("mit", "ohne", "blind", "voll"):
                gewaehlt.append("mit" if klein == "voll" else klein)
            elif klein in ("alle", "all", "voll+"):
                gewaehlt = ["mit", "ohne", "blind"]
        if gewaehlt:
            varianten = gewaehlt

        anzahl = min(len(aufgaben), grenze) if grenze else len(aufgaben)
        self.info(f"{anzahl} Aufgaben × {len(varianten)} Varianten"
                  + (f" × {runden} Runden" if runden > 1 else "")
                  + f" = {anzahl * len(varianten) * runden} Agentenläufe. "
                  f"Das dauert. Strg+C bricht ab.")
        if varianten == ["mit", "ohne"] and runden == 1:
            self.info("Tipp: `/messen alle` nimmt die Kontrollgruppe `blind` dazu "
                      "(kein Abruf). `/messen 3x` läuft jede Variante dreimal – "
                      "dann steht neben dem Ergebnis, wie stark es schwankt.")
        laeufe = stand.messen(varianten=varianten, grenze=grenze,
                              fortschritt=self.info, runden=runden)
        if not laeufe:
            self.warn("Keine Aufgaben gelaufen.")
            return

        zeilen = [d.zusammenfassung() for d in laeufe.values()]
        if "mit" in laeufe and "ohne" in laeufe:
            zeilen.append("")
            zeilen.append(stand._urteil(laeufe["mit"], laeufe["ohne"]).replace("## Urteil\n\n", ""))
        text = "\n".join(zeilen)
        self.out(Panel(Markdown(text), title="Messung", border_style="green")
                 if self.console else text)

        stand.journal_schreiben(laeufe)
        import datetime as dt

        titel = f"Messung {dt.date.today().isoformat()}"
        ordner = (self.cfg.code_folder or "_System/Code").strip("/")
        try:
            inhalt = stand.bericht(laeufe)
            if self.agent.tools.notiz_exakt(titel) is not None:
                ergebnis = self.agent.tools.notiz_ueberschreiben(name=titel, inhalt=inhalt)
            else:
                ergebnis = self.agent.tools.notiz_erstellen(
                    titel=titel, inhalt=inhalt, ordner=f"{ordner}/Messungen")
            if ergebnis.ok:
                self.info(f"Vollständiger Bericht: {ergebnis.daten.get('pfad') or titel}")
        except Exception as exc:
            self.warn(f"Bericht nicht ablegbar: {exc} – die Zahlen oben stehen trotzdem.")

    def _aufgaben(self, arg: str) -> None:
        """Offene Punkte zeigen – und auf Wunsch durchgehen und abhaken."""
        liste = self.agent.aufgaben
        liste.laden()
        befehl = arg.strip().lower()

        if befehl in ("abarbeiten", "durchgehen", "pruefen", "prüfen", "check"):
            if not (liste.offene() or liste.festgefahren()):
                self.info("Nichts offen.")
                return
            self.info("Gehe die offenen Punkte durch – ohne Modell, rein prüfend.")
            ergebnis = liste.abarbeiten(fortschritt=self.info)
            zeilen = [f"{ergebnis['geprueft']} Punkte geprüft.", ""]
            if ergebnis["erledigt"]:
                zeilen.append("**Abgehakt**")
                zeilen += [f"- ✔ {z}" for z in ergebnis["erledigt"]]
                zeilen.append("")
            if ergebnis["weiterhin_offen"]:
                zeilen.append("**Weiterhin offen**")
                zeilen += [f"- ✖ {z}" for z in ergebnis["weiterhin_offen"]]
            text = "\n".join(zeilen)
            self.out(Panel(Markdown(text), title="Offene Punkte durchgegangen",
                           border_style="green") if self.console else text)
            return

        if befehl in ("leeren", "zuruecksetzen", "zurücksetzen"):
            erledigt = liste.erledigte()
            for aufgabe in erledigt:
                liste.alle.pop(aufgabe.schluessel, None)
            liste._sichern()
            liste.notiz_schreiben()
            self.info(f"{len(erledigt)} erledigte Punkte aus der Liste entfernt.")
            return

        bericht = liste.bericht()
        rahmen = "red" if liste.festgefahren() else ("yellow" if liste.offene() else "green")
        self.out(Panel(Markdown(bericht), title=f"Offene Punkte – {liste.zusammenfassung()}",
                       border_style=rahmen) if self.console else bericht)
        datei = liste.notiz_schreiben()
        if liste.alle:
            self.info(f"Als Notiz geführt: {self.cfg.learning_folder}/{datei}  ·  "
                      f"durchgehen mit /aufgaben abarbeiten")

    # -------------------------------------------------- Betriebssystem-Kern
    def _kern_holen(self):
        """Den Kern besorgen - und ehrlich sagen, wenn es keinen gibt."""
        kern = getattr(self.agent, "kern", None)
        if kern is None and getattr(self.cfg, "kern_aktiv", False):
            kern = self.agent.kern_starten()
        return kern

    def _kern(self, arg: str) -> None:
        kern = self._kern_holen()
        if kern is None:
            self.warn("Der Betriebssystem-Kern läuft nicht (KERN_AKTIV=false in der "
                      ".env). Zeus arbeitet dann wie bisher, nur ohne Module, "
                      "Auftragsplaner und Hintergrundarbeit.")
            return
        wahl = (arg or "").strip().lower()
        if wahl in ("auftraege", "aufträge", "jobs"):
            stand = kern.planer.zustand()
            self.info(f"{stand['wartend']} wartend, {stand['geplant']} geplant, "
                      f"{len(stand['laufend'])} laufend")
            for auftrag in stand["laufend"]:
                self.out(f"   ▶ {auftrag['id']:>4} {auftrag['art']:<14} "
                         f"{auftrag['beschreibung'][:44]} ({auftrag['dauer']}s)")
            for auftrag in stand["zuletzt"]:
                zeichen = {"fertig": "✔", "gescheitert": "✖",
                           "abgebrochen": "·"}.get(auftrag["zustand"], "?")
                self.out(f"   {zeichen} {auftrag['id']:>4} {auftrag['art']:<14} "
                         f"{auftrag['beschreibung'][:44]}"
                         + (f"  {auftrag['fehler'][:50]}" if auftrag["fehler"] else ""))
            return
        if wahl in ("ereignisse", "events", "bus"):
            for ereignis in kern.bus.verlauf(30):
                self.out(f"   {str(ereignis)[:110]}")
            return
        bericht = kern.bericht()
        if self.console:
            self.out(Panel(Markdown(bericht), title="Zeus – Betriebssystem",
                           border_style="cyan"))
        else:
            self.out(bericht)

    # ------------------------------------------------- Ziele und Auftraege
    def _stapel(self):
        """Der Aufgabenstapel - oder eine ehrliche Auskunft, warum nicht.

        Bewusst eine eigene Methode: Sechs Befehle brauchen ihn, und sechs
        eigene Erklaerungen, warum er fehlt, laufen nach einem halben Jahr
        auseinander.
        """
        kern = self._kern_holen()
        if kern is None:
            self.warn("Ohne Kern gibt es keine persistenten Auftraege "
                      "(KERN_AKTIV=false in der .env).")
            return None
        if getattr(kern, "laeufer", None) is None:
            modul = kern.modul("aufgaben")
            grund = getattr(modul, "ruht", "") or (
                modul.stoerung if modul else "nicht aufgebaut")
            self.warn(f"Der Aufgabenstapel arbeitet nicht: {grund}")
            if modul is not None and getattr(modul, "speicher", None) is None:
                return None
        return kern

    def _ziel(self, arg: str) -> None:
        """Ein Ziel aufgeben. Das ist die Tuer zur autonomen Arbeit."""
        auftrag = (arg or "").strip()
        if not auftrag:
            self.warn("Was soll erreicht werden? Beispiel: "
                      "/ziel Lege eine Uebersicht zu meinen offenen Projekten an")
            return
        kern = self._stapel()
        if kern is None:
            return
        try:
            ziel, wurzel = kern.auftrag_annehmen(auftrag, quelle="terminal")
        except RuntimeError as exc:
            self.warn(str(exc))
            return
        self.info(f"Ziel aufgenommen: {ziel.beschreibung[:90]}")
        self.out(f"   Kennung {ziel.id} · wird zerlegt und abgearbeitet.")
        if kern.stube is not None and not kern.stube.laeuft:
            # Ohne laufende Arbeiter passiert sonst NICHTS, und der Nutzer
            # wartet auf etwas, das nie kommt. Lieber gleich im Vordergrund.
            self.info("Keine Hintergrundarbeiter (Rechtestufe < 2) – "
                      "ich arbeite es jetzt im Vordergrund ab.")
            self._arbeiten("")
        else:
            self.out("   Stand ansehen mit /ziele, Ergebnis mit /aufgaben.")

    def _ziele(self, arg: str) -> None:
        kern = self._stapel()
        if kern is None or kern.speicher is None:
            return
        speicher = kern.speicher
        wahl = (arg or "").strip()
        if wahl and wahl not in ("alle", "all"):
            ziel = speicher.ziel(wahl)
            if ziel is None:
                self.warn(f"Kein Ziel mit der Kennung '{wahl}'.")
                return
            self.out(ziel.stichpunkt())
            if ziel.ergebnis:
                self.out(f"   Erwartet: {ziel.ergebnis[:100]}")
            for aufgabe in speicher.aufgaben_zum_ziel(ziel.id):
                self.out(f"     {aufgabe.stichpunkt()}")
                for eintrag in speicher.artefakte(aufgabe.id):
                    self.out(f"        → {eintrag['art']}: {eintrag['ort']}")
            return

        ziele = speicher.ziele(nur_offene=(wahl not in ("alle", "all")))
        if not ziele:
            self.info("Keine offenen Ziele. Eines aufgeben mit /ziel <was>.")
            return
        for ziel in ziele:
            kinder = speicher.aufgaben_zum_ziel(ziel.id)
            fertig = sum(1 for k in kinder if k.zustand == "fertig")
            self.out(f"{ziel.stichpunkt()}   [{ziel.id}]")
            if kinder:
                self.out(f"     {fertig}/{len(kinder)} Schritte erledigt")
        zahlen = speicher.kennzahlen()
        if zahlen.get("wartet_auf_mensch"):
            self.warn(f"{zahlen['wartet_auf_mensch']} Aufgabe(n) warten auf "
                      f"deine Entscheidung – /freigeben zeigt sie.")

    def _arbeiten(self, arg: str) -> None:
        kern = self._stapel()
        if kern is None or getattr(kern, "stube", None) is None:
            return
        if (arg or "").strip().lower() in ("runde", "schritt", "einmal"):
            zustand = kern.stube.runde("terminal")
            self.info("Nichts zu tun." if zustand is None
                      else f"Ein Durchgang: Aufgabe steht jetzt auf '{zustand}'.")
            return
        self.info("Arbeite die Warteschlange ab …")
        bericht = kern.arbeiten(max_runden=60, frist=180)
        self.out(f"   {bericht.get('runden', 0)} Durchgaenge, "
                 f"{bericht.get('fertig', 0)} fertig, "
                 f"{bericht.get('gescheitert', 0)} gescheitert, "
                 f"{bericht.get('neugeplant', 0)} neu geplant, "
                 f"{bericht.get('offen', 0)} noch offen")
        self.out(kern.stube.bericht())

    def _freigeben(self, arg: str) -> None:
        kern = self._stapel()
        if kern is None or kern.speicher is None:
            return
        speicher = kern.speicher
        offen = speicher.offene_freigaben()
        teile = (arg or "").strip().split(None, 2)
        if not teile:
            if not offen:
                self.info("Nichts wartet auf deine Entscheidung.")
                return
            self.info(f"{len(offen)} Entscheidung(en) offen:")
            for nummer, eintrag in enumerate(offen, start=1):
                self.out(f"   {nummer}. [{eintrag['risiko']}] {eintrag['titel'][:60]}")
                self.out(f"      {eintrag['frage'][:150]}")
            self.out("")
            self.out("Antworten mit:  /freigeben 1 ja       oder  /freigeben 1 nein")
            return

        if not teile[0].isdigit() or not (1 <= int(teile[0]) <= len(offen)):
            self.warn(f"'{teile[0]}' ist keine der {len(offen)} offenen Nummern.")
            return
        eintrag = offen[int(teile[0]) - 1]
        antwortwort = (teile[1] if len(teile) > 1 else "").strip().lower()
        if antwortwort in ("ja", "j", "yes", "ok", "freigeben"):
            erteilt = True
        elif antwortwort in ("nein", "n", "no", "ablehnen"):
            erteilt = False
        else:
            self.warn("Sag 'ja' oder 'nein'. Beispiel: /freigeben 1 nein zu riskant")
            return
        begruendung = teile[2] if len(teile) > 2 else ""
        speicher.freigabe_beantworten(eintrag["id"], erteilt, begruendung)
        self.info(("Freigegeben. " if erteilt else "Abgelehnt. ")
                  + f"'{eintrag['titel'][:60]}'")
        if kern.stube is not None and not kern.stube.laeuft:
            self._arbeiten("")

    def _termine(self, arg: str) -> None:
        kern = self._stapel()
        if kern is None or kern.speicher is None:
            return
        from .kern.zeitplan import ABSTAENDE, Zeitgeber

        uhr = getattr(kern, "zeitgeber", None) or Zeitgeber(
            kern.speicher, kern.laeufer, bus=kern.bus, rechte=kern.rechte)
        teile = (arg or "").strip().split(None, 2)
        if teile and teile[0].lower() in ("neu", "anlegen"):
            if len(teile) < 3:
                self.warn("So: /termine neu taeglich Lege die Tagesnotiz an\n"
                          f"Abstaende: {', '.join(sorted(ABSTAENDE))}")
                return
            try:
                eintrag = uhr.eintragen(teile[1], teile[2], abstand=teile[1])
            except ValueError as exc:
                self.warn(str(exc))
                return
            self.info(f"Termin '{eintrag['name']}' angelegt.")
            return
        if teile and teile[0].lower() in ("aus", "abschalten", "stop"):
            if len(teile) < 2 or not uhr.abschalten(teile[1]):
                self.warn("Welche Kennung? /termine zeigt sie.")
                return
            self.info("Termin abgeschaltet.")
            return
        self.out(uhr.uebersicht())

    def _ablaeufe(self, arg: str) -> None:
        kern = self._stapel()
        if kern is None or getattr(kern, "ablaufwerk", None) is None:
            self.warn("Kein Ablaufwerk aktiv.")
            return
        werk = kern.ablaufwerk
        teile = (arg or "").strip().split(None, 1)
        if teile and teile[0].lower() in ("start", "starte"):
            if len(teile) < 2:
                self.warn("Welcher Ablauf? /ablaeufe zeigt sie.")
                return
            try:
                lauf = werk.starten(teile[1].strip())
            except Exception as exc:
                self.warn(str(exc))
                return
            ergebnis = werk.durchlaufen(lauf["id"])
            self.info(f"Ablauf '{teile[1].strip()}': {ergebnis.get('zustand')}")
            for zeile in (ergebnis.get("protokoll") or [])[-10:]:
                self.out(f"   {zeile}")
            if ergebnis.get("fehler"):
                self.warn(ergebnis["fehler"][:200])
            return
        if teile and teile[0].lower() in ("zeigen", "zeig"):
            eintrag = werk.speicher.ablauf(teile[1].strip() if len(teile) > 1 else "")
            if not eintrag:
                self.warn("Den Ablauf gibt es nicht.")
                return
            self.out(f"{eintrag['name']}: {eintrag['beschreibung']}")
            for nummer, schritt in enumerate(eintrag["schritte"], start=1):
                self.out(f"   {nummer}. {schritt.get('art')}"
                         f" {schritt.get('werkzeug') or schritt.get('ziel') or ''}")
            return
        alle = werk.liste()
        if not alle:
            self.info("Keine Ablaeufe gespeichert.")
            return
        for eintrag in alle:
            marke = "▶" if eintrag["aktiv"] else "·"
            self.out(f"{marke} {eintrag['name']:<22} {len(eintrag['schritte'])} "
                     f"Schritte – {eintrag['beschreibung'][:60]}")
        laeufe = werk.speicher.ablauflaeufe(grenze=5)
        if laeufe:
            self.out("")
            self.out("Zuletzt gelaufen:")
            for lauf in laeufe:
                self.out(f"   {lauf['ablauf_name']:<22} {lauf['zustand']}"
                         f" (Schritt {lauf['zeiger']})")

    def _rechte(self, arg: str) -> None:
        kern = self._kern_holen()
        if kern is None:
            self.warn("Ohne Kern gibt es keine Rechteverwaltung (KERN_AKTIV=false).")
            return
        wahl = (arg or "").strip().lower()
        if wahl in ("antrag", "bewerbung"):
            self.out(kern.rechte.antrag_als_text())
            return
        if wahl.isdigit():
            # Das ist die EINZIGE Stelle, an der eine Rechtestufe steigt, und
            # sie wird von einem Menschen getippt. Zeus hat auf sie keinen
            # Zugriff - das ist der ganze Sinn der Trennung.
            alt = kern.rechte.stufe
            neu = kern.rechte.setzen(int(wahl), durch="nutzer")
            self.info(f"Rechtestufe {alt} → {neu}. Dauerhaft wird das erst mit "
                      f"RECHTE_STUFE={neu} in der .env – hier gilt es für diese "
                      f"Sitzung.")
            self.out(kern.rechte.uebersicht())
            return
        self.out(kern.rechte.uebersicht())
        antrag = kern.rechte.antrag()
        if antrag:
            self.out("")
            self.info("Es liegt ein Antrag auf die nächste Stufe vor – /rechte antrag")

    def _selbst(self, arg: str) -> None:
        kern = self._kern_holen()
        modul = kern.modul("selbstentwicklung") if kern else None
        if modul is None or getattr(modul, "quarantaene", None) is None:
            self.warn("Die Selbstentwicklung ist nicht bereit"
                      + (f": {modul.abgelehnt}" if modul and modul.abgelehnt else "."))
            return
        teile = (arg or "").strip().split()
        if teile and teile[0] in ("uebernehmen", "übernehmen", "freigeben") and len(teile) > 1:
            geglueckt, meldung = modul.quarantaene.uebernehmen(teile[1])
            (self.info if geglueckt else self.warn)(meldung)
            return
        if teile and teile[0] in ("verwerfen", "ablehnen") and len(teile) > 1:
            if modul.quarantaene.verwerfen(teile[1]):
                self.info(f"Vorschlag '{teile[1]}' verworfen.")
            else:
                self.warn(f"Vorschlag '{teile[1]}' ist nicht bekannt.")
            return
        if teile and teile[0] in ("zeigen", "diff") and len(teile) > 1:
            vorschlag = modul.quarantaene.holen(teile[1])
            if not vorschlag:
                self.warn(f"Vorschlag '{teile[1]}' ist nicht bekannt.")
                return
            self.out(vorschlag.diff or "(kein Unterschied aufgezeichnet)")
            if vorschlag.testausgabe:
                self.out("\nTestlauf:")
                self.out(vorschlag.testausgabe)
            return
        bericht = modul.quarantaene.bericht()
        if self.console:
            self.out(Panel(Markdown(bericht), title="Selbstentwicklung",
                           border_style="magenta"))
        else:
            self.out(bericht)

    # ---------------------------------------------------------- Regeln
    def _regeln(self, arg: str) -> None:
        """Was gerade als verbindliche Regel gilt - und die Startfassung anlegen."""
        from .regeln import laden, vorlage_schreiben

        befehl = (arg or "").strip().lower()
        if befehl in ("anlegen", "vorlage", "neu", "init"):
            probleme: List[str] = []
            geschrieben = vorlage_schreiben(self.cfg, fehler=probleme)
            for eintrag in probleme:
                self.warn(f"Nicht geschrieben: {eintrag}")
            if geschrieben:
                self.info(f"{len(geschrieben)} Regeldatei(en) angelegt in "
                          f"{self.cfg.regel_pfad}")
                for pfad in geschrieben:
                    self.info(f"  {pfad.name}")
                self.info("Bearbeite sie in Obsidian. Sie gelten ab der nächsten Frage.")
            else:
                self.warn("Nichts angelegt – die Dateien gibt es schon "
                          "(`/regeln neu!` überschreibt) oder der Ordner ist "
                          "nicht schreibbar.")
            return
        if befehl in ("neu!", "überschreiben", "ueberschreiben"):
            probleme = []
            geschrieben = vorlage_schreiben(self.cfg, ueberschreiben=True,
                                            fehler=probleme)
            self.info(f"{len(geschrieben)} Regeldatei(en) überschrieben.")
            for eintrag in probleme:
                self.warn(f"Nicht geschrieben: {eintrag}")
            return

        werk = laden(self.cfg)
        zeilen = [werk.uebersicht()]
        if werk.vorhanden and befehl in ("voll", "alle", "text"):
            zeilen += ["", "---", "", werk.text]
        elif werk.vorhanden:
            zeilen += ["", "*`/regeln voll` zeigt den Wortlaut, der im Prompt steht.*"]
        text = "\n".join(zeilen)
        self.out(Panel(Markdown(text), title="Grundregeln", border_style="cyan")
                 if self.console else text)

    # --------------------------------------------------------- Rückfrage
    def _aenderung_bestaetigen(self, vorschau) -> bool:
        """Den Unterschied zeigen und fragen. True = schreiben.

        Wird nur aufgerufen, wenn CONFIRM_WRITES=true ist. Bis hierher ist
        NICHTS geschrieben - die Vorschau entsteht aus dem Text im Speicher.
        """
        text = vorschau.bericht()
        self.out(Panel(Markdown(text), title="Änderung – bestätigen?",
                       border_style="yellow") if self.console else text)
        antwort = ""
        try:
            if self.console:
                self.console.print("[bold yellow]Schreiben? [j/N][/bold yellow] ",
                                   end="")
            else:
                print("Schreiben? [j/N] ", end="", flush=True)
            antwort = (input() or "").strip().lower()
        except (EOFError, KeyboardInterrupt):
            # Abbruch ist ein Nein. Bei einer Rückfrage vor dem Schreiben ist
            # das die einzig vertretbare Auslegung.
            self.out("")
            return False
        return antwort in ("j", "ja", "y", "yes", "ok")

    # ------------------------------------------------------- Aufgabenliste
    LEERLAUF = LEERLAUF_MARKE

    def _leerlauf_zeit(self) -> Optional[float]:
        """Nach wie vielen Sekunden ohne Eingabe in die Aufgabenliste gesehen
        wird. None = gar nicht (Vorgabe)."""
        sekunden = int(getattr(self.cfg, "leerlauf_sekunden", 0) or 0)
        return float(sekunden) if sekunden > 0 else None

    def _leerlauf_schritt(self) -> None:
        """Ein Leerlaufdurchgang: nächste Aufgabe holen und VORSCHLAGEN."""
        self.out("")
        self.info("Nichts eingegeben – sehe in die Aufgabenliste …")
        vorschlag = self.leerlauf.vorbereiten(melden=self.info)
        rahmen = "cyan" if vorschlag.brauchbar else "yellow"
        titel = ("Vorschlag aus der Aufgabenliste" if vorschlag.brauchbar
                 else "Leerlauf")
        text = vorschlag.bericht()
        self.out(Panel(Markdown(text), title=titel, border_style=rahmen)
                 if self.console else text)
        if not vorschlag.brauchbar:
            # Sonst schlägt derselbe Punkt im Sekundentakt wieder auf.
            self.info("Leerlauf ausgesetzt – `/leerlauf an` schaltet ihn "
                      "wieder ein, `/weiter` fragt sofort erneut.")
            self.cfg.leerlauf_sekunden = 0

    def _weiter(self, arg: str) -> None:
        """`/weiter` schlägt die nächste Aufgabe vor, `/weiter ja` führt aus."""
        befehl = (arg or "").strip().lower()

        if befehl in ("an", "ein", "on"):
            sekunden = int(getattr(self.cfg, "leerlauf_sekunden", 0) or 0) or 120
            self.cfg.leerlauf_sekunden = sekunden
            self.info(f"Leerlauf an: nach {sekunden}s ohne Eingabe sehe ich in "
                      f"die Aufgabenliste. Vorgeschlagen wird, nicht ausgeführt.")
            return
        if befehl in ("aus", "off", "nein"):
            self.cfg.leerlauf_sekunden = 0
            self.info("Leerlauf aus.")
            return

        if befehl in ("ja", "los", "ausführen", "ausfuehren", "mach"):
            if self.leerlauf.letzter is None or not self.leerlauf.letzter.brauchbar:
                self.warn("Kein vorbereiteter Vorschlag. Erst `/weiter`.")
                return
            aktionen: List[str] = []

            def melden(art: str, text: str) -> None:
                aktionen.append(f"{'✔' if art != 'fehler' else '✖'} {art} – {text}")
                self.info(text)

            ergebnis, meldung = self.leerlauf.ausfuehren(on_event=melden)
            if ergebnis is None:
                self.warn(meldung)
                return
            bericht = ergebnis.bericht(self.cfg.assistant_name, self.cfg.user_title)
            self.out(Panel(Markdown(bericht), title="Ausgeführt",
                           border_style="green") if self.console else bericht)
            self.info(meldung)
            return

        self._leerlauf_schritt()

    def _aufgabenliste(self, arg: str) -> None:
        """Deine Aufgabenliste – als Stichpunkte, mit Hinweis auf Ungenaues."""
        buch = self.leerlauf.buch
        befehl = (arg or "").strip()
        klein = befehl.lower()

        if klein.startswith(("neu", "add", "+")):
            titel = befehl.split(maxsplit=1)[1].strip() if " " in befehl else ""
            if not titel:
                self.warn('Titel fehlt:  /aufgaben neu Idle-Schleife ergänzen')
                return
            ok, meldung = buch.anhaengen(titel)
            (self.info if ok else self.warn)(meldung)
            if ok:
                self.info(f"Ausfüllen in Obsidian: {buch.pfad}")
            return

        if klein in ("weiter", "naechste", "nächste"):
            self._weiter("")
            return

        buch.laden()
        if not buch.existiert():
            if buch.vorlage_schreiben():
                self.info(f"Aufgabenliste angelegt: {buch.pfad}")
                self.info("Sie erklärt das Format und enthält ein Beispiel.")
                buch.laden()
            else:
                self.warn(f"Aufgabenliste nicht schreibbar: {buch.pfad}")
                return

        text = buch.stichpunkte(alle=klein in ("alle", "all", "voll"))
        self.out(Panel(Markdown(text),
                       title=f"Aufgabenliste – {buch.zusammenfassung()}",
                       border_style="cyan") if self.console else text)
        naechste = buch.naechste(nur_scharfe=True)
        if naechste is not None:
            self.info(f"Als nächstes: „{naechste.titel}“  ·  `/weiter` "
                      f"bereitet sie vor")

    def _server(self, arg: str) -> None:
        """Welcher Ollama-Server gilt gerade - und Umschalten von Hand."""
        llm = self.agent.llm
        befehl = (arg or "").strip().lower()

        if befehl in ("ausweich", "alternativ", "ersatz"):
            if llm.ausweichen("Von Hand umgeschaltet"):
                self.info(llm.umschaltgrund)
            else:
                self.warn(llm.umschaltgrund or
                          "Kein Ausweichserver eingetragen oder er antwortet nicht.")
        elif befehl in ("haupt", "zurueck", "zurück", "primaer", "primär"):
            if llm.host_erreichbar(llm.haupt_host):
                llm._zurueck_auf_haupt()
                self.info(llm.umschaltgrund)
            else:
                self.warn(f"{llm.haupt_host} antwortet nicht – bleibe auf {self.cfg.host}.")
        elif befehl in ("pruefen", "prüfen", "test", ""):
            if befehl:
                llm.verbindung_pruefen(melden=self.info)
        else:
            self.warn(f"Unbekannt: '{arg}'. Möglich: /server, /server ausweich, "
                      f"/server haupt, /server pruefen")
            return

        text = "\n".join(llm.serverbericht())
        self.out(Panel(text, title="Ollama-Server", border_style="blue")
                 if self.console else text)

    def _architektur(self) -> None:
        """Nur vermessen, ohne Defektsuche - Sekunden statt Minuten.

        Der Unterschied zu /codereview: Dort geht es um einzelne Zeilen, hier
        um Größe, Verzweigungen, Kopplung, Testabdeckung und darum, welche
        Aufgabenart der Agent gar nicht bedienen kann.
        """
        from .architektur import architektur_pruefen, terminalfassung
        from .codereview import CodePruefer, ist_testdatei

        pruefer = CodePruefer(self.agent, self.cfg)
        alle = pruefer.quelldateien()
        quellen = [(p, a) for p, a in alle if not ist_testdatei(a)]
        tests = [(p, a) for p, a in alle if ist_testdatei(a)]
        if not quellen:
            self.warn("Keine Quelldateien gefunden – CODE_ROOTS prüfen.")
            return
        self.info(f"{len(quellen)} Module, {len(tests)} Testdateien – vermesse …")
        try:
            bericht = architektur_pruefen(quellen, tests, self.cfg)
        except Exception as exc:
            self.warn(f"Architekturprüfung fehlgeschlagen: {exc}")
            return

        text = "\n".join(["```"] + terminalfassung(bericht) + ["```"])
        self.out(Panel(Markdown(text), title="Aufbau und Fähigkeiten",
                       border_style="cyan") if self.console else text)

        import datetime as dt

        from .architektur import als_markdown

        titel = f"Aufbau und Fähigkeiten {dt.date.today().isoformat()}"
        ordner = (self.cfg.code_folder or "_System/Code").strip("/")
        inhalt = (f"---\ntitel: \"{titel}\"\ntags:\n  - code\n  - architektur\n"
                  f"  - selbstpruefung\ndatum: {dt.date.today().isoformat()}\n---\n\n"
                  f"# {titel}\n\n" + als_markdown(bericht))
        try:
            self.agent.index.refresh_structure()
            if self.agent.tools.notiz_exakt(titel) is not None:
                ergebnis = self.agent.tools.notiz_ueberschreiben(name=titel, inhalt=inhalt)
            else:
                ergebnis = self.agent.tools.notiz_erstellen(
                    titel=titel, inhalt=inhalt, ordner=f"{ordner}/Pruefungen")
        except Exception as exc:
            self.warn(f"Bericht nicht ablegbar: {exc} – die Messwerte oben stehen trotzdem.")
            return
        if ergebnis.ok:
            self.info(f"Vollständiger Bericht: {ergebnis.daten.get('pfad') or titel}")
        else:
            self.warn(f"Bericht nicht ablegbar: {ergebnis.meldung}")

    def _codereview(self, arg: str) -> None:
        """Eigenen Code prüfen. Ohne Argument statisch (Sekunden), 'tief' fragt das Modell."""
        from .codereview import CodePruefer

        tief = arg.strip().lower().startswith(("tief", "deep", "modell"))
        teile = arg.split()
        budget = 6
        for teil in teile:
            if teil.isdigit():
                budget = max(1, min(30, int(teil)))

        pruefer = CodePruefer(self.agent, self.cfg)
        if tief:
            self.info(f"Tiefe Prüfung: {budget} Abschnitte gehen einzeln ans Modell. "
                      f"Bei langsamer Hardware dauert das – Strg+C bricht ab.")
        bericht = pruefer.pruefen(mit_modell=tief, abschnitte=budget,
                                  fortschritt=self.info)

        zeilen = ["```", bericht.kurzfassung(), ""] + bericht.terminalfassung() + ["```"]
        if bericht.verworfen:
            zeilen.append("")
            zeilen.append(f"**{len(bericht.verworfen)} Modellbefunde verworfen**, z. B.:")
            for eintrag in bericht.verworfen[:3]:
                zeilen.append(f"- {eintrag[:150]}")

        text = "\n".join(zeilen)
        self.out(Panel(Markdown(text), title="Code-Prüfung", border_style="cyan")
                 if self.console else text)

        import datetime as dt

        titel = f"Code-Prüfung {dt.date.today().isoformat()}"
        ordner = (self.cfg.code_folder or "_System/Code").strip("/")
        try:
            self.agent.index.refresh_structure()
            inhalt = bericht.als_markdown(titel)
            if self.agent.tools.notiz_exakt(titel) is not None:
                # Zweiter Lauf am selben Tag ersetzt den Bericht, statt
                # daneben einen zweiten anzulegen.
                ergebnis = self.agent.tools.notiz_ueberschreiben(name=titel, inhalt=inhalt)
            else:
                ergebnis = self.agent.tools.notiz_erstellen(
                    titel=titel, inhalt=inhalt, ordner=f"{ordner}/Pruefungen")
        except Exception as exc:
            self.warn(f"Bericht nicht ablegbar: {exc} – die Befunde oben stehen trotzdem.")
            return
        if ergebnis.ok:
            self.info(f"Vollständiger Bericht: {ergebnis.daten.get('pfad') or titel}")
        else:
            self.warn(f"Bericht nicht ablegbar: {ergebnis.meldung}")

    def _stimme_zur_sprache(self) -> None:
        """Nach einem Sprachwechsel die passende Stimme aktivieren.

        Reihenfolge: passende piper-Stimme, die schon da ist → sonst Angebot,
        sie zu laden → sonst die Systemstimme umstellen (pyttsx3).
        """
        from .piper_stimmen import (
            empfehlung_fuer, modelle_auflisten, sprache_von,
        )

        kuerzel = self.cfg.sprach_kuerzel
        empfohlen = empfehlung_fuer(kuerzel, self.cfg.tts_gender)

        if self.speaker.engine_name == "piper":
            passend = [p for p in modelle_auflisten(self.cfg)
                       if sprache_von(p.stem) == kuerzel]
            if passend:
                bevorzugt = next((p for p in passend
                                  if p.stem.lower() == empfohlen.lower()), passend[0])
                gewaehlt = self.speaker.stimme_setzen(bevorzugt.stem)
                if gewaehlt:
                    self.cfg.piper_voice = gewaehlt
                    self.cfg.persist("PIPER_VOICE", gewaehlt)
                    self.info(f"Sprache: {self.cfg.language} · Stimme: {gewaehlt}")
                    return
            self.warn(f"Sprache: {self.cfg.language} – aber es ist keine passende "
                      f"Stimme installiert. Die aktuelle spricht "
                      f"{sprache_von(self.speaker.voice_name) or '?'}.")
            self.info(f"Nachladen mit:  /stimme installieren {empfohlen}")
            return

        neue = self.speaker._stimme_waehlen()
        self.info(f"Sprache: {self.cfg.language}"
                  + (f" · Stimme angepasst: {neue}" if neue else ""))
        if not neue:
            self.info(f"Natürlicher klingende Stimme für {self.cfg.language}: "
                      f"/stimme installieren {empfohlen}")

    def _stimme_installieren(self, kennung: str) -> None:
        """Lädt piper und eine Stimme nach - ohne die bisherige Engine zu verlieren."""
        from .piper_stimmen import STANDARD_STIMME, einrichten, uebersicht

        kennung = (kennung or "").strip()
        if kennung in ("liste", "list", "?"):
            self.out(uebersicht(self.cfg))
            return
        kennung = kennung or self.cfg.piper_voice or STANDARD_STIMME
        self.info(f"Richte piper mit '{kennung}' ein – das dauert beim ersten Mal ein paar Minuten.")
        ok, meldung = einrichten(kennung, self.cfg, protokoll=self.info)
        if not ok:
            self.warn(meldung)
            return
        self.cfg.tts_engine = "piper"
        self.cfg.piper_voice = kennung
        self.cfg.persist("TTS_ENGINE", "piper")
        self.cfg.persist("PIPER_VOICE", kennung)
        self.cfg.persist("PIPER_DATA_DIR", str(self.cfg.piper_data_path))
        self.info(meldung)

        # Sofort umschalten, ohne Neustart
        from .voice import Speaker

        neuer = Speaker(self.cfg)
        if neuer.available:
            try:
                self.speaker.stop()
            except Exception:
                pass
            self.speaker = neuer
            self.speaking = True
            self.info(f"Aktive Stimme: {self.speaker.status}")
            self.speaker.say(self._begruessung(self.agent.structure.note_count()))
        else:
            self.warn(f"Stimme geladen, aber nicht nutzbar: {neuer.status} "
                      f"– bitte einmal neu starten.")

    def _tempo(self) -> None:
        """Misst das Tempo UND sagt, woran es liegt - statt nur zu raten."""
        from .hardware import analysieren

        from .hardware import ist_entfernt

        wo = ("dem Server " + self.cfg.host) if ist_entfernt(self.cfg) else "diesem Rechner"
        hinweis = (f"[cyan]Messe {self.cfg.llm_model} auf {wo} … "
                   f"(erst laden, dann messen)")
        if self.console:
            with self.console.status(hinweis, spinner="dots"):
                messung = self.agent.llm.tempo_messen()
        else:
            print(f"Messe {self.cfg.llm_model} auf {wo} …")
            messung = self.agent.llm.tempo_messen()

        if messung.get("fehler"):
            self.warn(f"Messung fehlgeschlagen: {messung['fehler']}")
            return
        tps = messung.get("token_pro_s")
        if not tps:
            self.warn("Messung lieferte keinen Wert.")
            return

        ziel = self.cfg.note_target_words
        # 1,4 Token je Wort ist für deutschen Fließtext die brauchbarere
        # Faustregel als 1,0 - deutsche Wörter zerfallen häufiger in mehrere
        # Token als englische.
        sekunden = int(ziel * 1.4 / max(tps, 0.01))
        zeilen = [
            f"Server:   {messung.get('host')}",
            f"Modell:   {messung.get('modell')}",
            "",
            f"Ausgabe:  {tps} Token/s   ({messung.get('quelle')}, "
            f"{messung.get('token')} Token gemessen)",
        ]
        if messung.get("prompt_pro_s"):
            zeilen.append(
                f"Einlesen: {messung['prompt_pro_s']} Token/s beim Verarbeiten des Prompts")
        if messung.get("aufwaermzeit") is not None:
            zeilen.append(
                f"Laden:    {messung['aufwaermzeit']} s, bis das Modell bereit war "
                f"(zählt NICHT ins Tempo)")
        zeilen += [
            "",
            f"Eine Notiz mit {ziel} Wörtern dauert damit rund "
            f"{sekunden // 60} min {sekunden % 60} s." if sekunden >= 60 else
            f"Eine Notiz mit {ziel} Wörtern dauert damit rund {sekunden} s.",
        ]
        if self.cfg.fast_model:
            zeilen.append(f"Routineaufgaben laufen auf {self.cfg.fast_model} – "
                          f"die zählen hier nicht mit.")
        if messung.get("quelle", "").startswith("geschätzt"):
            zeilen += ["",
                       "Achtung: Der Server meldet keine Zählwerte (ältere Ollama-Fassung). "
                       "Der Wert ist aus der Zeichenzahl geschätzt und kann um ein "
                       "Drittel danebenliegen."]

        text = "\n".join(zeilen) + "\n\n" + analysieren(self.cfg, tempo=tps).bericht()
        self.out(Panel(text, title="Tempo und Ursache", border_style="blue")
                 if self.console else text)

    def _hardware(self) -> None:
        from .hardware import analysieren

        bericht = analysieren(self.cfg).bericht()
        self.out(Panel(bericht, title="Hardware", border_style="blue") if self.console else bericht)

    def _wartung(self) -> None:
        if self.console:
            with self.console.status("[cyan]Wartungslauf …", spinner="dots"):
                ergebnis = self.agent.tools.vault_wartung(reparieren=True)
        else:
            ergebnis = self.agent.tools.vault_wartung(reparieren=True)
        d = ergebnis.daten
        zeilen = [
            d.get("index", ""),
            f"Reparierte Links: {len(d.get('links_repariert', []))}",
            f"Weiter offen: {len(d.get('links_offen', []))}",
            f"Verwaiste Notizen (ohne jede Verknüpfung): {len(d.get('verwaiste_notizen', []))}",
            f"Mögliche Dubletten: {len(d.get('moegliche_dubletten', []))}",
        ]
        for verwaist in d.get("verwaiste_notizen", [])[:10]:
            zeilen.append(f"  · {verwaist}")
        for dub in d.get("moegliche_dubletten", [])[:5]:
            zeilen.append(f"  ≈ {dub['a']}  ↔  {dub['b']}")
        text = "\n".join(z for z in zeilen if z)
        self.out(Panel(text, title="Wartung", border_style="blue") if self.console else text)

    # ------------------------------------------------------ Sitzungsgedächtnis
    def _sitzung_abschliessen(self) -> None:
        """Beim Beenden verdichten - still, aber nicht heimlich."""
        werk = getattr(self.agent, "sitzung", None)
        if werk is None or not werk.aktiv or not werk.offene_zuege:
            return
        if self.console:
            with self.console.status("[cyan]Sitzung wird gemerkt …", spinner="dots"):
                pfad = self.agent.sitzung_abschliessen()
        else:
            pfad = self.agent.sitzung_abschliessen()
        if pfad:
            self.info(f"Sitzung gemerkt: {pfad.name}")
        elif werk.letzter_fehler:
            self.warn(werk.letzter_fehler)

    def _sitzung(self, arg: str) -> None:
        werk = getattr(self.agent, "sitzung", None)
        if werk is None or not werk.aktiv:
            self.info("Sitzungsgedächtnis ist aus (SESSION_MEMORY=false in der .env).")
            return

        if arg.strip().lower() in ("merken", "jetzt", "speichern"):
            if not werk.offene_zuege:
                self.info("Seit der letzten Verdichtung gab es keine neuen Züge.")
                return
            if self.console:
                with self.console.status("[cyan]Verdichte die Sitzung …", spinner="dots"):
                    pfad = werk.verdichten("auf Zuruf")
            else:
                pfad = werk.verdichten("auf Zuruf")
            if pfad:
                self.info(f"Gemerkt in {self.cfg.session_folder}/{pfad.name}")
            elif werk.letzter_fehler:
                self.warn(werk.letzter_fehler)
            else:
                self.info("Nichts Dauerhaftes dabei – nichts geschrieben.")
            return

        heute = werk.lesen()
        zeilen = [f"Ordner: `{self.cfg.session_folder}`  ·  "
                  f"{werk.offene_zuege} Züge seit der letzten Verdichtung "
                  f"(verdichtet wird alle {self.cfg.session_summary_every})"]
        if werk.zuletzt_verdichtet:
            zeilen.append(f"Zuletzt verdichtet: {werk.zuletzt_verdichtet}")
        if werk.letzter_fehler:
            zeilen.append(f"⚠ {werk.letzter_fehler}")
        zeilen.append("")

        for titel, tag in (("Heute", heute), ("Zuletzt", werk.juengste())):
            if tag is None or not tag.alle() or (titel == "Zuletzt"
                                                 and tag.tag == heute.tag):
                continue
            zeilen.append(f"**{titel} – {tag.tag}**")
            for eintrag in tag.alle()[:14]:
                zeichen = "☐ " if eintrag.art == "offen" and not eintrag.erledigt else (
                    "☑ " if eintrag.art == "offen" else "· ")
                zeilen.append(f"  {zeichen}{eintrag.anzeige()}")
            zeilen.append("")
        if len(zeilen) <= 2:
            zeilen.append("Noch keine Sitzungsnotiz. `/sitzung merken` legt eine an.")
        text = "\n".join(zeilen).rstrip()
        self.out(Panel(Markdown(text), title="Sitzung", border_style="cyan")
                 if self.console else text)

    def _sitzungen(self) -> None:
        werk = getattr(self.agent, "sitzung", None)
        if werk is None or not werk.aktiv:
            self.info("Sitzungsgedächtnis ist aus (SESSION_MEMORY=false in der .env).")
            return
        notizen = werk.notizen()
        if not notizen:
            self.info(f"Noch keine Sitzungsnotizen in `{self.cfg.session_folder}`.")
            return
        zeilen = ["| Tag | bestätigt | vorläufig | verworfen | offen |",
                  "|---|---:|---:|---:|---:|"]
        for pfad in notizen[:30]:
            z = werk.lesen_pfad(pfad).zahlen()
            zeilen.append(f"| {pfad.stem} | {z.get('bestaetigt', 0)} | "
                          f"{z.get('vorlaeufig', 0)} | {z.get('verworfen', 0)} | "
                          f"{z.get('offen', 0)} |")
        text = "\n".join(zeilen)
        self.out(Panel(Markdown(text), title=f"Sitzungen ({len(notizen)})",
                       border_style="cyan") if self.console else text)

    def _sitzung_kennzahl(self) -> str:
        """Eine Zeile Sitzungsgedächtnis für /lernen."""
        werk = getattr(self.agent, "sitzung", None)
        if werk is None or not werk.aktiv:
            return "Sitzungsfakten: – (Sitzungsgedächtnis aus)\n"
        k = werk.kennzahlen()
        return (f"Sitzungsfakten: {k.get('bestaetigt', 0)} bestätigt, "
                f"{k.get('vorlaeufig', 0)} vorläufig, {k.get('verworfen', 0)} "
                f"verworfen · {k.get('offen', 0)} offene Punkte "
                f"aus {k.get('tage', 0)} Tagen\n")

    def _sitzung_status(self) -> str:
        werk = getattr(self.agent, "sitzung", None)
        if werk is None or not werk.aktiv:
            return "Sitzungsgedächtnis: aus (SESSION_MEMORY=false)"
        k = werk.kennzahlen()
        zeile = (f"Sitzungsgedächtnis: an · {k.get('tage', 0)} Tage, "
                 f"{k.get('bestaetigt', 0)} bestätigt / {k.get('vorlaeufig', 0)} "
                 f"vorläufig · {werk.offene_zuege} Züge offen")
        if werk.zuletzt_verdichtet:
            zeile += f" · zuletzt {werk.zuletzt_verdichtet}"
        ausgelassen = (getattr(self.agent, "letzter_kontext", {}) or {}).get(
            "gedaechtnis_ausgelassen") or {}
        if any(ausgelassen.values()):
            zeile += (f"\n  ⚠ Zuletzt ausgelassen (Kontextbudget): "
                      f"{ausgelassen.get('fakten', 0)} Fakten, "
                      f"{ausgelassen.get('lektionen', 0)} Lektionen, "
                      f"{ausgelassen.get('auszug', 0)} Zeichen Anknüpfung")
        if werk.letzter_fehler:
            zeile += f"\n  ⚠ {werk.letzter_fehler}"
        return zeile

    def _episoden_status(self) -> str:
        """Eine Zeile Episodengedächtnis - mit dem, was die letzte Frage bekam."""
        werk = getattr(self.agent, "episoden", None)
        if werk is None or not werk.aktiv:
            return "Episodengedächtnis: aus (EPISODEN_GEDAECHTNIS=false)"
        k = werk.kennzahlen()
        zeile = (f"Episodengedächtnis: an · {k.get('zuege', 0)} Züge aus "
                 f"{k.get('sitzungen', 0)} Sitzungen · {k.get('vorgaben', 0)} geltende "
                 f"Vorgaben · {k.get('ersetzt', 0)} Angaben ersetzt")
        arbeit = getattr(self.agent, "letztes_arbeitsgedaechtnis", None)
        if arbeit is not None:
            d = arbeit.als_dict()
            zeile += (f"\n  Letzte Frage: {len(d['erinnerungen'])} frühere Stellen, "
                      f"{len(d['bezuege'])} Bezüge, {len(d['widersprueche'])} "
                      f"Widersprüche, Beleglage {d['beleg'] or '–'}")
        if k.get("defekte"):
            zeile += f"\n  ⚠ {k['defekte']} defekte Einträge übersprungen"
        if werk.gestoert:
            zeile += f"\n  ⚠ {werk.letzter_fehler}"
        return zeile

    def _status(self) -> None:
        s = self.agent.structure
        lines = [
            f"Konfig: {self.cfg.env_file or 'keine .env – Standardwerte'}",
            f"Vault: {self.cfg.vault_path}",
            f"Notizen: {s.note_count()} in {len(s.folder_paths)} Ordnern",
            f"Ordner: {', '.join(s.folder_paths) or '–'}",
            f"LLM: {self.cfg.llm_model} @ {self.cfg.host}",
            f"Embeddings: {self.cfg.embedding_model}",
            f"Schreibzugriff: {'aktiv' if self.cfg.tools_enabled else 'aus'} "
            f"({self.agent.tool_mode}, Backups: {'an' if self.cfg.backups_enabled else 'aus'})",
            f"Sprache: {self.cfg.language} ({self.cfg.sprach_kuerzel})",
            f"Sprachausgabe: {self.speaker.status} ({'an' if self.speaking else 'aus'})",
            f"Spracheingabe: {self.listener.status}",
            f"Verlauf: {len(self.agent.history)//2} Runden",
            self._sitzung_status(),
            self._episoden_status(),
            f"Terminal: {'ANSI ok' if self.ansi else 'ohne ANSI'}, "
            f"Live-Ansicht {'an' if self.live_moeglich else 'aus'}"
            + (f", {self.console.size.width}x{self.console.size.height}"
               if self.console else ""),
        ]
        text = "\n".join(lines)
        self.out(Panel(text, title="Status", border_style="blue") if self.console else text)


def main() -> None:
    if not RICH:
        print("Hinweis: 'rich' ist nicht installiert – die Oberfläche läuft im Textmodus.")
        print("Installieren mit: pip install rich")
    JarvisConsole().run()
