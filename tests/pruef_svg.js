// Prüft den SVG-Weissfilter der Oberfläche ohne Browser: Der Abschnitt wird
// aus der HTML-Datei geschnitten und in einer nackten Funktion ausgeführt.
const fs = require("fs");
const path = require("path");
const html = fs.readFileSync(path.join(__dirname, "..", "jarvis", "web", "oberflaeche.html"), "utf8");

const anfang = html.indexOf("/* Ein Bild aus");
const ende = html.indexOf("function markdown(roh){");
if (anfang < 0 || ende < 0) { console.log("Abschnitt nicht gefunden"); process.exit(1); }
const quelle = html.slice(anfang, ende);

const bauen = new Function(quelle + "\nreturn svgSaeubern;");
const svgSaeubern = bauen();

const gut = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
          + '<rect x="1" y="1" width="9" height="9" fill="#151b26"/>'
          + '<text x="2" y="5">Connector</text></svg>';
const faelle = [
  ["sauberes SVG angenommen", svgSaeubern(gut) !== null],
  ["onload-Attribut abgelehnt", svgSaeubern('<svg onload="alert(1)"><rect/></svg>') === null],
  ["script-Element abgelehnt",
   svgSaeubern("<svg><scr" + "ipt>alert(2)</scr" + "ipt></svg>") === null],
  ["foreignObject/iframe abgelehnt",
   svgSaeubern("<svg><foreignObject><iframe/></foreignObject></svg>") === null],
  ["javascript:-Verweis abgelehnt",
   svgSaeubern('<svg><a href="javascript:alert(1)"><rect/></a></svg>') === null],
  ["Nicht-SVG abgelehnt", svgSaeubern("<div>x</div>") === null],
  ["Riesenblock abgelehnt", svgSaeubern("<svg>" + "x".repeat(500000) + "</svg>") === null],
];

let fehler = 0;
for (const [name, ok] of faelle) {
  console.log((ok ? "✅ " : "❌ ") + name);
  if (!ok) fehler++;
}
console.log(fehler ? fehler + " fehlgeschlagen" : "Alle Tests bestanden.");
process.exit(fehler ? 1 : 0);
