"""Convert a Süddeutsche Zeitung e-paper PDF into a large-print HTML reader.

Usage:
    python zeitung_reader.py Zeitung/SueddeutscheZeitung-2026-10-12.pdf
    python zeitung_reader.py Zeitung/            # every PDF in the folder

For each PDF a self-contained HTML file is written next to it (or into --out).
The page lists the main headlines with their page number; clicking one opens
the article reflowed into a single column with adjustable, very large text.

Article detection relies on the SZ layout: body text is set in "SZoText" at
~9 pt, headlines in larger "SZ..." fonts. Each body line is assigned to the
nearest headline above it in the same columns.
"""
import argparse
import html
import json
import re
from pathlib import Path

import fitz  # PyMuPDF

HEADER_LIMIT = 100        # y-coordinate (pt) above which the running page header sits
MIN_ARTICLE_CHARS = 600   # shorter "articles" are teasers, boxes or ads
HEADLINE_MIN_SIZE = 14
HEADLINE_EXCLUDED_FONTS = ("SZSansDigital-Black", "SZoSymbol")
SPACE_GAP = 0.06          # char gap (relative to font size) that counts as a missing space
SIGNATURE_MAX_SIZE = 8.5  # author signatures at the end of an article are set in ~7.5 pt sans


def font_name(span):
    return span["font"].split("-OV")[0]


def line_text(spans):
    """Rebuild text from characters; the PDF often omits spaces in justified lines.

    Letter-spacing (bylines, photo credits) is estimated per font, because such text
    often shares a line with normally set text.
    """
    chars = [(c, s["font"]) for s in spans for c in s["chars"]]
    gaps = {}
    for (a, fa), (b, fb) in zip(chars, chars[1:]):
        if fa == fb:
            gaps.setdefault(fa, []).append(b["bbox"][0] - a["bbox"][2])
    tracking = {f: max(0, sorted(g)[len(g) // 2]) for f, g in gaps.items()}
    out, prev = [], None
    for span in spans:
        for ch in span["chars"]:
            if prev is not None and ch["c"] != " " and out and out[-1] != " ":
                if ch["bbox"][0] - prev["bbox"][2] > tracking.get(span["font"], 0) + SPACE_GAP * span["size"]:
                    out.append(" ")
            out.append(ch["c"])
            prev = ch
    return re.sub(r"\s+", " ", "".join(out)).strip()


DATE_RE = re.compile(r"(Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag)(/Sonntag)?,\s*[\d./]+\s*\w+\s*\d{4},?")
EDITION_CODES = {"HM1", "HBG", "PSM", "PML"}   # regional edition markers in the header


def page_header(lines):
    """Printed page label ("5", "R2", "12/13") and section name from the running header."""
    text = " ".join(l["text"] for l in lines)
    if "DEFGH" not in text:   # no regular running header (front page, ads)
        return None, ""
    text = DATE_RE.sub(" ", re.sub(r"DEFGH|Nr\.\s*\d+,?", " ", text))
    label, kept = None, []
    for w in text.split():
        if re.fullmatch(r"R?\d+(/\d+)?", w):
            label = label or w
        elif w not in EDITION_CODES and w not in kept:
            kept.append(w)
    small = {"DES", "DER", "DIE", "DAS", "FÜR", "UND"}
    section = " ".join(w if len(w) <= 2 or not w.isupper() else w.lower() if i and w in small else w.capitalize()
                       for i, w in enumerate(kept))
    return label, section


def classify(line):
    font, size, text = line["font"], line["size"], line["text"]
    if line["y0"] < HEADER_LIMIT:
        return "header"
    if size >= HEADLINE_MIN_SIZE and font.startswith("SZ") and not font.startswith(HEADLINE_EXCLUDED_FONTS) \
            and len(text) >= 4 and re.search(r"[a-zäöü]", text):
        return "headline"
    if font.startswith("SZoText") and 8.4 <= size <= 9.6:
        return "body"
    if font.startswith("SZSansDigital-Black") and size < 10 and re.match(r"(Interview )?[Vv]on ", text):
        return "byline"
    if font.startswith("SZSansDigital") and 9.5 <= size < 10.5:
        return "subhead"
    if font.startswith(("SZSansDigital", "SZoSerif-RegularSub")) and 10.5 <= size < HEADLINE_MIN_SIZE:
        return "subtitle"
    return "other"


def extract_lines(page):
    """All text lines on a page with geometry, dominant font and drop caps split off."""
    lines, dropcaps = [], []
    for block in page.get_text("rawdict")["blocks"]:
        if block["type"] != 0:
            continue
        blines = []
        for raw_spans in merge_baselines(block["lines"]):
            spans = []
            for s in raw_spans:
                txt = "".join(c["c"] for c in s["chars"]).strip()
                if not txt:
                    continue
                if len(txt) == 1 and txt.isalpha() and s["size"] >= 25:
                    dropcaps.append({"c": txt, "bbox": s["bbox"]})
                else:
                    spans.append(s)
            if spans:
                spans, signature = split_signature(spans)
                blines.append(make_line(spans))
                blines[-1]["signature"] = line_text(signature) if signature else ""
                blines[-1]["above"] = blines[-2] if len(blines) > 1 else None
        if not blines:
            continue
        col_x = min(l["x0"] for l in blines)
        caption = all(l["italic"] > 0.8 for l in blines) or any("FOTO:" in l["text"] for l in blines)
        for l in blines:
            l["col_x"] = col_x
            l["kind"] = classify(l)
            if caption and l["kind"] == "body":
                l["kind"] = "caption"
        lines.extend(blines)
    attach_dropcaps(lines, dropcaps)
    return lines


def split_signature(spans):
    """Split off a trailing author signature ("Lea Hampel", small sans) set on the last body line's baseline.

    Returns (line spans, signature spans); otherwise the signature could outweigh the body font.
    """
    for i in range(1, len(spans)):
        if font_name(spans[i - 1]).startswith("SZoText") and \
                all(font_name(s).startswith("SZSansDigital") and s["size"] < SIGNATURE_MAX_SIZE for s in spans[i:]):
            return spans[:i], spans[i:]
    return spans, []


def make_line(spans):
    """Line record with text, geometry and dominant font (weighted by character count)."""
    weights = {}
    for s in spans:
        weights[font_name(s)] = weights.get(font_name(s), 0) + len(s["chars"])
    n = sum(weights.values())
    return {
        "text": line_text(spans), "font": max(weights, key=weights.get),
        "size": max(s["size"] for s in spans),
        "x0": min(s["bbox"][0] for s in spans), "x1": max(s["bbox"][2] for s in spans),
        "y0": min(s["bbox"][1] for s in spans), "y1": max(s["bbox"][3] for s in spans),
        "italic": sum(w for f, w in weights.items() if "Ital" in f) / n,
        "bold": sum(w for f, w in weights.items() if "Bold" in f or "Black" in f) / n,
    }


def merge_baselines(raw_lines):
    """MuPDF sometimes splits one printed line into several (e.g. word-by-word bylines)."""
    merged = []
    for l in raw_lines:
        if merged and abs(merged[-1]["bbox"][3] - l["bbox"][3]) < 1 and l["bbox"][0] >= merged[-1]["bbox"][2] - 1:
            merged[-1]["spans"].extend(l["spans"])
            merged[-1]["bbox"] = (merged[-1]["bbox"][0], merged[-1]["bbox"][1], l["bbox"][2], l["bbox"][3])
        else:
            merged.append({"spans": list(l["spans"]), "bbox": l["bbox"]})
    return [m["spans"] for m in merged]


def attach_dropcaps(lines, dropcaps):
    """Prepend each oversized initial to the first body line right of it."""
    for dc in dropcaps:
        x0, y0, x1, _ = dc["bbox"]
        near = [l for l in lines if l["kind"] == "body" and 0 <= l["x0"] - x1 < 20 and y0 - 5 <= l["y0"] <= y0 + 15]
        if near:
            first = min(near, key=lambda l: l["y0"])
            first["text"] = dc["c"] + first["text"]


def overlaps(a0, a1, b0, b1):
    return min(a1, b1) - max(a0, b0) > 0


def group_headlines(lines):
    """Merge multi-line headlines and attach the subtitle lines underneath."""
    groups = []
    for l in sorted((l for l in lines if l["kind"] == "headline"), key=lambda l: l["y0"]):
        for g in groups:
            last = g["lines"][-1]
            if 0.6 < l["size"] / last["size"] < 1.6 \
                    and -l["size"] / 2 <= l["y0"] - last["y1"] < l["size"] and overlaps(g["x0"], g["x1"], l["x0"], l["x1"]):
                g["lines"].append(l)
                g["x0"], g["x1"], g["y1"] = min(g["x0"], l["x0"]), max(g["x1"], l["x1"]), l["y1"]
                break
        else:
            groups.append({"lines": [l], "sub": [], "x0": l["x0"], "x1": l["x1"],
                           "y0": l["y0"], "y1": l["y1"], "size": l["size"], "body": []})
    # a much smaller "headline" right under a big one is really its subtitle
    for g in list(groups):
        parents = [p for p in groups if p is not g and 0 <= g["y0"] - p["y1"] < 40
                   and g["size"] < 0.7 * p["size"] and overlaps(p["x0"], p["x1"], g["x0"], g["x1"])]
        if parents:
            p = parents[0]
            p["sub"].extend(g["lines"])
            p["x0"], p["x1"], p["y1"] = min(p["x0"], g["x0"]), max(p["x1"], g["x1"]), max(p["y1"], g["y1"])
            groups.remove(g)
    for l in sorted((l for l in lines if l["kind"] == "subtitle"), key=lambda l: l["y0"]):
        above = [g for g in groups if 0 <= l["y0"] - g["y1"] < 60 and overlaps(g["x0"], g["x1"], l["x0"], l["x1"])]
        if above:
            g = max(above, key=lambda g: g["y1"])
            g["sub"].append(l)
            g["x0"], g["x1"], g["y1"] = min(g["x0"], l["x0"]), max(g["x1"], l["x1"]), l["y1"]
    return groups


def assign_body(lines, groups):
    """Give each text line to the closest headline above it in the same columns."""
    if not groups:
        return
    main = max(groups, key=lambda g: (g["size"], g["x1"] - g["x0"]))
    for l in lines:
        if l["kind"] not in ("body", "subhead", "byline"):
            continue
        if l["signature"] and l["above"] and "target" in l["above"]:
            # a short closing line follows the line above it; its own width may match another column
            l["above"]["target"]["body"].append(l)
            continue
        above = [g for g in groups if g["y0"] <= l["y0"]]
        same_cols = [g for g in above if overlaps(g["x0"], g["x1"], l["x0"], l["x1"])]
        if same_cols:
            target = max(same_cols, key=lambda g: g["y1"])
        elif above:
            target = max(above, key=lambda g: g["y1"])
        else:
            target = main   # text above every headline (e.g. Feuilleton openers) belongs to the lead story
        target["body"].append(l)
        l["target"] = target


def join_lines(prev, nxt):
    """Join two consecutive lines, undoing end-of-line hyphenation."""
    if re.search(r"[A-Za-zÄÖÜäöüß]-$", prev):
        return prev[:-1] + nxt if nxt[:1].islower() else prev + nxt
    return prev + " " + nxt


def build_paragraphs(body):
    """Sort lines into reading order (column by column) and merge them into paragraphs."""
    body.sort(key=lambda l: (round(l["col_x"] / 40), l["y0"]))
    paras, author = [], None
    for l in body:
        if l["kind"] == "byline":
            author = re.sub(r"^(Interview )?[Vv]on ", "", l["text"])
            continue
        kind = "h" if l["kind"] == "subhead" else ("q" if l["bold"] > 0.8 else "p")
        indented = l["x0"] - l["col_x"] > 3
        if paras and paras[-1]["type"] == kind and not indented:
            paras[-1]["text"] = join_lines(paras[-1]["text"], l["text"])
        else:
            paras.append({"type": kind, "text": l["text"]})
        if l["signature"]:
            paras[-1]["text"] += " " + l["signature"]
    return paras, author


def parse_pdf(path):
    doc = fitz.open(path)
    articles = []
    for pno, page in enumerate(doc, start=1):
        lines = extract_lines(page)
        label, section = page_header([l for l in lines if l["kind"] == "header"])
        if pno == 1:
            label, section = "1", "Titelseite"
        groups = group_headlines(lines)
        assign_body(lines, groups)
        for g in sorted(groups, key=lambda g: (g["y0"], g["x0"])):
            paras, author = build_paragraphs(g["body"])
            if sum(len(p["text"]) for p in paras) < MIN_ARTICLE_CHARS:
                continue
            headline = " ".join(l["text"] for l in g["lines"])
            subtitle = " ".join(l["text"] for l in g["sub"])
            articles.append({"page": label or str(pno), "pdf_page": pno, "section": section, "headline": headline,
                             "subtitle": subtitle, "author": author, "paragraphs": paras})
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", Path(path).stem)
    date = f"{m[3]}.{m[2]}.{m[1]}" if m else Path(path).stem
    return {"title": "Süddeutsche Zeitung", "date": date, "articles": articles}


def render_html(edition):
    template = (Path(__file__).parent / "reader_template.html").read_text(encoding="utf-8")
    data = json.dumps(edition, ensure_ascii=False).replace("</", "<\\/")
    return (template.replace("{{TITLE}}", html.escape(f"{edition['title']} – {edition['date']}"))
                    .replace("{{DATA}}", data))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path, help="PDF file or folder containing PDFs")
    ap.add_argument("--out", type=Path, help="output folder (default: next to the PDF)")
    args = ap.parse_args()
    pdfs = sorted(args.source.glob("*.pdf")) if args.source.is_dir() else [args.source]
    for pdf in pdfs:
        edition = parse_pdf(pdf)
        out_dir = args.out or pdf.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        target = out_dir / (pdf.stem + ".html")
        target.write_text(render_html(edition), encoding="utf-8")
        print(f"{pdf.name}: {len(edition['articles'])} Artikel -> {target}")


if __name__ == "__main__":
    main()
