"""Tests für die Stimmenauswahl - besonders für piper.

Hintergrund: Unter Windows sind nur die Microsoft-SAPI-Stimmen installiert.
piper bringt lokale, natürlich klingende Stimmen mit. Getestet wird ohne
Audioausgabe: Es geht darum, ob die richtige .onnx-Datei gefunden, gewechselt
und synthetisiert wird - der Ton selbst ist nicht prüfbar.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import stubs  # noqa: E402

stubs.install()

TMP = Path(tempfile.mkdtemp(prefix="jarvis_stimme_"))
STIMMEN = TMP / "stimmen"
os.environ["OBSIDIAN_VAULT"] = str(TMP / "Zeus")
os.environ["VECTOR_STORE_PATH"] = str(TMP / "vector_store")
os.environ["PIPER_DATA_DIR"] = str(STIMMEN)
os.environ["TTS_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "false"

from jarvis import piper_stimmen  # noqa: E402
from jarvis.config import Settings  # noqa: E402
from jarvis.voice import Speaker, speakable  # noqa: E402

FAILURES = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"{'✅' if condition else '❌'} {name}" + (f"  – {detail}" if detail else ""))
    if not condition:
        FAILURES.append(name)


class FakePiperVoice:
    """Tut so, als wäre ein Modell geladen, und schreibt eine echte WAV-Datei."""

    geladen: list = []
    letzte_konfig = None

    def __init__(self, pfad: str):
        self.pfad = pfad

    @classmethod
    def load(cls, pfad, *args, **kwargs):
        cls.geladen.append(str(pfad))
        return cls(str(pfad))

    def synthesize_wav(self, text, wav_datei, syn_config=None, **kwargs):
        type(self).letzte_konfig = syn_config
        wav_datei.setnchannels(1)
        wav_datei.setsampwidth(2)
        wav_datei.setframerate(22050)
        # 0,2 s Stille reichen: geprüft wird die Dateigröße, nicht der Klang.
        wav_datei.writeframes(b"\x00\x00" * 4410)


class FakeSynthesisConfig:
    def __init__(self, **kwargs):
        self.werte = kwargs


def piper_modul_faelschen() -> None:
    import types

    modul = types.ModuleType("piper")
    modul.PiperVoice = FakePiperVoice
    modul.SynthesisConfig = FakeSynthesisConfig
    sys.modules["piper"] = modul


def stimmdatei_anlegen(kennung: str) -> Path:
    STIMMEN.mkdir(parents=True, exist_ok=True)
    datei = STIMMEN / f"{kennung}.onnx"
    datei.write_bytes(b"onnx-attrappe")
    (STIMMEN / f"{kennung}.onnx.json").write_text("{}", encoding="utf-8")
    return datei


def main() -> int:
    piper_modul_faelschen()
    alan = stimmdatei_anlegen("en_GB-alan-medium")
    stimmdatei_anlegen("de_DE-thorsten-medium")

    cfg = Settings()
    cfg.tts_enabled = True
    cfg.tts_engine = "piper"
    cfg.piper_data_dir = str(STIMMEN)
    cfg.language = "English"          # Stimme und Sprache müssen zusammenpassen
    cfg.piper_voice = "en_GB-alan-medium"
    cfg.tts_voice = ""

    gefunden = piper_stimmen.piper_modell_finden(cfg)
    check("Gewünschte Stimme wird gefunden", gefunden == alan, str(gefunden))

    alle = piper_stimmen.modelle_auflisten(cfg)
    check("Beide Stimmen werden aufgelistet", len(alle) == 2,
          ", ".join(p.stem for p in alle))

    speaker = Speaker(cfg)
    check("piper wird als Engine gewählt", speaker.engine_name == "piper", speaker.status)
    check("Stimmenname stimmt", speaker.voice_name == "en_GB-alan-medium", speaker.voice_name)

    # Wechsel per Namensteil, ohne Neustart
    gewechselt = speaker.stimme_setzen("thorsten")
    check("Wechsel per Namensteil", gewechselt == "de_DE-thorsten-medium", gewechselt)
    zurueck = speaker.stimme_setzen("alan")
    check("Wechsel zurück", zurueck == "en_GB-alan-medium", zurueck)

    # Unbekannte Stimme darf die aktive NICHT zerstören
    leer = speaker.stimme_setzen("gibtsnicht")
    check("Unbekannte Stimme ändert nichts",
          leer == "" and speaker.voice_name == "en_GB-alan-medium", speaker.voice_name)

    # Tempo: piper rechnet in Längenskala statt Wörter je Minute
    speaker.tempo_setzen(350)
    schnell = speaker._laengen_skala
    speaker.tempo_setzen(175)
    normal = speaker._laengen_skala
    check("Tempo wirkt auf die Längenskala", schnell < normal,
          f"{schnell:.2f} < {normal:.2f}")

    # Synthese schreibt eine abspielbare WAV-Datei
    ziel = TMP / "probe.wav"
    ok = speaker._piper_wav_schreiben("Good evening, Sir.", ziel)
    with wave.open(str(ziel), "rb") as wav:
        rahmen = wav.getnframes()
    check("WAV wird geschrieben", ok and rahmen > 1000, f"{rahmen} Rahmen")
    check("Klangeinstellungen werden übergeben",
          isinstance(FakePiperVoice.letzte_konfig, FakeSynthesisConfig),
          str(getattr(FakePiperVoice.letzte_konfig, "werte", "")))

    # Markdown darf nicht mitgesprochen werden
    gereinigt = speakable("## Titel\n\n- **fett** und `code`\n\n[[Notiz]]")
    check("Markdown wird für die Ausgabe entfernt",
          "#" not in gereinigt and "*" not in gereinigt and "[[" not in gereinigt,
          gereinigt)

    # Empfehlung: englisch männlich -> Butler-Stimme
    check("Empfehlung für Englisch ist alan",
          piper_stimmen.empfehlung_fuer("en", "m") == "en_GB-alan-medium")
    check("Empfehlung für Deutsch ist thorsten-high",
          piper_stimmen.empfehlung_fuer("de", "m") == "de_DE-thorsten-high",
          piper_stimmen.empfehlung_fuer("de", "m"))

    # Bereits vorhandene Stimme wird nicht erneut geladen
    ok, meldung = piper_stimmen.stimme_herunterladen("en_GB-alan-medium", cfg)
    check("Vorhandene Stimme wird nicht neu geladen", ok and "bereits" in meldung, meldung)

    text = piper_stimmen.uebersicht(cfg)
    check("Übersicht markiert installierte Stimmen",
          "✓ en_GB-alan-medium" in text and "en_US-ryan-high" in text)


    # ---------------------------- Sprache und Stimme dürfen nicht auseinanderlaufen
    # Josh hatte deutsche Ausgabe und eine englische Stimme – die las den
    # deutschen Text mit englischer Aussprache vor.
    deutsch = Settings()
    deutsch.tts_enabled = True
    deutsch.tts_engine = "piper"
    deutsch.piper_data_dir = str(STIMMEN)
    deutsch.language = "Deutsch"
    deutsch.piper_voice = "en_GB-alan-medium"   # steht noch vom Englischen drin
    deutsch.tts_voice = ""
    deutsch.piper_model = ""

    notizen = []
    gewaehlt = piper_stimmen.piper_modell_finden(deutsch, melden=notizen.append)
    check("Bei deutscher Ausgabe gewinnt die deutsche Stimme",
          gewaehlt is not None and gewaehlt.stem.startswith("de_DE"),
          gewaehlt.stem if gewaehlt else "keine")
    check("Der Konflikt wird gemeldet, nicht stillschweigend geändert",
          any("passt nicht zu LANGUAGE" in n for n in notizen), str(notizen))
    check("Die Meldung nennt die dauerhafte Lösung",
          any("PIPER_VOICE=de_DE" in n for n in notizen), str(notizen))

    sprecher = Speaker(deutsch)
    check("Der Status weist auf den Konflikt hin",
          "⚠️" in sprecher.status, sprecher.status[:90])

    # Ohne Angabe: die empfohlene Stimme der eingestellten Sprache
    deutsch.piper_voice = ""
    ohne_wunsch = piper_stimmen.piper_modell_finden(deutsch)
    check("Ohne PIPER_VOICE wird nach Sprache gewählt",
          ohne_wunsch is not None and ohne_wunsch.stem.startswith("de_DE"),
          ohne_wunsch.stem if ohne_wunsch else "keine")

    # Bei mehreren passenden gewinnt die höhere Qualitätsstufe
    stimmdatei_anlegen("de_DE-thorsten-high")
    beste = piper_stimmen.piper_modell_finden(deutsch)
    check("Unter mehreren deutschen Stimmen gewinnt die empfohlene",
          beste is not None and beste.stem == "de_DE-thorsten-high",
          beste.stem if beste else "keine")

    text = piper_stimmen.uebersicht(deutsch)
    check("Die Übersicht stellt die passende Sprache voran",
          "Für Deutsch:" in text and text.index("Für Deutsch:") < text.index("Andere Sprachen"),
          text.splitlines()[0])

    print("\n" + ("Alle Tests bestanden." if not FAILURES
                  else f"{len(FAILURES)} fehlgeschlagen: {FAILURES}"))
    return 1 if FAILURES else 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(code)
