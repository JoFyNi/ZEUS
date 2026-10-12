# Was noch fehlt – Weg zu Industrietauglichkeit und Autonomie

Ehrliche Einordnung: Das System ist jetzt *zuverlässig im Schreiben*. Für
„industriereif und autonom" fehlen vor allem drei Dinge – **Messbarkeit**,
**Nachvollziehbarkeit** und **kontrollierte Selbstständigkeit**. Nach Wirkung
pro Aufwand sortiert.

---

## Stufe 1 – sofort, größter Hebel

### 1.1 Modellwechsel (kostet nichts außer Speicherplatz)
`llama3` ist die eigentliche Bremse: keine Werkzeug-Schnittstelle, schwaches
Deutsch, neigt zum Beschreiben statt Handeln. Der Planer fängt das ab, aber
freies Werkzeug-Denken bleibt eingeschränkt.

```bash
ollama pull qwen2.5:14b        # Minimum für Werkzeugbetrieb
ollama pull qwen2.5:32b        # wenn die Hardware es hergibt
ollama pull bge-m3             # deutlich besseres mehrsprachiges Embedding
```

`EMBEDDING_MODEL=bge-m3` bringt bei deutschen Fachnotizen spürbar bessere
Treffer als `nomic-embed-text`. Achtung: danach einmal `--reindex`.

### 1.2 Bewertungs-Set (das fehlende Fundament)
Ohne Messung ist jede Qualitätsaussage Gefühl. Konkret: eine Datei
`tests/goldset.yaml` mit 30–50 Fragen und den Notizen, die als Quelle korrekt
wären. Ein Skript misst Recall@5 und meldet Regressionen nach jeder Änderung an
Chunking, Modell oder Prompt.

Das ist die Voraussetzung dafür, überhaupt sagen zu können „die Änderung hat es
besser gemacht" – aktuell wäre das geraten.

### 1.3 Git-Versionierung der Vault
Backups im `.jarvis`-Ordner sind ein Notnagel. Ein `git init` in der Vault plus
automatischer Commit nach jedem Agentenlauf gibt echte Historie, Diffs, Blame
und Wiederherstellung auf jeden Stand – und ist die übliche Antwort auf
„Nachvollziehbarkeit" in industriellen Umgebungen.

```
Agentenlauf → git add -A → git commit -m "jarvis: <Auftrag>"
```

---

## Stufe 2 – Qualität der Antworten

### 2.1 Reranking
Vektorsuche liefert Kandidaten, ein Cross-Encoder sortiert sie um. Das ist der
größte Einzelgewinn bei RAG-Qualität nach dem Embedding-Modell. Ablauf: 24
Kandidaten holen, per Reranker auf die besten 5 eindampfen.

### 2.2 Fragen-Umschreibung (Query Rewriting)
Die Frage des Nutzers ist selten die beste Suchanfrage. Ein billiger
Vorverarbeitungsschritt formuliert 2–3 Suchvarianten und vereinigt die Treffer.
Fängt Fälle ab, in denen dein Wortlaut nicht dem Notiz-Wortlaut entspricht.

### 2.3 Gedächtnis über Sitzungen hinweg
Aktuell endet der Gesprächsverlauf mit dem Programm. Ein Sitzungsprotokoll als
Notiz in der Vault (`_Sitzungen/2026-08-20.md`) macht den Agenten über Wochen
anschlussfähig – und ist gleichzeitig Lernmaterial für ihn selbst.

---

## Stufe 3 – kontrollierte Autonomie

### 3.1 Vorschlagsordner statt direktem Schreiben
Für den unbeaufsichtigten Betrieb: Der Agent schreibt zunächst nach
`_Vorschläge/`. Du siehst die Notizen in Obsidian, und ein Befehl
(`/uebernehmen`) verschiebt die freigegebenen an ihren Zielort. Damit kann er
nachts arbeiten, ohne dass du morgens ungeprüften Inhalt in deiner Vault hast.

### 3.2 Zeitgesteuerte Läufe
`python jarvis_run.py --wartung` ist bereits dafür gebaut. Unter Windows über
die Aufgabenplanung, unter Linux per cron. Sinnvolle Aufträge:

| Rhythmus | Aufgabe |
|---|---|
| stündlich | Index abgleichen |
| täglich | tote Links reparieren, verwaiste Notizen melden |
| wöchentlich | Dubletten zusammenführen, Struktur-Vorschläge erzeugen |

### 3.3 Kurator-Agent
Ein zweiter, seltener Lauf mit anderem Auftrag: nicht schreiben, sondern die
Struktur beurteilen. „Welche Ordner sind überfüllt? Welche Notizen gehören
zusammengelegt? Wo fehlt eine Übersichtsnotiz?" Ergebnis als Bericht, nicht als
Änderung.

### 3.4 Wissenslücken-Erkennung
Der Agent protokolliert Fragen, die er *nicht* aus den Notizen beantworten
konnte. Daraus entsteht automatisch eine Liste offener Themen – die
Selbstlern-Funktion, die du ursprünglich wolltest, aber gesteuert statt
zufällig.

---

## Stufe 4 – Betrieb

### 4.1 Sperren gegen gleichzeitige Bearbeitung
Wenn Obsidian und der Agent dieselbe Datei anfassen, gewinnt der Letzte. Der
Agent liest zwar unmittelbar vor jedem Schreiben frisch von der Platte, aber
sauber wäre eine Prüfung der Änderungszeit plus Sperrdatei.

### 4.2 Kennzahlen und Alarm
Das JSONL-Protokoll unter `.jarvis/logs/` liegt bereits vor. Was fehlt: eine
Auswertung (Schreibvorgänge, Fehlerquote, Antwortzeiten) und ein Alarm, wenn
die Fehlerquote steigt oder ein Wartungslauf ausbleibt.

### 4.3 Inhaltsprüfung vor dem Schreiben
Für ein Firmenumfeld: Prüfung auf versehentlich eingefügte Zugangsdaten,
API-Schlüssel oder personenbezogene Daten, bevor etwas in der Vault landet.

### 4.4 Automatische Tests im Durchlauf
Die drei Testdateien laufen bisher von Hand. In eine CI-Pipeline gehängt (oder
als Pre-Commit-Hook), verhindern sie genau die Art von Rückschritt, die dir
zweimal aufgefallen ist.

---

## Was ich zuerst machen würde

1. **`qwen2.5:14b` + `bge-m3`** installieren, `--reindex`. Eine Stunde Arbeit,
   größter spürbarer Effekt.
2. **Git in der Vault.** Zwei Zeilen Code, löst Nachvollziehbarkeit dauerhaft.
3. **Bewertungs-Set.** Danach ist jede weitere Verbesserung überprüfbar statt
   Geschmackssache.

Alles darunter lohnt sich erst, wenn diese drei stehen.
