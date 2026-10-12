"""Stimme für JARVIS: Sprachausgabe (TTS) und Spracheingabe (STT).

Beides ist optional und degradiert sauber: fehlt ein Paket, läuft der Agent
weiter, sagt aber einmal, was zu installieren wäre.
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import settings
from .piper_stimmen import (
    EMPFOHLENE_STIMMEN,
    abspielprogramm,
    modelle_auflisten,
    piper_modell_finden,
)

# Bekannte Stimmennamen. Windows meldet das Geschlecht meist gar nicht,
# deshalb der Umweg über die Namen der gängigen SAPI5- und macOS-Stimmen.
MAENNLICHE_STIMMEN = {
    "david", "mark", "george", "richard", "james", "guy", "ryan", "brian", "andrew",
    "christopher", "eric", "roger", "steffan", "thomas", "stefan", "conrad", "bernd",
    "alex", "daniel", "fred", "tom", "paul", "matthew", "joey", "oliver", "liam",
    # piper-Stimmen
    "alan", "thorsten", "karlsson", "joe", "john", "bryce", "norman", "danny", "kusal",
}
WEIBLICHE_STIMMEN = {
    "zira", "hazel", "susan", "linda", "heather", "catherine", "katja", "hedda",
    "anna", "samantha", "victoria", "karen", "moira", "tessa", "fiona", "ava",
    "joanna", "salli", "kimberly", "emma", "amy", "aria", "jenny", "michelle",
}
SPRACH_MUSTER = {
    "en": ("en-us", "en_us", "en-gb", "en_gb", "english", "united states",
           "united kingdom", "australia", r"\ben\b"),
    "de": ("de-de", "de_de", "german", "deutsch", "germany", r"\bde\b"),
    "fr": ("fr-fr", "fr_fr", "french", "français", r"\bfr\b"),
    "es": ("es-es", "es_es", "spanish", "español", r"\bes\b"),
    "it": ("it-it", "it_it", "italian", "italiano", r"\bit\b"),
}


def _geschlecht_normieren(wert: str) -> str:
    wert = (wert or "").strip().lower()
    if wert in ("m", "mann", "maennlich", "männlich", "male", "herr"):
        return "m"
    if wert in ("w", "f", "frau", "weiblich", "female"):
        return "w"
    return ""


def _geschlecht_erkennen(stimme) -> str:
    """Erst das Attribut, dann der Vorname - Windows liefert oft nichts."""
    roh = _geschlecht_normieren(str(getattr(stimme, "gender", "") or ""))
    if roh:
        return roh
    text = f"{getattr(stimme, 'name', '')} {getattr(stimme, 'id', '')}".lower()
    woerter = set(re.findall(r"[a-zäöüß]+", text))
    if woerter & MAENNLICHE_STIMMEN:
        return "m"
    if woerter & WEIBLICHE_STIMMEN:
        return "w"
    return ""


def _sprache_erkennen(stimme) -> str:
    text = f"{getattr(stimme, 'name', '')} {getattr(stimme, 'id', '')}".lower()
    for eintrag in (getattr(stimme, "languages", None) or []):
        try:
            text += " " + (eintrag.decode(errors="ignore")
                           if isinstance(eintrag, bytes) else str(eintrag)).lower()
        except Exception:
            pass
    for kuerzel, muster in SPRACH_MUSTER.items():
        for teil in muster:
            if re.search(teil, text):
                return kuerzel
    return ""


MARKDOWN_NOISE = [
    (re.compile(r"```.*?```", re.DOTALL), " Codeblock übersprungen. "),
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"), r"\1"),
    (re.compile(r"\[([^\]]+)\]\([^)]+\)"), r"\1"),
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE), ""),
    (re.compile(r"[*_>#]+"), ""),
    (re.compile(r"^\s*[-–•]\s*", re.MULTILINE), ""),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), ""),
    (re.compile(r"\n{2,}"), ". "),
    (re.compile(r"[ \t]{2,}"), " "),
]


def speakable(text: str, limit: int = 1200) -> str:
    for pattern, repl in MARKDOWN_NOISE:
        text = pattern.sub(repl, text)
    text = text.strip()
    if len(text) > limit:
        cut = text[:limit]
        text = cut[: cut.rfind(".") + 1] or cut
    return text


class Speaker:
    """Sprachausgabe in einem Hintergrund-Thread, jederzeit abbrechbar."""

    def __init__(self, cfg=settings):
        self.cfg = cfg
        self.engine_name = "off"
        self.status = "deaktiviert"
        self.voice_name = ""
        self._engine = None
        self._piper = None
        self._piper_pfad: Optional[Path] = None
        self._laengen_skala = max(0.3, float(getattr(cfg, "piper_length_scale", 1.0) or 1.0))
        self._proc: Optional[subprocess.Popen] = None
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._worker: Optional[threading.Thread] = None
        self._stop = threading.Event()
        if cfg.tts_enabled and cfg.tts_engine != "off":
            try:
                self._setup()
            except Exception as exc:  # nie den Start blockieren
                self.engine_name = "off"
                self.status = f"Sprachausgabe nicht verfügbar ({exc})"

    # ----------------------------------------------------------------- Setup
    def _setup(self) -> None:
        wanted = self.cfg.tts_engine
        order = [wanted] if wanted != "auto" else ["piper", "pyttsx3", "system"]
        for name in order:
            if name == "piper":
                # Bevorzugt das Python-Paket (pip install piper-tts): Das Modell
                # bleibt geladen, statt bei jedem Satz neu von der Platte zu kommen.
                # Der Hinweis landet im Status, damit ein Sprach-/Stimm-
                # Konflikt sichtbar ist, statt still falsch zu klingen.
                notiz: List[str] = []
                modell = piper_modell_finden(self.cfg, melden=notiz.append)
                if modell and self._piper_laden(modell):
                    if notiz:
                        self.status += f"  ⚠️ {notiz[0]}"
                    break
                # Ältere Installation mit separatem piper-Programm
                if self.cfg.piper_model and shutil.which(self.cfg.piper_binary):
                    self.engine_name = "piper-exe"
                    self.voice_name = Path(self.cfg.piper_model).stem
                    self.status = f"piper (Programm) · {self.voice_name}"
                    break
                if not modell and wanted == "piper":
                    self.status = (
                        "keine piper-Stimme gefunden - "
                        "python jarvis_run.py --stimme-einrichten"
                    )
            if name == "pyttsx3":
                try:
                    import pyttsx3

                    engine = pyttsx3.init()
                    engine.setProperty("rate", self.cfg.tts_rate)
                    self._engine = engine
                    gewaehlt = self._stimme_waehlen()
                    self.engine_name = "pyttsx3"
                    self.status = f"pyttsx3 · {gewaehlt or 'Standardstimme'}"
                    break
                except Exception as exc:  # pragma: no cover
                    self.status = f"pyttsx3 nicht nutzbar ({exc})"
            if name == "system":
                for binary in ("espeak-ng", "espeak", "say"):
                    if shutil.which(binary):
                        self.engine_name, self.status = binary, binary
                        break
                if self.engine_name != "off":
                    break
        if self.engine_name == "off" and self.status == "deaktiviert":
            self.status = "keine TTS-Engine gefunden"
        if self.engine_name == "off":
            self.status += " – python jarvis_run.py --stimme-einrichten"
        if self.engine_name != "off":
            self._worker = threading.Thread(target=self._run, daemon=True)
            self._worker.start()

    def _piper_laden(self, modell: Path) -> bool:
        """Lädt eine .onnx-Stimme. Schlägt es fehl, bleibt alles wie es war."""
        try:
            from piper import PiperVoice
        except Exception as exc:
            self.status = f"piper-tts fehlt ({exc}) - pip install piper-tts"
            return False
        try:
            stimme = PiperVoice.load(str(modell))
        except Exception as exc:
            self.status = f"piper-Modell nicht ladbar ({exc})"
            return False
        self._piper = stimme
        self._piper_pfad = modell
        self.engine_name = "piper"
        self.voice_name = modell.stem
        self.status = f"piper · {modell.stem}"
        return True

    @property
    def available(self) -> bool:
        return self.engine_name != "off"

    # ------------------------------------------------------- Stimmenauswahl
    def piper_stimmen(self) -> List[dict]:
        """Installierte piper-Stimmen, im selben Format wie stimmen()."""
        beschreibung = {e["kennung"].lower(): e for e in EMPFOHLENE_STIMMEN}
        eintraege = []
        for nummer, datei in enumerate(modelle_auflisten(self.cfg), 1):
            bekannt = beschreibung.get(datei.stem.lower(), {})
            eintraege.append({
                "nummer": nummer,
                "name": datei.stem,
                "id": str(datei),
                "sprache": bekannt.get("sprache", datei.stem[:2].lower()),
                "geschlecht": bekannt.get("geschlecht", ""),
                "klang": bekannt.get("klang", ""),
                "aktiv": self._piper_pfad is not None and datei == self._piper_pfad,
            })
        return eintraege

    def stimmen(self) -> List[dict]:
        """Alle installierten Stimmen mit erkannter Sprache und Geschlecht."""
        if self.engine_name == "piper":
            return self.piper_stimmen()
        if self._engine is None:
            return []
        gefunden = []
        try:
            rohe = self._engine.getProperty("voices") or []
        except Exception:
            return []
        for nummer, stimme in enumerate(rohe, 1):
            name = getattr(stimme, "name", "") or ""
            kennung = getattr(stimme, "id", "") or ""
            gefunden.append({
                "nummer": nummer,
                "name": name,
                "id": kennung,
                "sprache": _sprache_erkennen(stimme),
                "geschlecht": _geschlecht_erkennen(stimme),
            })
        return gefunden

    def _stimme_waehlen(self, wunsch: str = "") -> str:
        """Sucht die passendste Stimme: Name > Sprache > Geschlecht.

        Der frühere Code hat nur nach einem Namensfragment gesucht. Ohne Treffer
        blieb die Systemstandardstimme aktiv - bei deutschem Windows also eine
        deutsche Frauenstimme, egal was eingestellt war.
        """
        if self._engine is None:
            return ""
        wunschname = (wunsch or self.cfg.tts_voice or "").strip().lower()
        wunschsprache = self.cfg.sprach_kuerzel
        wunschgeschlecht = _geschlecht_normieren(self.cfg.tts_gender)

        bester, bestwert, bestname = None, -1_000, ""
        for eintrag in self.stimmen():
            wert = 0
            text = f"{eintrag['name']} {eintrag['id']}".lower()
            if wunschname:
                if wunschname == eintrag["name"].lower():
                    wert += 200
                elif wunschname in text:
                    wert += 120
            if eintrag["sprache"] == wunschsprache:
                wert += 60
            elif eintrag["sprache"] and eintrag["sprache"] != wunschsprache:
                wert -= 40
            if wunschgeschlecht and eintrag["geschlecht"]:
                wert += 45 if eintrag["geschlecht"] == wunschgeschlecht else -35
            if wert > bestwert:
                bester, bestwert, bestname = eintrag["id"], wert, eintrag["name"]

        # Ohne jeden Anhaltspunkt lieber nichts umstellen
        if bester is None or bestwert <= -50:
            return ""
        try:
            self._engine.setProperty("voice", bester)
        except Exception:
            return ""
        self.voice_name = bestname
        return bestname

    def stimme_setzen(self, auswahl: str) -> str:
        """Stimme zur Laufzeit wechseln - per Nummer oder Namensteil."""
        auswahl_roh = str(auswahl).strip()
        if self.engine_name == "piper":
            alle = self.piper_stimmen()
            treffer = None
            if auswahl_roh.isdigit():
                treffer = next((s for s in alle if s["nummer"] == int(auswahl_roh)), None)
            else:
                gesucht = auswahl_roh.lower()
                treffer = (next((s for s in alle if s["name"].lower() == gesucht), None)
                           or next((s for s in alle if gesucht in s["name"].lower()), None))
            if not treffer:
                return ""
            return self.voice_name if self._piper_laden(Path(treffer["id"])) else ""
        if self._engine is None:
            return ""
        alle = self.stimmen()
        auswahl = str(auswahl).strip()
        if auswahl.isdigit():
            treffer = next((s for s in alle if s["nummer"] == int(auswahl)), None)
            if treffer:
                try:
                    self._engine.setProperty("voice", treffer["id"])
                    self.voice_name = treffer["name"]
                    self.status = f"pyttsx3 · {treffer['name']}"
                    return treffer["name"]
                except Exception:
                    return ""
            return ""
        return self._stimme_waehlen(auswahl)

    def tempo_setzen(self, rate: int) -> bool:
        if self.engine_name == "piper":
            # piper kennt keine Wörter je Minute, sondern eine Längenskala.
            # 175 wpm gilt als Normaltempo; schneller heißt kürzere Laute.
            try:
                rate = int(rate)
            except (TypeError, ValueError):
                return False
            if rate <= 0:
                return False
            self._laengen_skala = max(0.3, min(2.5, 175.0 / rate))
            return True
        if self._engine is None:
            return False
        try:
            self._engine.setProperty("rate", int(rate))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ Lauf
    def _run(self) -> None:
        while True:
            text = self._queue.get()
            if text is None:
                return
            if self._stop.is_set():
                continue
            try:
                self._speak_blocking(text)
            except Exception:
                pass

    # ------------------------------------------------------------- piper
    def _piper_wav_schreiben(self, text: str, ziel: Path) -> bool:
        """Synthetisiert nach WAV. True nur, wenn die Datei danach Ton enthält."""
        import wave

        einstellungen = None
        try:
            from piper import SynthesisConfig

            werte = dict(
                length_scale=self._laengen_skala,
                noise_scale=float(getattr(self.cfg, "piper_noise_scale", 0.667) or 0.667),
                noise_w_scale=float(getattr(self.cfg, "piper_noise_w", 0.8) or 0.8),
            )
            # Mehr-Sprecher-Modelle wie de_DE-thorsten_emotional-medium
            sprecher = int(getattr(self.cfg, "piper_speaker", -1) or -1)
            if sprecher >= 0:
                werte["speaker_id"] = sprecher
            einstellungen = SynthesisConfig(**werte)
        except Exception:
            einstellungen = None       # ältere piper-Version ohne SynthesisConfig

        with wave.open(str(ziel), "wb") as wav:
            try:
                if einstellungen is not None:
                    self._piper.synthesize_wav(text, wav, syn_config=einstellungen)
                else:
                    self._piper.synthesize_wav(text, wav)
            except TypeError:
                # Signatur ohne syn_config
                self._piper.synthesize_wav(text, wav)
        try:
            return ziel.stat().st_size > 128
        except OSError:
            return False

    def _starten(self, befehl, **kwargs):
        """Programm starten - ein fehlendes darf die Ausgabe nicht abstürzen lassen.

        subprocess.Popen wirft FileNotFoundError (ein OSError), wenn aplay,
        espeak oder say nicht installiert sind. Das lief bisher ungefangen
        durch und riss den Sprech-Thread mit.
        """
        try:
            return subprocess.Popen(befehl, **kwargs)
        except OSError as exc:
            self.status = f"{befehl[0]} nicht startbar ({exc})"
            return None

    def _wav_abspielen(self, datei: Path) -> None:
        """WAV abspielen - auf Windows ohne Zusatzprogramm, sonst per Player."""
        if sys.platform.startswith("win"):
            try:
                import winsound

                # SND_FILENAME blockiert bis zum Ende - genau das wollen wir hier,
                # die Ausgabe läuft ohnehin schon im Hintergrund-Thread.
                winsound.PlaySound(str(datei), winsound.SND_FILENAME)
                return
            except Exception:
                pass
        player = abspielprogramm()
        if not player:
            self.status += " (kein Audioplayer gefunden)"
            return
        self._proc = self._starten(
            player + [str(datei)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        if self._proc is None:
            return
        self._proc.wait()
        self._proc = None

    def _speak_blocking(self, text: str) -> None:
        if self.engine_name == "piper" and self._piper is not None:
            pfad = Path(tempfile.gettempdir()) / f"jarvis_tts_{os.getpid()}_{threading.get_ident()}.wav"
            try:
                if self._piper_wav_schreiben(text, pfad) and not self._stop.is_set():
                    self._wav_abspielen(pfad)
            finally:
                try:
                    pfad.unlink()
                except OSError:
                    # Absicht: temporäre WAV-Datei. Windows sperrt sie kurz nach
                    # dem Abspielen; beim nächsten Satz wird sie überschrieben.
                    pass
            return
        if self.engine_name in ("piper", "piper-exe"):
            # Getrenntes piper-Programm: Rohton direkt in den Player pipen.
            modell = self.cfg.piper_model or (str(self._piper_pfad) if self._piper_pfad else "")
            player = abspielprogramm()
            if not modell or not player:
                return
            cmd = [self.cfg.piper_binary, "--model", modell, "--output-raw"]
            roh = player[0] == "aplay"
            piper = self._starten(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL)
            if piper is None:
                return
            befehl = (["aplay", "-q", "-r", "22050", "-f", "S16_LE", "-t", "raw", "-"]
                      if roh else player + ["-"])
            self._proc = self._starten(befehl, stdin=piper.stdout,
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            if self._proc is None:
                piper.kill()
                return
            try:
                piper.stdin.write(text.encode("utf-8"))  # type: ignore[union-attr]
                piper.stdin.close()  # type: ignore[union-attr]
            except OSError as exc:
                # Ohne Vermerk bliebe es einfach still, und niemand wüsste warum.
                self.status = f"piper (Programm) schreibt nicht ({exc})"
            self._proc.wait()
            self._proc = None
            return
        if self.engine_name == "pyttsx3" and self._engine is not None:
            self._engine.say(text)
            self._engine.runAndWait()
            return
        if self.engine_name in ("espeak-ng", "espeak"):
            stimme = self.cfg.tts_voice
            if not stimme:
                stimme = self.cfg.sprach_kuerzel
                if _geschlecht_normieren(self.cfg.tts_gender) == "m":
                    stimme += "+m3"          # männliche Variante von espeak
                elif _geschlecht_normieren(self.cfg.tts_gender) == "w":
                    stimme += "+f3"
            self._proc = self._starten(
                [self.engine_name, "-v", stimme, "-s", str(self.cfg.tts_rate), text],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            if self._proc is None:
                return
            self._proc.wait()
            self._proc = None
            return
        if self.engine_name == "say":
            befehl = ["say"]
            if self.cfg.tts_voice:
                befehl += ["-v", self.cfg.tts_voice]
            elif _geschlecht_normieren(self.cfg.tts_gender) == "m":
                befehl += ["-v", "Alex" if self.cfg.sprach_kuerzel == "en" else "Markus"]
            befehl.append(text)
            self._proc = self._starten(befehl, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
            if self._proc is None:
                return
            self._proc.wait()
            self._proc = None

    # --------------------------------------------------------------- Steuerung
    def say(self, text: str) -> None:
        if not self.available:
            return
        cleaned = speakable(text)
        if cleaned:
            self._stop.clear()
            self._queue.put(cleaned)

    def stop(self) -> None:
        self._stop.set()
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        if self.engine_name == "piper" and sys.platform.startswith("win"):
            try:
                import winsound

                winsound.PlaySound(None, winsound.SND_PURGE)
            except Exception:
                pass
        if self.engine_name == "pyttsx3" and self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass


class Listener:
    """Spracheingabe per faster-whisper, lokal. Nimmt auf, bis es still wird."""

    def __init__(self, cfg=settings):
        self.cfg = cfg
        self.status = "deaktiviert"
        self._model = None
        self._sd = None
        self._np = None
        if cfg.stt_enabled:
            try:
                self._probe()
            except Exception as exc:  # nie den Start blockieren
                self._sd = None
                self.status = f"Spracheingabe nicht verfügbar ({exc})"

    def _probe(self) -> None:
        # Bewusst breit: sounddevice wirft ohne PortAudio/Audiogerät OSError, nicht ImportError.
        try:
            import numpy as np
            import sounddevice as sd

            self._np, self._sd = np, sd
        except ImportError:
            self.status = "sounddevice/numpy fehlen (pip install sounddevice numpy)"
            return
        except Exception as exc:
            self.status = f"Audio nicht verfügbar ({exc})"
            return
        try:
            import faster_whisper  # noqa: F401

            self.status = f"bereit (Modell '{self.cfg.stt_model}' wird beim ersten Mal geladen)"
        except ImportError:
            self.status = "faster-whisper fehlt (pip install faster-whisper)"
            self._sd = None
        except Exception as exc:
            self.status = f"faster-whisper nicht nutzbar ({exc})"
            self._sd = None

    @property
    def available(self) -> bool:
        return self._sd is not None and "fehlt" not in self.status

    @staticmethod
    def _huggingface_beruhigen() -> None:
        """Die Warnungsflut beim ersten Modell-Download abstellen.

        Beim Diktieren erschienen mitten im Chat drei Absätze über HF_TOKEN,
        Symlinks und den Entwicklermodus von Windows. Nichts davon ist ein
        Problem: Der Download funktioniert ohne Konto, und ohne Symlinks
        braucht der Zwischenspeicher nur etwas mehr Platz. Für den Benutzer
        sieht es aber aus, als sei etwas kaputt.
        """
        import logging
        import os
        import warnings

        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
        os.environ.setdefault("HF_HUB_DISABLE_IMPLICIT_TOKEN", "1")
        for name in ("huggingface_hub", "huggingface_hub.file_download",
                     "faster_whisper"):
            logging.getLogger(name).setLevel(logging.ERROR)
        warnings.filterwarnings("ignore", module=r"huggingface_hub.*")

    def _load_model(self):
        if self._model is not None:
            return self._model
        self._huggingface_beruhigen()
        from faster_whisper import WhisperModel

        device = self.cfg.stt_device
        if device == "auto":
            device = "cpu"
            try:
                import torch  # type: ignore

                if torch.cuda.is_available():
                    device = "cuda"
            except Exception:
                pass
        compute = self.cfg.stt_compute_type or ("float16" if device == "cuda" else "int8")
        self._model = WhisperModel(self.cfg.stt_model, device=device, compute_type=compute)
        self.status = f"aktiv ({self.cfg.stt_model}, {device})"
        return self._model

    def record(self, on_level: Optional[Callable[[float], None]] = None):
        """Nimmt auf, bis es {STT_SILENCE_SECONDS} lang leise ist."""
        np, sd = self._np, self._sd
        assert np is not None and sd is not None
        sr = self.cfg.stt_samplerate
        frames = []
        silence_for = 0.0
        spoke = False
        block = 0.1
        threshold = 0.012
        started = time.time()

        with sd.InputStream(samplerate=sr, channels=1, dtype="float32") as stream:
            while True:
                data, _ = stream.read(int(sr * block))
                mono = data[:, 0]
                frames.append(mono.copy())
                level = float(np.sqrt(np.mean(mono ** 2)))
                if on_level:
                    on_level(level)
                if level > threshold:
                    spoke, silence_for = True, 0.0
                else:
                    silence_for += block
                if spoke and silence_for >= self.cfg.stt_silence_seconds:
                    break
                if time.time() - started > self.cfg.stt_max_seconds:
                    break
                if not spoke and time.time() - started > 6:
                    return None
        return np.concatenate(frames) if frames else None

    def listen(self, on_level: Optional[Callable[[float], None]] = None) -> Optional[str]:
        if not self.available:
            return None
        audio = self.record(on_level=on_level)
        if audio is None or len(audio) < self.cfg.stt_samplerate * 0.4:
            return None
        model = self._load_model()
        segments, _info = model.transcribe(
            audio,
            language=self.cfg.stt_language or None,
            vad_filter=True,
            beam_size=1,
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        return text or None
