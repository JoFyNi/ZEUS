"""Hardware-Diagnose: Warum ist es langsam, und was hilft wirklich.

Wichtig vorweg, weil es eine verbreitete Fehlannahme ist: **Ollama nutzt weder
TensorFlow noch PyTorch.** Darunter liegt llama.cpp/GGML mit eigenen CUDA-,
ROCm- und Metal-Kerneln. Ein `pip install torch` ändert an der Geschwindigkeit
also genau nichts.

Was tatsächlich zählt:

1. Ob Ollama überhaupt eine GPU sieht (Treiber, CUDA-Laufzeit).
2. Wie viele **Schichten** ins VRAM passen. Passt nur ein Teil, rechnet der
   Rest auf der CPU - und der langsamste Teil bestimmt das Tempo. Halb
   ausgelagert ist fast so langsam wie gar nicht ausgelagert.
3. Die **Quantisierung**. q4_K_M braucht rund die Hälfte von q8_0.

Diese Datei liest den echten Ladezustand aus (`/api/ps` liefert `size` und
`size_vram`) und rechnet aus, was bei der vorhandenen VRAM-Menge passt.
"""
from __future__ import annotations

import platform
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import requests

from .config import settings

# Ungefährer Speicherbedarf gängiger Modelle in GB (q4-Quantisierung, ohne Kontext)
MODELL_GROESSEN = {
    "qwen2.5:32b": 20.0, "qwen2.5:14b": 9.0, "qwen2.5:7b": 4.7, "qwen2.5:3b": 2.0,
    "qwen2.5:1.5b": 1.0, "llama3.1:8b": 4.9, "llama3:8b": 4.7, "mistral": 4.1,
    "mistral-nemo": 7.1, "gemma2:9b": 5.4, "phi3:medium": 7.9, "phi3": 2.2,
}


@dataclass
class Ladezustand:
    modell: str = ""
    gesamt_gb: float = 0.0
    im_vram_gb: float = 0.0
    geladen: bool = False

    @property
    def anteil_gpu(self) -> float:
        if not self.gesamt_gb:
            return 0.0
        return max(0.0, min(1.0, self.im_vram_gb / self.gesamt_gb))


@dataclass
class GpuInfo:
    vorhanden: bool = False
    name: str = ""
    vram_gb: float = 0.0
    frei_gb: float = 0.0
    treiber: str = ""


@dataclass
class Befund:
    gpu: GpuInfo = field(default_factory=GpuInfo)
    zustand: Ladezustand = field(default_factory=Ladezustand)
    tempo: Optional[float] = None
    system: str = ""
    ram_gb: float = 0.0
    hinweise: List[str] = field(default_factory=list)
    empfehlungen: List[str] = field(default_factory=list)
    entfernt: bool = False
    server_version: str = ""

    def bericht(self) -> str:
        zeilen = [f"System: {self.system}"]
        if self.server_version:
            zeilen.append(f"Ollama-Version: {self.server_version}")
        if self.entfernt:
            # Die lokale Hardware wird bewusst NICHT gezeigt.
            zeilen.append("Die Grafikkarte dieses Rechners spielt keine Rolle – "
                          "gerechnet wird auf dem Server.")
            if self.zustand.geladen:
                zeilen.append(
                    f"Modell '{self.zustand.modell}' auf dem Server: "
                    f"{self.zustand.gesamt_gb:.1f} GB belegt, davon "
                    f"{self.zustand.im_vram_gb:.1f} GB im VRAM "
                    f"→ {self.zustand.anteil_gpu * 100:.0f} % auf der GPU")
            else:
                zeilen.append("Auf dem Server ist gerade kein Modell geladen.")
            if self.tempo:
                zeilen.append(f"Gemessen: ~{self.tempo} Token/s")
            if self.hinweise:
                zeilen.append("")
                zeilen.extend(f"• {h}" for h in self.hinweise)
            if self.empfehlungen:
                zeilen += ["", "Empfohlene Schritte, wichtigster zuerst:"]
                zeilen.extend(f"{i}. {e}" for i, e in enumerate(self.empfehlungen, 1))
            return "\n".join(zeilen)
        if self.ram_gb:
            zeilen.append(f"Arbeitsspeicher: {self.ram_gb:.0f} GB")
        if self.gpu.vorhanden:
            zeilen.append(
                f"GPU: {self.gpu.name} · {self.gpu.vram_gb:.1f} GB VRAM "
                f"(frei: {self.gpu.frei_gb:.1f} GB)"
                + (f" · Treiber {self.gpu.treiber}" if self.gpu.treiber else "")
            )
        else:
            zeilen.append("GPU: keine erkannt")
        if self.zustand.geladen:
            zeilen.append(
                f"Modell '{self.zustand.modell}': {self.zustand.gesamt_gb:.1f} GB geladen, "
                f"davon {self.zustand.im_vram_gb:.1f} GB im VRAM "
                f"→ {self.zustand.anteil_gpu * 100:.0f} % auf der GPU"
            )
        else:
            zeilen.append("Modell ist gerade nicht geladen (erst eine Anfrage stellen).")
        if self.tempo:
            zeilen.append(f"Gemessen: ~{self.tempo} Token/s")
        if self.hinweise:
            zeilen.append("")
            zeilen.extend(f"• {h}" for h in self.hinweise)
        if self.empfehlungen:
            zeilen.append("")
            zeilen.append("Empfohlene Schritte, wichtigster zuerst:")
            zeilen.extend(f"{i}. {e}" for i, e in enumerate(self.empfehlungen, 1))
        return "\n".join(zeilen)



# Auch die Hardware-Abfragen gehen ins Protokoll - sie sprechen mit demselben
# Server wie das Modell, und wer wissen will, was den Rechner verlaesst, will
# auch diese Zeilen sehen.
_SITZUNG = None


def _sitzung():
    global _SITZUNG
    if _SITZUNG is None:
        from .protokoll import netzsession
        _SITZUNG = netzsession("hardware")
    return _SITZUNG

def _programm_ausgabe(befehl: Sequence[str], zeitlimit: int = 15) -> str:
    """Ein externes Programm aufrufen - ohne dass ein fehlendes den Lauf killt.

    subprocess.run wirft FileNotFoundError, wenn es das Programm nicht gibt.
    Das ist ein OSError und KEIN SubprocessError - deshalb rutschte es durch
    jedes `except subprocess.SubprocessError` hindurch. Genau daran ist /tempo
    gestorben, als Windows 11 `wmic` entfernt hatte. Alle Aufrufe hier gehen
    jetzt durch diese eine Stelle.
    """
    try:
        fertig = subprocess.run(befehl, capture_output=True, text=True,
                                timeout=zeitlimit)
    except FileNotFoundError:
        return ""                       # Programm gibt es auf diesem Rechner nicht
    except (OSError, subprocess.SubprocessError):
        return ""                       # abgebrochen, kein Recht, Zeitlimit
    return fertig.stdout or ""


# --------------------------------------------------------------------- Abfrage
def gpu_erkennen() -> GpuInfo:
    """nvidia-smi ist der zuverlässigste Weg; für AMD/Apple gibt es Ersatzpfade."""
    info = GpuInfo()
    if shutil.which("nvidia-smi"):
        try:
            roh = _programm_ausgabe(
                ["nvidia-smi",
                 "--query-gpu=name,memory.total,memory.free,driver_version",
                 "--format=csv,noheader,nounits"], 15).strip().splitlines()
            if roh:
                teile = [t.strip() for t in roh[0].split(",")]
                info.vorhanden = True
                info.name = teile[0]
                info.vram_gb = float(teile[1]) / 1024
                info.frei_gb = float(teile[2]) / 1024
                info.treiber = teile[3] if len(teile) > 3 else ""
                return info
        except (ValueError, IndexError):
            pass                        # Ausgabe unerwartet formatiert

    if platform.system() == "Darwin" and platform.machine().startswith("arm"):
        info.vorhanden = True
        info.name = "Apple Silicon (gemeinsamer Speicher)"
        info.vram_gb = _ram_gb() * 0.7
        info.frei_gb = info.vram_gb
        return info

    for befehl, muster in (
        (["rocm-smi", "--showmeminfo", "vram"], r"(\d+)\s*$"),
    ):
        if shutil.which(befehl[0]):
            try:
                ausgabe = _programm_ausgabe(befehl, 15)
                zahlen = [int(z) for z in re.findall(muster, ausgabe, re.MULTILINE)]
                if zahlen:
                    info.vorhanden = True
                    info.name = "AMD GPU (ROCm)"
                    info.vram_gb = max(zahlen) / (1024 ** 3)
                    info.frei_gb = info.vram_gb
            except ValueError:
                pass                    # Ausgabe unerwartet formatiert
    return info


def _ram_gb() -> float:
    try:
        import os

        if hasattr(os, "sysconf") and "SC_PAGE_SIZE" in os.sysconf_names:
            return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / (1024 ** 3)
    except (ValueError, OSError):
        pass
    if platform.system() == "Windows":
        # Früher lief das über `wmic`. Das Programm ist in Windows 11 entfernt
        # worden - der Aufruf endete dann in FileNotFoundError, und weil das
        # ein OSError ist und kein SubprocessError, flog /tempo mit einem
        # Traceback aus dem Chat. GlobalMemoryStatusEx ist Teil der API,
        # braucht keinen Unterprozess und kann nicht fehlen.
        try:
            import ctypes

            class _Speicher(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _Speicher()
            status.dwLength = ctypes.sizeof(_Speicher)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / (1024 ** 3)
        except Exception:
            # Kein Grund zum Abbruch: 0.0 heißt "unbekannt", und der Bericht
            # kommt damit zurecht.
            pass
        # Letzte Rückfallebene für ältere Systeme, auf denen wmic noch da ist
        try:
            ausgabe = _programm_ausgabe(
                ["wmic", "computersystem", "get", "TotalPhysicalMemory"], 10)
            zahlen = re.findall(r"\d{9,}", ausgabe)
            if zahlen:
                return int(zahlen[0]) / (1024 ** 3)
        except ValueError:
            pass
    return 0.0


def ladezustand(cfg=settings) -> Ladezustand:
    """`/api/ps` verrät, wie viel des Modells wirklich im VRAM liegt."""
    zustand = Ladezustand(modell=cfg.llm_model)
    try:
        antwort = _sitzung().get(f"{cfg.host}/api/ps", timeout=10)
        antwort.raise_for_status()
        modelle = antwort.json().get("models") or []
    except (requests.RequestException, ValueError):
        return zustand

    for eintrag in modelle:
        name = str(eintrag.get("name") or eintrag.get("model") or "")
        if name.split(":")[0] != cfg.llm_model.split(":")[0]:
            continue
        zustand.modell = name
        zustand.geladen = True
        zustand.gesamt_gb = float(eintrag.get("size", 0)) / (1024 ** 3)
        zustand.im_vram_gb = float(eintrag.get("size_vram", 0)) / (1024 ** 3)
        break
    return zustand


def modellgroesse(name: str) -> Optional[float]:
    if name in MODELL_GROESSEN:
        return MODELL_GROESSEN[name]
    basis = name.split(":")[0]
    for schluessel, wert in MODELL_GROESSEN.items():
        if schluessel.split(":")[0] == basis:
            return wert
    treffer = re.search(r"(\d+(?:\.\d+)?)\s*b\b", name, re.IGNORECASE)
    if treffer:
        return float(treffer.group(1)) * 0.65   # grobe Faustregel für q4
    return None


# ------------------------------------------------------------------- Analyse
def ist_entfernt(cfg=settings) -> bool:
    """Läuft Ollama auf diesem Rechner oder woanders?

    Das ist die Frage, an der die ganze Diagnose hängt. Zeigt OLLAMA_HOST auf
    einen anderen Rechner, sagt die lokale GPU nichts über das Tempo aus - und
    ein Bericht, der die Grafikkarte des Laptops zeigt, während das Modell auf
    einem Server rechnet, ist nicht nur nutzlos, sondern führt in die Irre.
    """
    host = str(cfg.host or "")
    for eigen in ("localhost", "127.0.0.1", "0.0.0.0", "[::1]", "host.docker.internal"):
        if eigen in host:
            return False
    return bool(host)


def server_auskunft(cfg=settings) -> dict:
    """Was der Server über sich selbst preisgibt.

    Ollama verrät über HTTP nicht, welche Hardware darunter steckt - es gibt
    keinen Endpunkt dafür. Was es gibt: die Version und, über `/api/ps`, wie
    viel des Modells im VRAM liegt. Aus dem Verhältnis VRAM zu Gesamtgröße
    lässt sich das Wesentliche ableiten - nämlich ob dort überhaupt eine GPU
    arbeitet. Mehr wird hier nicht behauptet.
    """
    auskunft: dict = {"host": cfg.host, "erreichbar": False}
    try:
        antwort = _sitzung().get(f"{cfg.host}/api/version", timeout=6)
        antwort.raise_for_status()
        auskunft["version"] = str(antwort.json().get("version") or "?")
        auskunft["erreichbar"] = True
    except (requests.RequestException, ValueError):
        return auskunft

    try:
        antwort = _sitzung().get(f"{cfg.host}/api/ps", timeout=8)
        antwort.raise_for_status()
        modelle = antwort.json().get("models") or []
    except (requests.RequestException, ValueError):
        modelle = []

    auskunft["geladen"] = [{
        "name": str(m.get("name") or m.get("model") or "?"),
        "gesamt_gb": float(m.get("size", 0)) / (1024 ** 3),
        "vram_gb": float(m.get("size_vram", 0)) / (1024 ** 3),
    } for m in modelle]
    return auskunft


def analysieren(cfg=settings, tempo: Optional[float] = None) -> Befund:
    entfernt = ist_entfernt(cfg)
    befund = Befund(
        # Bei einem entfernten Server wird die LOKALE Hardware nicht erhoben.
        # Sie hat mit dem gemessenen Tempo nichts zu tun, und sie im Bericht
        # zu zeigen hieße, eine Ursache zu behaupten, die es nicht ist.
        gpu=GpuInfo() if entfernt else gpu_erkennen(),
        zustand=ladezustand(cfg),
        tempo=tempo,
        system=(f"Ollama läuft entfernt auf {cfg.host}" if entfernt
                else f"{platform.system()} {platform.release()} ({platform.machine()})"),
        ram_gb=0.0 if entfernt else _ram_gb(),
    )
    befund.entfernt = entfernt
    if entfernt:
        return _entfernt_beurteilen(befund, cfg, tempo)
    gpu, zustand = befund.gpu, befund.zustand
    bedarf = modellgroesse(cfg.llm_model) or 0.0
    # Kontext braucht zusätzlich Speicher - grob 0,5 GB je 8k bei einem 14b-Modell
    kontext_gb = cfg.num_ctx / 8192 * 0.6
    gesamtbedarf = bedarf + kontext_gb

    if bedarf:
        befund.hinweise.append(
            f"'{cfg.llm_model}' braucht etwa {bedarf:.1f} GB, dazu ~{kontext_gb:.1f} GB "
            f"für {cfg.num_ctx} Token Kontext – zusammen rund {gesamtbedarf:.1f} GB."
        )

    # --- Der eigentliche Befund ---
    if not gpu.vorhanden:
        befund.hinweise.append(
            "Es wurde keine GPU gefunden. Bei reiner CPU-Rechnung sind 1–3 Token/s "
            "für ein 14b-Modell normal – das ist kein Fehler der Software."
        )
        befund.empfehlungen += [
            "Kleineres Modell verwenden: `ollama pull qwen2.5:7b` (etwa 3× schneller) "
            "oder `qwen2.5:3b` für Routineaufgaben.",
            f"FAST_MODEL=qwen2.5:3b in der .env setzen – dann laufen Pläne und "
            f"Ordnerentscheidungen auf dem kleinen Modell, nur Inhalte auf '{cfg.llm_model}'.",
            f"NUM_CTX von {cfg.num_ctx} auf 4096 senken – halbiert die Zeit fürs "
            "Einlesen des Prompts.",
            "NOTE_TARGET_WORDS auf 250 senken – weniger erzeugte Token, direkt weniger Zeit.",
        ]
        if gpu.vram_gb == 0 and befund.ram_gb and befund.ram_gb < gesamtbedarf + 4:
            befund.hinweise.append(
                f"Mit {befund.ram_gb:.0f} GB RAM wird es bei {gesamtbedarf:.1f} GB Bedarf eng – "
                "dann lagert Windows aus und es wird nochmals deutlich langsamer."
            )
        return befund

    # GPU vorhanden - liegt das Modell auch darauf?
    if zustand.geladen and zustand.anteil_gpu < 0.9:
        befund.hinweise.append(
            f"Nur {zustand.anteil_gpu * 100:.0f} % des Modells liegen im VRAM. Der Rest "
            "rechnet auf der CPU, und der langsamste Teil bestimmt das Tempo – "
            "halb ausgelagert ist fast so langsam wie gar nicht ausgelagert."
        )
        passt = gpu.vram_gb - 1.0        # ~1 GB für Anzeige/Overhead
        if bedarf and bedarf > passt:
            kleiner = _kleineres_modell(cfg.llm_model, passt)
            if kleiner:
                befund.empfehlungen.append(
                    f"'{cfg.llm_model}' ({bedarf:.1f} GB) passt nicht in {gpu.vram_gb:.1f} GB "
                    f"VRAM. `ollama pull {kleiner}` passt vollständig und läuft dann "
                    f"um ein Vielfaches schneller."
                )
        befund.empfehlungen.append(
            f"NUM_CTX von {cfg.num_ctx} auf 4096 senken – der Kontext belegt VRAM, "
            "das sonst für Modellschichten frei wäre."
        )
        befund.empfehlungen.append(
            "NUM_GPU=99 in der .env erzwingt maximale Auslagerung. Passt das Modell "
            "nicht, bricht Ollama ab statt still auf die CPU zu wechseln – "
            "so merkst du es sofort."
        )
    elif zustand.geladen:
        befund.hinweise.append("Das Modell liegt vollständig im VRAM – das ist der Sollzustand.")
        if tempo and tempo < 10:
            befund.hinweise.append(
                "Trotzdem langsam: Dann ist die GPU selbst der Flaschenhals. Ein kleineres "
                "Modell bleibt die wirksamste Stellschraube."
            )
    else:
        befund.empfehlungen.append(
            "Modell ist nicht geladen. Einmal `/tempo` oder eine Frage ausführen, "
            "dann zeigt die Diagnose den echten Ladezustand."
        )

    if tempo and tempo < 5:
        befund.empfehlungen.append(
            "FAST_MODEL setzen (z. B. qwen2.5:3b): Pläne, Ordnerwahl und JSON-Antworten "
            "brauchen kein großes Modell. Das spart bei einem Struktur-Auftrag mit "
            "zehn Notizen schnell die Hälfte der Zeit."
        )
    return befund


def _entfernt_beurteilen(befund: Befund, cfg, tempo: Optional[float]) -> Befund:
    """Diagnose für einen Ollama-Server auf einem anderen Rechner.

    Was von hier aus feststellbar ist, ist begrenzt, und der Bericht sagt das
    auch. Feststellbar ist der Ladezustand über `/api/ps` - und der beantwortet
    die wichtigste Frage: Rechnet dort eine GPU, oder liegt das Modell im
    Arbeitsspeicher? Alles Übrige muss auf dem Server selbst nachgesehen werden,
    und dafür stehen die Befehle im Bericht.
    """
    auskunft = server_auskunft(cfg)
    befund.server_version = str(auskunft.get("version") or "")
    if not auskunft.get("erreichbar"):
        befund.hinweise.append(f"{cfg.host} antwortet nicht.")
        befund.empfehlungen += [
            "Läuft Ollama auf dem Server, und hört es auf allen Adressen? "
            "Standardmäßig hört es NUR auf 127.0.0.1 und ist von außen "
            "unerreichbar. Auf dem Server setzen: OLLAMA_HOST=0.0.0.0:11434",
            "Firewall auf dem Server: Port 11434 muss eingehend offen sein.",
            "OLLAMA_HOST_ALTERNATIVE in der .env eintragen, dann weicht Zeus "
            "automatisch auf das lokale Ollama aus.",
        ]
        return befund

    zustand = befund.zustand
    bedarf = modellgroesse(cfg.llm_model) or 0.0
    kontext_gb = cfg.num_ctx / 8192 * 0.6
    if bedarf:
        befund.hinweise.append(
            f"'{cfg.llm_model}' braucht etwa {bedarf:.1f} GB, dazu ~{kontext_gb:.1f} GB "
            f"für {cfg.num_ctx} Token Kontext.")

    if not zustand.geladen:
        befund.empfehlungen.append(
            "Es war kein Modell geladen, als nachgesehen wurde. Nach einer Frage "
            "oder direkt nach /tempo erneut ausführen – dann steht hier der "
            "echte Ladezustand.")
    elif zustand.gesamt_gb and zustand.anteil_gpu < 0.05:
        # Der eindeutigste Fall: nichts im VRAM, alles im RAM des Servers.
        befund.hinweise.append(
            "Auf dem Server liegt NICHTS im VRAM – das Modell rechnet dort "
            "vollständig auf der CPU. Das erklärt ein niedriges Tempo "
            "vollständig, und zwar unabhängig davon, wie stark der Server sonst ist: "
            "Beim Erzeugen jedes einzelnen Tokens muss das gesamte Modell einmal "
            "durch den Speicher gelesen werden. Token/s ≈ Speicherbandbreite ÷ "
            "Modellgröße – die Rechenleistung ist dabei fast bedeutungslos.")
        befund.empfehlungen += [
            "Auf dem Server prüfen: `nvidia-smi` (bzw. `rocm-smi`) und "
            "`ollama ps`. Zeigt ollama ps 0 % GPU, erkennt Ollama die Karte nicht "
            "– meist fehlen die CUDA-Treiber oder der Dienst läuft in einem "
            "Container ohne `--gpus all`.",
            f"Passt '{cfg.llm_model}' überhaupt ins VRAM? {bedarf:.1f} GB plus "
            f"{kontext_gb:.1f} GB Kontext müssen hineinpassen, sonst lagert "
            f"Ollama von selbst auf die CPU aus." if bedarf else
            "Modellgröße gegen das VRAM des Servers halten.",
            "Bis dahin: LLM_MODEL_ALTERNATIVE auf ein kleineres Modell setzen.",
        ]
    elif zustand.anteil_gpu < 0.9:
        befund.hinweise.append(
            f"Nur {zustand.anteil_gpu * 100:.0f} % des Modells liegen im VRAM des "
            "Servers. Der langsamste Teil bestimmt das Tempo – halb ausgelagert "
            "ist fast so langsam wie gar nicht ausgelagert.")
        befund.empfehlungen += [
            f"NUM_CTX von {cfg.num_ctx} senken – der Kontext belegt VRAM, das "
            f"sonst für Modellschichten frei wäre.",
            "Auf dem Server OLLAMA_KV_CACHE_TYPE=q8_0 und "
            "OLLAMA_FLASH_ATTENTION=1 setzen – halbiert den Speicher für den "
            "Kontext, ohne die Qualität spürbar zu senken.",
            "Sonst ein kleineres Modell, das vollständig hineinpasst.",
        ]
    else:
        befund.hinweise.append(
            "Das Modell liegt vollständig im VRAM des Servers – das ist der Sollzustand.")
        if tempo and tempo < 8:
            befund.hinweise.append(
                "Trotzdem langsam: Dann ist die Karte selbst der Engpass, oder "
                "OLLAMA_NUM_PARALLEL ist größer als 1 und der Kontextspeicher "
                "wird vervielfacht.")

    # Netz: bei einem entfernten Server kommt die Latenz jedes Aufrufs dazu.
    befund.hinweise.append(
        "Gemessen wird über das Netz. Bei kurzen Antworten fällt die Laufzeit "
        "der Verbindung ins Gewicht; bei langen Antworten ist sie bedeutungslos.")

    if cfg.fast_model:
        befund.hinweise.append(
            f"Routineaufgaben laufen auf '{cfg.fast_model}' – die zählen hier nicht mit.")
    return befund


def _kleineres_modell(aktuell: str, passt_in_gb: float) -> Optional[str]:
    basis = aktuell.split(":")[0]
    kandidaten = sorted(
        ((n, g) for n, g in MODELL_GROESSEN.items()
         if n.split(":")[0] == basis and g <= passt_in_gb),
        key=lambda kv: -kv[1],
    )
    return kandidaten[0][0] if kandidaten else None
