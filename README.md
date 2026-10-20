# Zeitung in großer Schrift

Macht das E-Paper (PDF) der Süddeutschen Zeitung für Menschen mit Sehschwäche lesbar:
Artikel einzeln, in sehr großer, gut lesbarer Schrift (Atkinson Hyperlegible), mit einstellbarer Größe
und kontrastreichen Farben.

## Benutzung im Browser

1. Die Zeitung wie gewohnt als PDF herunterladen.
2. Die Website öffnen und auf **„Zeitung öffnen“** klicken (oder die PDF ins Fenster ziehen).

Die Umwandlung läuft komplett im Browser: [Pyodide](https://pyodide.org) führt `zeitung_reader.py`
mit PyMuPDF direkt auf dem eigenen Computer aus. Die PDF wird nirgendwohin hochgeladen.
Beim ersten Öffnen werden ca. 15–30 MB (Python + PyMuPDF) von jsDelivr geladen, danach aus dem Browser-Cache.

## Benutzung am Computer (Python)

```
pip install pymupdf
python3 zeitung_reader.py SueddeutscheZeitung-2026-10-12.pdf   # oder ein Ordner mit PDFs
```

Erzeugt eine selbstständige HTML-Datei neben der PDF.

## Dateien

- `zeitung_reader.py` – Artikelerkennung (PDF → Artikel) und HTML-Erzeugung
- `reader_template.html` – Leseansicht
- `index.html`, `app.js` – Startseite für den Browser

Dieses Repository enthält keine Zeitungsinhalte.
