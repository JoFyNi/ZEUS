"""piper-Stimmen: finden, auflisten, nachinstallieren.

Windows liefert nur die Microsoft-SAPI-Stimmen mit - die klingen blechern.
piper ist ein lokales neuronales TTS (kein Konto, keine Cloud, kein Internet
nach dem Download) und klingt deutlich natürlicher.

Installation:
    pip install piper-tts
    python -m piper.download_voices en_GB-alan-medium --data-dir stimmen

Dieses Modul kapselt beides, damit der Agent es selbst anstoßen kann.
Es LÖSCHT nie etwas und ersetzt keine bestehende Engine - pyttsx3, espeak und
say bleiben unverändert als Rückfallebene erhalten.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import settings

# Kuratierte Auswahl. Die Beschreibung ist bewusst in Klangbildern gehalten -
# "welche klingt wie ein Butler?" ist die Frage, die hier beantwortet wird.
EMPFOHLENE_STIMMEN: List[Dict[str, str]] = [
    {
        "kennung": "en_GB-alan-medium",
        "sprache": "en",
        "geschlecht": "m",
        "klang": "Britisch, ruhig, sachlich - die Butler-Stimme. Empfehlung.",
    },
    {
        "kennung": "en_GB-alan-low",
        "sprache": "en",
        "geschlecht": "m",
        "klang": "Wie alan-medium, kleineres Modell - für schwache Rechner.",
    },
    {
        "kennung": "en_GB-northern_english_male-medium",
        "sprache": "en",
        "geschlecht": "m",
        "klang": "Britisch mit nordenglischem Einschlag, wärmer, etwas rauer.",
    },
    {
        "kennung": "en_US-ryan-high",
        "sprache": "en",
        "geschlecht": "m",
        "klang": "Amerikanisch, tief und gelassen. Höchste Qualitätsstufe.",
    },
    {
        "kennung": "en_US-ryan-medium",
        "sprache": "en",
        "geschlecht": "m",
        "klang": "Wie ryan-high, schneller und sparsamer.",
    },
    {
        "kennung": "en_US-joe-medium",
        "sprache": "en",
        "geschlecht": "m",
        "klang": "Amerikanisch, neutral, nachrichtensprecherhaft.",
    },
    {
        "kennung": "en_US-hfc_male-medium",
        "sprache": "en",
        "geschlecht": "m",
        "klang": "Amerikanisch, hell und freundlich.",
    },
    {
        "kennung": "de_DE-thorsten-high",
        "sprache": "de",
        "geschlecht": "m",
        "klang": "Deutsch, männlich, ruhig und klar - die Butler-Stimme für "
                 "Deutsch. Höchste Qualitätsstufe. Empfehlung.",
    },
    {
        "kennung": "de_DE-thorsten-medium",
        "sprache": "de",
        "geschlecht": "m",
        "klang": "Wie thorsten-high, schneller und sparsamer.",
    },
    {
        "kennung": "de_DE-thorsten-low",
        "sprache": "de",
        "geschlecht": "m",
        "klang": "Kleinstes Thorsten-Modell - für schwache Rechner.",
    },
    {
        "kennung": "de_DE-karlsson-low",
        "sprache": "de",
        "geschlecht": "m",
        "klang": "Deutsch, männlich, dunkler und getragener. Nur Stufe 'low'.",
    },
    {
        "kennung": "de_DE-pavoque-low",
        "sprache": "de",
        "geschlecht": "m",
        "klang": "Deutsch, männlich, sachlich-trocken. Nur Stufe 'low'.",
    },
    {
        "kennung": "de_DE-thorsten_emotional-medium",
        "sprache": "de",
        "geschlecht": "m",
        "klang": "Deutsch, männlich, mit Stimmungen. Braucht PIPER_SPEAKER "
                 "(0=amused, 1=angry, 2=disgusted, 3=drunk, 4=neutral, "
                 "5=sleepy, 6=surprised, 7=whisper).",
    },
]

# Kein fester Vorgabewert mehr: Die Stimme richtet sich nach LANGUAGE.
# Vorher stand hier en_GB-alan-medium fest verdrahtet - bei deutscher Ausgabe
# las dann eine englische Stimme deutschen Text vor, mit dem erwartbaren
# Ergebnis. Sprache und Stimme dürfen nicht auseinanderlaufen.
STANDARD_STIMME = "en_GB-alan-medium"


def empfehlung_fuer(sprache: str, geschlecht: str = "m") -> str:
    """Beste Stimme für Sprache/Geschlecht - fällt auf die Standardstimme zurück."""
    sprache = (sprache or "en").lower()[:2]
    for eintrag in EMPFOHLENE_STIMMEN:
        if eintrag["sprache"] == sprache and eintrag["geschlecht"] == (geschlecht or "m"):
            return eintrag["kennung"]
    return STANDARD_STIMME


def paket_vorhanden() -> bool:
    try:
        import piper  # noqa: F401

        return True
    except Exception:
        return False


def _suchordner(cfg=settings) -> List[Path]:
    """Alle Orte, an denen eine .onnx-Stimme liegen könnte - ohne Duplikate."""
    ordner: List[Path] = []
    for kandidat in (
        cfg.piper_data_path,
        Path(__file__).resolve().parent.parent / "stimmen",
        Path.cwd() / "stimmen",
        Path.home() / ".local" / "share" / "piper" / "voices",
        Path.home() / ".piper",
    ):
        try:
            aufgeloest = kandidat.expanduser()
        except (OSError, RuntimeError):
            continue
        if aufgeloest not in ordner:
            ordner.append(aufgeloest)
    return ordner


def modelle_auflisten(cfg=settings) -> List[Path]:
    """Alle installierten piper-Stimmen, ohne Doppelnennung gleicher Namen."""
    gefunden: List[Path] = []
    namen = set()
    for ordner in _suchordner(cfg):
        try:
            if not ordner.is_dir():
                continue
            dateien = sorted(ordner.rglob("*.onnx"))
        except OSError:
            continue
        for datei in dateien:
            if datei.name in namen:
                continue
            namen.add(datei.name)
            gefunden.append(datei)
    return gefunden


def sprache_von(kennung: str) -> str:
    """'de_DE-thorsten-high' -> 'de'. Für den Abgleich mit LANGUAGE."""
    return str(kennung).strip().lower()[:2]


def piper_modell_finden(cfg=settings, melden=None) -> Optional[Path]:
    """Die Stimmdatei, die benutzt werden soll.

    Reihenfolge: expliziter PIPER_MODEL-Pfad, dann PIPER_VOICE bzw. TTS_VOICE -
    ABER nur, wenn die Stimme zur eingestellten Sprache passt. Eine englische
    Stimme, die deutschen Text vorliest, ist kein Randfall, sondern das, was
    beim Sprachwechsel zwangsläufig passiert: LANGUAGE wird umgestellt,
    PIPER_VOICE bleibt stehen.
    """
    hinweis = melden or (lambda _t: None)

    # 1) Voll ausgeschriebener Pfad in der .env - der ist eine bewusste Ansage
    if cfg.piper_model.strip():
        pfad = Path(cfg.piper_model.strip()).expanduser()
        if pfad.is_file():
            return pfad

    vorhandene = modelle_auflisten(cfg)
    if not vorhandene:
        return None

    kuerzel = cfg.sprach_kuerzel or "en"
    passend = [d for d in vorhandene if sprache_von(d.stem) == kuerzel]

    # 2) Gewünschte Kennung - exakt, dann als Namensteil
    wuensche = [w.strip().lower() for w in (cfg.piper_voice, cfg.tts_voice) if w and w.strip()]
    for wunsch in wuensche:
        treffer = None
        for datei in vorhandene:
            if datei.stem.lower() == wunsch:
                treffer = datei
                break
        if treffer is None:
            for datei in vorhandene:
                if wunsch in datei.stem.lower():
                    treffer = datei
                    break
        if treffer is None:
            continue
        if sprache_von(treffer.stem) == kuerzel or not passend:
            return treffer
        # Eingestellte Stimme spricht eine andere Sprache als die Ausgabe -
        # und es liegt eine passende da. Dann die BESTE passende, aber laut
        # sagen, was gerade passiert.
        ersatz = _beste(passend, cfg, kuerzel)
        hinweis(f"'{treffer.stem}' passt nicht zu LANGUAGE={cfg.language} – "
                f"nutze stattdessen '{ersatz.stem}'. Dauerhaft: "
                f"PIPER_VOICE={ersatz.stem}")
        return ersatz

    # 3) Ohne Wunsch: die beste installierte Stimme in der richtigen Sprache
    if passend:
        return _beste(passend, cfg, kuerzel)
    hinweis(f"Keine piper-Stimme für {cfg.language} installiert. Nachladen: "
            f"python jarvis_run.py --stimme-einrichten "
            f"{empfehlung_fuer(kuerzel, _geschlecht_kuerzel(cfg))}")
    return vorhandene[0]


def _beste(kandidaten: List[Path], cfg, kuerzel: str) -> Path:
    """Aus mehreren passenden Stimmen die empfohlene, sonst die hochwertigste."""
    empfohlen = empfehlung_fuer(kuerzel, _geschlecht_kuerzel(cfg)).lower()
    for datei in kandidaten:
        if datei.stem.lower() == empfohlen:
            return datei
    # Qualitätsstufe steckt im Namen: …-high schlägt …-medium schlägt …-low
    rang = {"high": 0, "medium": 1, "low": 2, "x_low": 3}
    return sorted(kandidaten,
                  key=lambda p: rang.get(p.stem.rsplit("-", 1)[-1].lower(), 9))[0]


def _geschlecht_kuerzel(cfg) -> str:
    wert = str(getattr(cfg, "tts_gender", "") or "").strip().lower()
    if wert in ("w", "f", "frau", "weiblich", "female"):
        return "w"
    return "m"


def paket_installieren(protokoll=None) -> Tuple[bool, str]:
    """pip install piper-tts - im selben Interpreter, der gerade läuft."""
    if paket_vorhanden():
        return True, "piper-tts ist bereits installiert."
    befehl = [sys.executable, "-m", "pip", "install", "piper-tts"]
    if protokoll:
        protokoll(" ".join(befehl))
    try:
        ergebnis = subprocess.run(befehl, capture_output=True, text=True, timeout=900)
    except Exception as exc:
        return False, f"pip konnte nicht gestartet werden: {exc}"
    if ergebnis.returncode != 0:
        letzte = (ergebnis.stderr or ergebnis.stdout or "").strip().splitlines()[-5:]
        return False, "pip install piper-tts fehlgeschlagen: " + " / ".join(letzte)
    return True, "piper-tts installiert."


def stimme_herunterladen(kennung: str, cfg=settings, protokoll=None) -> Tuple[bool, str]:
    """Lädt eine Stimme nach. Vorhandene Dateien werden nicht angetastet."""
    kennung = (kennung or STANDARD_STIMME).strip()
    ziel = cfg.piper_data_path
    try:
        ziel.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"Zielordner {ziel} nicht anlegbar: {exc}"

    schon_da = ziel / f"{kennung}.onnx"
    if schon_da.is_file():
        return True, f"{kennung} liegt bereits in {ziel}."

    befehl = [sys.executable, "-m", "piper.download_voices", kennung, "--data-dir", str(ziel)]
    if protokoll:
        protokoll(" ".join(befehl))
    try:
        ergebnis = subprocess.run(befehl, capture_output=True, text=True, timeout=1800)
    except Exception as exc:
        return False, f"Download nicht startbar: {exc}"
    if ergebnis.returncode != 0:
        letzte = (ergebnis.stderr or ergebnis.stdout or "").strip().splitlines()[-5:]
        return False, f"Download von {kennung} fehlgeschlagen: " + " / ".join(letzte)
    if not schon_da.is_file() and not list(ziel.rglob(f"{kennung}.onnx")):
        return False, f"Download meldete Erfolg, aber {kennung}.onnx fehlt in {ziel}."
    return True, f"{kennung} liegt jetzt in {ziel}."


def einrichten(kennung: str = "", cfg=settings, protokoll=None) -> Tuple[bool, str]:
    """Paket installieren und eine Stimme laden - ein Aufruf, ein Ergebnis."""
    melden = protokoll or (lambda _t: None)
    # Ohne Angabe die Stimme zur eingestellten SPRACHE laden, nicht die
    # englische Vorgabe. Sonst richtet man bei deutscher Ausgabe eine
    # englische Stimme ein und wundert sich über die Aussprache.
    kennung = (kennung or cfg.piper_voice
               or empfehlung_fuer(cfg.sprach_kuerzel, _geschlecht_kuerzel(cfg))).strip()

    ok, meldung = paket_installieren(melden)
    melden(meldung)
    if not ok:
        return False, meldung

    ok, meldung = stimme_herunterladen(kennung, cfg, melden)
    melden(meldung)
    if not ok:
        return False, meldung

    hinweis = (
        f"Fertig. Trage in die .env ein:\n"
        f"  TTS_ENGINE=piper\n"
        f"  PIPER_VOICE={kennung}\n"
        f"  PIPER_DATA_DIR={cfg.piper_data_path}"
    )
    return True, hinweis


def uebersicht(cfg=settings) -> str:
    """Kurzer Text für /stimme liste und --stimmen."""
    kuerzel = cfg.sprach_kuerzel or "en"
    empfohlen = empfehlung_fuer(kuerzel, _geschlecht_kuerzel(cfg))
    installiert = {p.stem.lower() for p in modelle_auflisten(cfg)}

    zeilen = [f"Ausgabesprache: {cfg.language} ({kuerzel}) – "
              f"empfohlene Stimme: {empfohlen}", ""]
    # Passende Sprache zuerst; eine Stimme in einer anderen Sprache ist für
    # diese Einstellung schlicht die falsche Wahl und gehört nicht nach oben.
    for ueberschrift, gruppe in (
            (f"Für {cfg.language}", [e for e in EMPFOHLENE_STIMMEN if e["sprache"] == kuerzel]),
            ("Andere Sprachen", [e for e in EMPFOHLENE_STIMMEN if e["sprache"] != kuerzel])):
        if not gruppe:
            continue
        zeilen.append(f"{ueberschrift}:")
        for eintrag in gruppe:
            marke = "✓" if eintrag["kennung"].lower() in installiert else " "
            zeilen.append(f" {marke} {eintrag['kennung']:34s} {eintrag['klang']}")
        zeilen.append("")
    zeilen.append(f"Ordner: {cfg.piper_data_path}")
    zeilen.append(f"Nachladen: python jarvis_run.py --stimme-einrichten {empfohlen}")
    zeilen.append("(ohne Kennung nimmt er automatisch die zur Sprache passende)")
    if not paket_vorhanden():
        zeilen.append("Hinweis: piper-tts ist noch nicht installiert (pip install piper-tts).")
    return "\n".join(zeilen)


def abspielprogramm() -> Optional[List[str]]:
    """Externer Player für WAV-Dateien, falls winsound fehlt (Linux/macOS)."""
    if shutil.which("aplay"):
        return ["aplay", "-q"]
    if shutil.which("afplay"):
        return ["afplay"]
    if shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet"]
    if shutil.which("paplay"):
        return ["paplay"]
    return None
