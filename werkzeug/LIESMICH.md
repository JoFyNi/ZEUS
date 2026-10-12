# Die Skripte, mit denen der Umbau vom 04.09.2026 gemacht wurde

Sie liegen hier als **Beleg**, nicht als Werkzeug für den Alltag. Wer
nachvollziehen will, wie `tools.py` und `agent.py` aufgeteilt wurden, ohne dass
dabei etwas verlorenging, findet hier das Verfahren:

| Datei | Was sie tut |
|---|---|
| `teile_tools.py` | Prueft die geplante Aufteilung von `tools.py` gegen den Syntaxbaum: jede Methode genau einmal, keine ueberlappenden Zeilenbereiche |
| `schreibe_paket.py` | Schreibt daraus `jarvis/werkzeuge/` |
| `teile_agent.py` | Dasselbe fuer `agent.py` |
| `schreibe_gespraech.py` | Schreibt daraus `jarvis/gespraech/` |
| `importe_kuerzen2.py` | Entfernt ungenutzte Importe, ueber den Syntaxbaum statt per Suchmuster |

Die Pfade darin sind absolut und zeigen auf die Umgebung, in der der Umbau
lief. Zum erneuten Ausfuehren muessten sie angepasst werden - das ist aber
nicht vorgesehen: Der Umbau ist gemacht, und ein zweiter Lauf wuerde die
seither von Hand hinzugekommenen Zeilen ueberschreiben.

Der Fehler, den diese Skripte selbst hatten und der die Nachpruefung
gerechtfertigt hat, steht in `block()` dokumentiert: ein Fehler um eins zog die
Zeile `class JarvisAgent:` in den `__init__`-Block und erzeugte eine zweite,
leere Klasse, die alle Mixins verdeckte.
