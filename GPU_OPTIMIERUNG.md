# 1,2 Token/s beheben – was wirklich hilft

Bei 1,2 Token/s dauert eine Notiz mit 350 Wörtern rund **7 Minuten**, ein
Struktur-Auftrag mit zehn Notizen über eine Stunde. Das ist der Flaschenhals des
ganzen Systems – alles andere ist dagegen zweitrangig.

## Zuerst: eine Fehlannahme ausräumen

> „Verwendung von Frameworks wie TensorFlow oder PyTorch zur Unterstützung von
> GPU-Berechnungen."

**Ollama nutzt weder TensorFlow noch PyTorch.** Darunter liegt llama.cpp/GGML mit
eigenen CUDA-, ROCm- und Metal-Kerneln. Ein `pip install torch` ändert an der
Geschwindigkeit exakt nichts – es lädt nur 2,5 GB herunter.

Was tatsächlich zählt, sind drei Dinge:

1. **Sieht Ollama die GPU?** (Treiber + CUDA-Laufzeit)
2. **Wie viele Schichten passen ins VRAM?** Passt nur ein Teil, rechnet der Rest
   auf der CPU. Und der langsamste Teil bestimmt das Tempo – halb ausgelagert ist
   fast so langsam wie gar nicht ausgelagert.
3. **Welche Quantisierung?** q4_K_M braucht etwa die Hälfte von q8_0.

## Schritt 1: Messen statt raten

```
python jarvis_run.py --hardware
```

bzw. `/hardware` im Chat. Das liest über `/api/ps` aus, wie viel des Modells
**wirklich** im VRAM liegt, erkennt die GPU über `nvidia-smi` und rechnet aus, was
bei deiner VRAM-Menge hineinpasst. Die Ausgabe nennt konkrete nächste Schritte
statt allgemeiner Ratschläge.

Die entscheidende Zeile ist diese:

```
Modell 'qwen2.5:14b': 9.2 GB geladen, davon 3.1 GB im VRAM → 34 % auf der GPU
```

34 % bedeutet: Zwei Drittel rechnen auf der CPU. Genau daher kommen die 1,2 Token/s.

## Schritt 2: Was zu welchem Befund passt

### Keine GPU erkannt

Dann sind 1–3 Token/s für ein 14b-Modell **normal**, kein Softwarefehler. Wirksam,
absteigend nach Wirkung:

| Maßnahme | Effekt |
|---|---|
| `LLM_MODEL=qwen2.5:7b` | etwa 2× schneller |
| `FAST_MODEL=qwen2.5:3b` | Pläne und Ordnerentscheidungen laufen auf dem kleinen Modell |
| `NUM_CTX=4096` statt 8192 | halbiert die Zeit fürs Einlesen des Prompts |
| `NOTE_TARGET_WORDS=250` | weniger erzeugte Token = direkt weniger Zeit |

### GPU da, aber Modell nur teilweise im VRAM

Das ist der häufigste und ärgerlichste Fall, weil er sich wie ein Softwareproblem
anfühlt. Rechnung: `qwen2.5:14b` braucht ~9 GB, dazu ~0,6 GB je 8k Kontext.

| VRAM | Passt vollständig |
|---|---|
| 6 GB | `qwen2.5:3b`, knapp `qwen2.5:7b` mit kleinem Kontext |
| 8 GB | `qwen2.5:7b` bequem |
| 12 GB | `qwen2.5:14b` mit `NUM_CTX=4096` |
| 16 GB+ | `qwen2.5:14b` mit vollem Kontext |

```env
NUM_GPU=99
```

erzwingt maximale Auslagerung. Passt das Modell nicht, **bricht Ollama ab statt
still auf die CPU zu wechseln** – so merkst du das Problem sofort, statt dich über
zehnfache Laufzeit zu wundern.

### GPU da, Modell vollständig drin, trotzdem langsam

Dann ist die GPU selbst der Flaschenhals. Ein kleineres Modell bleibt die
wirksamste Stellschraube.

## Schritt 3: Zweitmodell einrichten

Der größte Gewinn ohne Hardwarewechsel. Pläne, Ordnerwahl, JSON-Entscheidungen und
Lektionsformulierungen brauchen **kein** großes Modell:

```bash
ollama pull qwen2.5:3b
```

```env
LLM_MODEL=qwen2.5:14b      # schreibt die Inhalte
FAST_MODEL=qwen2.5:3b      # plant, entscheidet, sortiert
```

Bei einem Auftrag mit zehn Notizen entfallen so mehrere Routineaufrufe auf ein
Modell, das fünfmal schneller ist. `/tempo` zeigt beide getrennt.

## Schritt 4: Erwartung kalibrieren

Nach den Änderungen `/tempo` erneut ausführen. Realistische Werte:

| Aufbau | Token/s | Eine Notiz (350 Wörter) |
|---|---|---|
| 14b auf der CPU | 1–2 | 5–8 min |
| 7b auf der CPU | 3–5 | 2–3 min |
| 7b auf 8 GB GPU | 30–50 | 10–15 s |
| 14b auf 12 GB GPU | 20–35 | 15–25 s |

Der Sprung von CPU auf GPU ist ein Faktor 10–20. Kein Softwaretrick kommt dagegen an.

## Was das System schon selbst tut

- **Streaming überall**, damit Wartezeit sichtbar ist statt als Hänger zu wirken
- **`KEEP_ALIVE` + Warmlauf**, damit das Laden nicht bei jeder Anfrage anfällt
- **`NUM_PREDICT`**, damit Wiederholungsschleifen nicht den Kontext vollschreiben
- **Prompt-Budget aus `NUM_CTX`**, damit nichts stillschweigend abgeschnitten wird
- **`NOTE_TARGET_WORDS`** steuert die erzeugte Länge – der direkteste Zeithebel
- **`FAST_MODEL`** hält Routinearbeit vom großen Modell fern

## Dokumentation der Änderung

Geänderte Dateien für diesen Themenbereich:

| Datei | Zweck |
|---|---|
| `jarvis/hardware.py` | GPU-Erkennung, `/api/ps`-Ladezustand, Empfehlungen |
| `jarvis/llm.py` | `num_gpu`, `keep_alive`, Modellwahl je Aufruf, Streaming |
| `jarvis/config.py` | `FAST_MODEL`, `NUM_GPU`, `NOTE_TARGET_WORDS`, Prompt-Budget |
| `jarvis/planner.py` | Pläne laufen über das schnelle Modell |
| `jarvis/ui.py` | `/tempo` mit Ursachenanalyse, `/hardware` |
