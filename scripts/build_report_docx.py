#!/usr/bin/env python3
"""Convert the Markdown progress report into a formatted Word document.

  build_report_docx.py [--src docs/checkpoint2_draft.md] OUT.docx

Supported Markdown subset (what docs/checkpoint2_draft.md uses):
  # Title                  first one only; following lines up to the first
                           ## heading are centered front matter (authors, ...)
  ## / ### / ####          Word Heading 1 / 2 / 3 (section numbers kept as text)
  paragraphs               **bold**, *italic*, `code`, [text](url)
  | pipe | tables |        Word tables, bold header row, numeric cells
                           right-aligned; a "Table ..." line just before or
                           after, or a "Cells:"/"Note:" line just after,
                           becomes an italic 10 pt table note
  Figure: a.png (label); b.png (label) [caption prose]
                           images relative to the repo root, one caption
                           "Figure N. label; label ..." (file stem when no
                           label); a following line that starts lowercase or
                           with "Left"/"Right" is appended to the caption
  - item / 1. item         List Bullet / List Number
  > quote                  indented italic paragraph
  [[PENDING: ...]], [[RESULT: ...]]   highlighted yellow and reported
  ## References            each paragraph a hanging-indent reference

Prints warnings (missing figures, pending markers) and a summary line.
Exit status is 0 even with warnings; the summary says how many there were.
"""
import argparse, os, re, subprocess, sys, tempfile

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_COLOR_INDEX
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BODY_FONT = "Times New Roman"
MONO_FONT = "Courier New"
BODY_PT = 11
TABLE_PT = 9.5
TABLE_WIDE_PT = 9
TEXT_WIDTH_IN = 6.5          # letter, 1 inch margins
FIG_WIDTH_IN = 6.0
FIG_PAIR_WIDTH_IN = 3.1
FIG_MAX_HEIGHT_IN = 8.0
PAIR_MAX_ASPECT = 1.25      # width/height; wider pairs are stacked

MARKER_RE = re.compile(r"\[\[(PENDING|RESULT):[^\]]*\]\]")

warnings = []


def warn(msg):
    warnings.append(msg)
    print("WARNING: " + msg, file=sys.stderr)


# --------------------------------------------------------------------------
# Low-level formatting helpers

def set_run_font(run, name=None, size=None, bold=None, italic=None):
    if name:
        run.font.name = name
        rpr = run._element.get_or_add_rPr()
        rfonts = rpr.find(qn("w:rFonts"))
        if rfonts is None:
            rfonts = OxmlElement("w:rFonts")
            rpr.insert(0, rfonts)
        for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
            rfonts.set(qn(attr), name)
    if size is not None:
        run.font.size = Pt(size)
    if bold is not None:
        run.bold = bold
    if italic is not None:
        run.italic = italic


def set_style_font(style, name, size=None, bold=None, italic=None, color=None):
    style.font.name = name
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.insert(0, rfonts)
    for attr in list(rfonts.attrib):
        if attr.lower().endswith("theme"):          # theme fonts override explicit ones
            del rfonts.attrib[attr]
    for attr in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(attr), name)
    if size is not None:
        style.font.size = Pt(size)
    if bold is not None:
        style.font.bold = bold
    if italic is not None:
        style.font.italic = italic
    if color is not None:
        style.font.color.rgb = color


def add_page_number_footer(section):
    footer = section.footer
    p = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run()
    set_run_font(run, BODY_FONT, 10)
    for kind, text in (("begin", None), (None, "PAGE"), ("separate", None),
                       (None, None), ("end", None)):
        if kind:
            fc = OxmlElement("w:fldChar")
            fc.set(qn("w:fldCharType"), kind)
            run._element.append(fc)
        elif text:
            it = OxmlElement("w:instrText")
            it.set(qn("xml:space"), "preserve")
            it.text = text
            run._element.append(it)
        else:
            t = OxmlElement("w:t")
            t.text = "1"
            run._element.append(t)


def shade_cell(cell, fill):
    tcpr = cell._tc.get_or_add_tcPr()
    shd = OxmlElement("w:shd")
    shd.set(qn("w:val"), "clear")
    shd.set(qn("w:color"), "auto")
    shd.set(qn("w:fill"), fill)
    tcpr.append(shd)


def set_cell_width(cell, inches):
    tcpr = cell._tc.get_or_add_tcPr()
    tcw = tcpr.find(qn("w:tcW"))
    if tcw is None:
        tcw = OxmlElement("w:tcW")
        tcpr.insert(0, tcw)
    tcw.set(qn("w:w"), str(int(round(inches * 1440))))
    tcw.set(qn("w:type"), "dxa")


def set_table_widths(table, widths_in):
    tbl = table._tbl
    grid = tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), widths_in):
        gc.set(qn("w:w"), str(int(round(w * 1440))))
    tblpr = tbl.tblPr
    tblw = tblpr.find(qn("w:tblW"))
    if tblw is None:
        tblw = OxmlElement("w:tblW")
        tblpr.append(tblw)
    tblw.set(qn("w:w"), str(int(round(sum(widths_in) * 1440))))
    tblw.set(qn("w:type"), "dxa")
    for row in table.rows:
        for cell, w in zip(row.cells, widths_in):
            set_cell_width(cell, w)


def mark_header_row(row):
    trpr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trpr.append(el)


def cant_split(row):
    trpr = row._tr.get_or_add_trPr()
    trpr.append(OxmlElement("w:cantSplit"))


def remove_table_borders(table):
    tblpr = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:" + edge)
        e.set(qn("w:val"), "nil")
        borders.append(e)
    tblpr.append(borders)


# --------------------------------------------------------------------------
# Inline Markdown

INLINE_RE = re.compile(
    r"(?P<marker>\[\[(?:PENDING|RESULT):[^\]]*\]\])"
    r"|(?P<code>`[^`]+`)"
    r"|(?P<link>\[(?P<ltext>[^\]]+)\]\((?P<lurl>[^)\s]+)\))"
    r"|(?P<bold>\*\*(?P<btext>.+?)\*\*)"
    r"|(?P<ital>(?<![\w*])\*(?P<itext>[^*\s](?:[^*]*[^*\s])?)\*(?![\w*]))"
)


def typography(text):
    # The draft uses no em dashes; a literal "--" becomes an en dash.
    return text.replace("--", "–")


SUP_RE = re.compile(r"(?<=\d)\^(\d+|\{[^}]*\})")
NUM_HYPHEN_RE = re.compile(r"(?<=\d)-(?=\d)")


def emit_text(par, text, size, bold, italic, font, nobreak=False):
    """Plain text as runs: "10^4" becomes a superscript, and with `nobreak`
    a hyphen between digits becomes a Word no-break hyphen (table ranges)."""
    text = typography(text)
    pos = 0
    pieces = []
    for m in SUP_RE.finditer(text):
        pieces.append((text[pos:m.start()], False))
        pieces.append((m.group(1).strip("{}"), True))
        pos = m.end()
    pieces.append((text[pos:], False))
    for chunk, sup in pieces:
        if not chunk:
            continue
        r = par.add_run()
        set_run_font(r, font, size, bold, italic)
        if sup:
            r.font.superscript = True
        parts = NUM_HYPHEN_RE.split(chunk) if nobreak else [chunk]
        for k, part in enumerate(parts):
            if k:
                r._element.append(OxmlElement("w:noBreakHyphen"))
            if part:
                t = OxmlElement("w:t")
                t.set(qn("xml:space"), "preserve")
                t.text = part
                r._element.append(t)


def add_inline(par, text, size=None, bold=None, italic=None, font=BODY_FONT,
               nobreak=False):
    """Append Markdown-formatted text to paragraph `par` as runs. bold and
    italic None inherit from the paragraph style."""
    pos = 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            emit_text(par, text[pos:m.start()], size, bold, italic, font, nobreak)
        if m.group("marker"):
            r = par.add_run(m.group("marker"))
            set_run_font(r, font, size, True, italic)
            r.font.highlight_color = WD_COLOR_INDEX.YELLOW
        elif m.group("code"):
            r = par.add_run(m.group("code")[1:-1])
            set_run_font(r, MONO_FONT, min(10, size or 10), bold, italic)
        elif m.group("link"):
            ltext, url = m.group("ltext"), m.group("lurl")
            add_inline(par, ltext, size, bold, italic, font, nobreak)
            if url not in ltext:
                r = par.add_run(" (" + url + ")")
                set_run_font(r, font, size, bold, italic)
        elif m.group("bold"):
            add_inline(par, m.group("btext"), size, True, italic, font, nobreak)
        elif m.group("ital"):
            add_inline(par, m.group("itext"), size, bold, True, font, nobreak)
        pos = m.end()
    if pos < len(text):
        emit_text(par, text[pos:], size, bold, italic, font, nobreak)


def plain_text(text):
    """Text with Markdown syntax removed (for word counts and widths)."""
    text = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"(?<![\w*])\*([^*\s](?:[^*]*[^*\s])?)\*(?![\w*])", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = SUP_RE.sub(lambda m: m.group(1).strip("{}"), text)
    return text


UNIT_RE = re.compile(r"(?<=[\d\])]) (?=(?:us|ms|s|Gbps|KB|MB|GB|seeds?)\b|\[)")


def glue_numbers(cell):
    """Keep a value with its unit and its [range] on one line (NBSP)."""
    return UNIT_RE.sub(" ", cell)


# --------------------------------------------------------------------------
# Block parsing

def parse_blocks(lines):
    """Yield (kind, payload, lineno) blocks."""
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        line = lines[i].rstrip("\n")
        s = line.strip()
        if not s:
            i += 1
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            blocks.append(("heading", (len(m.group(1)), m.group(2).strip()), i + 1))
            i += 1
            continue
        if s.startswith("|"):
            start = i
            rows = []
            while i < n and lines[i].strip().startswith("|"):
                rows.append(lines[i].strip())
                i += 1
            blocks.append(("table", rows, start + 1))
            continue
        if s.startswith("Figure:"):
            start = i
            spec = s[len("Figure:"):].strip()
            extra = []
            i += 1
            while i < n and lines[i].strip() and (
                    lines[i].strip()[0].islower()
                    or re.match(r"^(Left|Right)\b", lines[i].strip())):
                extra.append(lines[i].strip())
                i += 1
            blocks.append(("figure", (spec, " ".join(extra)), start + 1))
            continue
        if re.match(r"^[-*+]\s+", s):
            blocks.append(("bullet", re.sub(r"^[-*+]\s+", "", s), i + 1))
            i += 1
            continue
        if re.match(r"^\d+[.)]\s+", s):
            blocks.append(("number", re.sub(r"^\d+[.)]\s+", "", s), i + 1))
            i += 1
            continue
        if s.startswith(">"):
            start = i
            buf = []
            while i < n and lines[i].strip().startswith(">"):
                buf.append(lines[i].strip().lstrip(">").strip())
                i += 1
            blocks.append(("quote", " ".join(buf), start + 1))
            continue
        # Paragraph: consecutive plain lines. A line that is only a marker
        # stays its own paragraph so pending items read as a list.
        start = i
        buf = []
        while i < n:
            t = lines[i].strip()
            if (not t or re.match(r"^(#{1,6}\s|\||Figure:|[-*+]\s|\d+[.)]\s|>)", t)):
                break
            if buf and (MARKER_RE.fullmatch(t) or MARKER_RE.fullmatch(buf[-1])):
                break
            buf.append(t)
            i += 1
        blocks.append(("para", " ".join(buf), start + 1))
    return blocks


def split_row(row):
    row = row.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|"):
        row = row[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", row)]


SEP_RE = re.compile(r"^:?-{3,}:?$")
UNIT_WORDS = {"us", "ms", "s", "gbps", "kb", "mb", "gb", "x", "seed", "seeds",
              "never", "max", "of", "pending"}
NUMTOK_RE = re.compile(r"^[\[\](]*[-+]?[\d.,]*\d[\d.,]*(?:[-–][\d.,]+)?[x%]?"
                       r"(?:/\d+)?[\])]*[,;]?$")


def is_numeric(cell):
    t = plain_text(cell).strip()
    if not t or not re.search(r"\d", t) and t.lower() != "pending":
        return t.lower() == "pending"
    for tok in re.split(r"[\s/]+", t.replace("(", " ( ").replace(")", " ) ")):
        if not tok or tok in "()[]":
            continue
        if NUMTOK_RE.match(tok):
            continue
        if tok.strip("()[],;").lower() in UNIT_WORDS:
            continue
        return False
    return True


_FONTS = {}


def text_width_in(text, size_pt, bold=False):
    """Rendered width of `text` in inches, measured with the body font when
    PIL can load it, else estimated."""
    key = (bold, size_pt)
    if key not in _FONTS:
        _FONTS[key] = None
        try:
            from PIL import ImageFont
            fname = "Times_New_Roman_Bold.ttf" if bold else "Times_New_Roman.ttf"
            for d in ("/usr/share/fonts/truetype/msttcorefonts",
                      "/Library/Fonts", "C:/Windows/Fonts"):
                fp = os.path.join(d, fname)
                if os.path.exists(fp):
                    # Measure at 10x for sub-point precision.
                    _FONTS[key] = ImageFont.truetype(fp, int(size_pt * 10))
                    break
        except Exception:
            pass
    f = _FONTS[key]
    if f is not None:
        return f.getlength(text) / 10 / 72.0 * 1.03
    return len(text) * size_pt * (0.5 if bold else 0.47) / 72.0


def column_widths(rows, ncols, size_pt):
    """Column widths (inches) summing to the text width: every column at
    least its longest unbreakable chunk, the label column up to a third of
    the width, and the remaining space shared in proportion to how much
    each column falls short of fitting on one line."""
    pad = 0.17                           # default cell margins, both sides
    desired, minimum = [0.0] * ncols, [0.0] * ncols
    for r, row in enumerate(rows):
        for c in range(ncols):
            t = plain_text(row[c]) if c < len(row) else ""
            if r > 0 and is_numeric(row[c]):
                t = glue_numbers(t)
            bold = (r == 0)
            w = text_width_in(t, size_pt, bold) + pad
            chunks = t.split(" ") or [""]
            longest = max(text_width_in(x, size_pt, bold) for x in chunks) + pad
            desired[c] = max(desired[c], w)
            minimum[c] = max(minimum[c], longest, 0.4)
    total = TEXT_WIDTH_IN
    minimum[0] = max(minimum[0], min(desired[0], total / 3))
    if sum(desired) <= total:
        scale = total / sum(desired)
        return [d * scale for d in desired]
    if sum(minimum) >= total:
        return [m * total / sum(minimum) for m in minimum]
    short = [max(d - m, 0.0) for d, m in zip(desired, minimum)]
    f = (total - sum(minimum)) / (sum(short) or 1.0)
    return [m + f * s for m, s in zip(minimum, short)]


# --------------------------------------------------------------------------
# Document builder

class Builder:
    def __init__(self, src):
        self.src = src
        self.doc = Document()
        self.tables = 0
        self.figures = 0
        self.body_words = 0
        self.table_words = 0
        self.markers = []
        self.space_before_next = None
        self.in_references = False
        self.title = None
        self.authors = None
        self._setup()

    def _setup(self):
        d = self.doc
        sec = d.sections[0]
        sec.orientation = WD_ORIENT.PORTRAIT
        sec.page_width, sec.page_height = Inches(8.5), Inches(11)
        for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
            setattr(sec, side, Inches(1))
        sec.header_distance = sec.footer_distance = Inches(0.5)
        add_page_number_footer(sec)

        st = d.styles
        normal = st["Normal"]
        set_style_font(normal, BODY_FONT, BODY_PT)
        pf = normal.paragraph_format
        pf.line_spacing = 1.08
        pf.space_after = Pt(6)
        pf.space_before = Pt(0)
        black = RGBColor(0, 0, 0)
        for name, size, before, after, ital in (("Heading 1", 14, 14, 6, False),
                                                ("Heading 2", 12, 10, 4, False),
                                                ("Heading 3", 11, 8, 3, True)):
            h = st[name]
            set_style_font(h, BODY_FONT, size, True, ital, black)
            h.paragraph_format.space_before = Pt(before)
            h.paragraph_format.space_after = Pt(after)
            h.paragraph_format.keep_with_next = True
            h.paragraph_format.line_spacing = 1.0
        for name in ("List Bullet", "List Number"):
            set_style_font(st[name], BODY_FONT, BODY_PT)
            st[name].paragraph_format.space_after = Pt(3)

    # -- paragraphs ---------------------------------------------------------
    def para(self, style=None):
        p = self.doc.add_paragraph(style=style)
        if self.space_before_next is not None:
            p.paragraph_format.space_before = Pt(self.space_before_next)
            self.space_before_next = None
        return p

    def note(self, text, align=None):
        p = self.para()
        if align is not None:
            p.alignment = align
        p.paragraph_format.space_after = Pt(8)
        add_inline(p, text, size=10, italic=True)
        return p

    def count(self, text):
        return len(plain_text(MARKER_RE.sub("", text)).split())

    def find_markers(self, text, lineno):
        for m in MARKER_RE.finditer(text):
            self.markers.append((lineno, m.group(0)))

    # -- blocks -------------------------------------------------------------
    def build(self, blocks):
        i = 0
        # Title and front matter.
        if blocks and blocks[0][0] == "heading" and blocks[0][1][0] == 1:
            self.title = plain_text(blocks[0][1][1])
            p = self.para()
            p.alignment = WD_ALIGN_PARAGRAPH.CENTER
            p.paragraph_format.space_after = Pt(6)
            p.paragraph_format.line_spacing = 1.0
            add_inline(p, blocks[0][1][1], size=18, bold=True)
            i = 1
            front = []
            while i < len(blocks) and blocks[i][0] == "para":
                front.append(blocks[i][1])
                i += 1
            for k, text in enumerate(front):
                p = self.para()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after = Pt(2)
                add_inline(p, text, size=11, italic=(k > 0))
                if k == 0:
                    self.authors = plain_text(text)
            spacer = self.para()
            spacer.paragraph_format.space_after = Pt(0)
            spacer.paragraph_format.line_spacing = 1.0
            r = spacer.add_run("")
            set_run_font(r, BODY_FONT, 6)

        while i < len(blocks):
            kind, payload, lineno = blocks[i]
            prev = blocks[i - 1] if i > 0 else None
            nxt = blocks[i + 1] if i + 1 < len(blocks) else None
            if kind == "heading":
                level, text = payload
                self.find_markers(text, lineno)
                self.in_references = (plain_text(text).strip().lower()
                                      in ("references", "bibliography"))
                style = {1: "Heading 1", 2: "Heading 1", 3: "Heading 2"}.get(level, "Heading 3")
                p = self.para(style)
                add_inline(p, text)
                self.body_words += self.count(text)
            elif kind == "table":
                after = nxt if nxt and nxt[0] == "para" and self.is_caption(nxt[1], before=False) else None
                self.table(payload, lineno)
                if after:
                    self.note(after[1])
                    self.find_markers(after[1], after[2])
                    i += 1
                elif nxt and nxt[0] == "table":
                    self.table_gap()
            elif kind == "figure":
                self.figure(payload, lineno)
            elif kind == "para":
                if nxt and nxt[0] == "table" and self.is_caption(payload, before=True):
                    p = self.note(payload)
                    p.paragraph_format.keep_with_next = True
                    p.paragraph_format.space_after = Pt(3)
                    self.find_markers(payload, lineno)
                    i += 1
                    continue
                self.find_markers(payload, lineno)
                p = self.para()
                if self.in_references:
                    pf = p.paragraph_format
                    pf.left_indent = Inches(0.5)
                    pf.first_line_indent = Inches(-0.5)
                    pf.space_after = Pt(4)
                else:
                    self.body_words += self.count(payload)
                add_inline(p, payload)
            elif kind in ("bullet", "number"):
                self.find_markers(payload, lineno)
                p = self.para("List Bullet" if kind == "bullet" else "List Number")
                add_inline(p, payload)
                self.body_words += self.count(payload)
            elif kind == "quote":
                self.find_markers(payload, lineno)
                p = self.para()
                p.paragraph_format.left_indent = Inches(0.5)
                p.paragraph_format.right_indent = Inches(0.5)
                add_inline(p, payload, italic=True)
                self.body_words += self.count(payload)
            i += 1

        cp = self.doc.core_properties
        cp.title = self.title or ""
        if self.authors:
            cp.author = self.authors
        cp.subject = "CS 6250 progress report"
        cp.comments = "Built from docs/checkpoint2_draft.md by scripts/build_report_docx.py"

    @staticmethod
    def is_caption(text, before):
        t = text.strip()
        if re.match(r"^Table\b", t) and t.endswith("."):
            return True
        if not before and re.match(r"^(Cells|Note|Notes):", t):
            return True
        return False

    def table(self, rows, lineno):
        cells = [split_row(r) for r in rows]
        align = None
        if len(cells) > 1 and all(SEP_RE.match(c) for c in cells[1] if c):
            spec = cells[1]
            align = []
            for c in spec:
                if c.startswith(":") and c.endswith(":"):
                    align.append("center")
                elif c.endswith(":"):
                    align.append("right")
                elif c.startswith(":"):
                    align.append("left")
                else:
                    align.append(None)
            cells = [cells[0]] + cells[2:]
        ncols = max(len(r) for r in cells)
        for r in cells:
            r.extend([""] * (ncols - len(r)))
        for r in cells:
            for c in r:
                self.find_markers(c, lineno)
                self.table_words += self.count(c)
        size = TABLE_WIDE_PT if ncols > 7 else TABLE_PT

        # Per-column alignment: explicit colons win; otherwise a column whose
        # body is mostly numeric is right-aligned throughout, and isolated
        # numeric cells elsewhere are right-aligned individually.
        col_right = []
        for c in range(ncols):
            body = [r[c] for r in cells[1:] if r[c].strip()]
            num = sum(is_numeric(x) for x in body)
            col_right.append(c > 0 and bool(body) and num >= 0.6 * len(body))

        t = self.doc.add_table(rows=len(cells), cols=ncols)
        t.style = self.doc.styles["Table Grid"]
        t.alignment = WD_TABLE_ALIGNMENT.CENTER
        t.autofit = True
        set_table_widths(t, column_widths(cells, ncols, size))
        for r, row in enumerate(cells):
            trow = t.rows[r]
            cant_split(trow)
            if r == 0:
                mark_header_row(trow)
            for c, text in enumerate(row):
                cell = trow.cells[c]
                p = cell.paragraphs[0]
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.space_before = Pt(0)
                p.paragraph_format.line_spacing = 1.0
                if len(cells) <= 20 and r < len(cells) - 1:
                    p.paragraph_format.keep_with_next = True   # keep short tables whole
                a = align[c] if align and c < len(align) else None
                if a == "right" or (a is None and (col_right[c] or
                                                  (c > 0 and r > 0 and is_numeric(text)))):
                    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                elif a == "center":
                    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                numeric = r > 0 and is_numeric(text)
                add_inline(p, glue_numbers(text) if numeric else text,
                           size=size, bold=(True if r == 0 else None),
                           nobreak=numeric)
                if r == 0:
                    shade_cell(cell, "F2F2F2")
        self.tables += 1
        self.space_before_next = 8

    def table_gap(self):
        """Empty paragraph between two adjacent tables, which Word and
        LibreOffice would otherwise merge into one."""
        p = self.para()
        p.paragraph_format.space_after = Pt(0)
        p.paragraph_format.line_spacing = 1.0
        r = p.add_run("")
        set_run_font(r, BODY_FONT, 4)

    def resolve_image(self, path):
        full = os.path.join(REPO, path)
        root, ext = os.path.splitext(full)
        if ext.lower() == ".pdf" and os.path.exists(root + ".png"):
            return root + ".png"
        if os.path.exists(full):
            if ext.lower() == ".pdf":
                out = os.path.join(tempfile.mkdtemp(), "fig")
                subprocess.run(["pdftoppm", "-png", "-r", "300", "-singlefile",
                                full, out], check=True)
                return out + ".png"
            return full
        if ext.lower() != ".png" and os.path.exists(root + ".png"):
            return root + ".png"
        return None

    def figure(self, payload, lineno):
        spec, extra = payload
        items = []
        prose = ""
        for seg in [s.strip() for s in spec.split(";") if s.strip()]:
            m = re.match(r"^(\S+\.(?:png|pdf|jpe?g|svg))\s*(.*)$", seg, re.I)
            if not m:
                prose = (prose + " " + seg).strip()
                continue
            path, rest = m.group(1), m.group(2).strip()
            label = None
            lm = re.match(r"^\(([^)]*)\)\s*(.*)$", rest)
            if lm:
                label, rest = lm.group(1).strip(), lm.group(2).strip()
            if rest:
                prose = (prose + " " + rest).strip()
            items.append((path, label))
        if extra:
            prose = (prose + " " + extra).strip()

        resolved = []
        for path, label in items:
            img = self.resolve_image(path)
            if img is None:
                warn(f"line {lineno}: missing figure {path}")
            resolved.append((path, label, img))

        from PIL import Image
        ratios = []
        for _, _, img in resolved:
            if img:
                with Image.open(img) as im:
                    ratios.append(im.size[0] / im.size[1])
            else:
                ratios.append(None)

        # Side by side only for two near-square or portrait images of similar
        # shape: wide multi-panel plots are illegible at half width.
        pair = (len(resolved) == 2 and all(ratios)
                and max(ratios) / min(ratios) <= 1.15
                and max(ratios) <= PAIR_MAX_ASPECT)
        if pair:
            body = self.doc.element.body
            last = [e for e in body if e.tag != qn("w:sectPr")][-1:]
            if last and last[0].tag == qn("w:tbl"):
                self.table_gap()
            t = self.doc.add_table(rows=1, cols=2)
            t.alignment = WD_TABLE_ALIGNMENT.CENTER
            remove_table_borders(t)
            set_table_widths(t, [TEXT_WIDTH_IN / 2] * 2)
            cant_split(t.rows[0])
            for c, (path, label, img) in enumerate(resolved):
                p = t.rows[0].cells[c].paragraphs[0]
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.keep_with_next = True
                w = FIG_PAIR_WIDTH_IN
                if w / ratios[c] > FIG_MAX_HEIGHT_IN:
                    w = FIG_MAX_HEIGHT_IN * ratios[c]
                p.add_run().add_picture(img, width=Inches(w))
        else:
            for (path, label, img), ratio in zip(resolved, ratios):
                p = self.para()
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.keep_with_next = True
                p.paragraph_format.space_after = Pt(4)
                p.paragraph_format.line_spacing = 1.0
                if img is None:
                    r = p.add_run(f"[missing figure: {path}]")
                    set_run_font(r, BODY_FONT, BODY_PT, True)
                    r.font.color.rgb = RGBColor(0xC0, 0, 0)
                    r.font.highlight_color = WD_COLOR_INDEX.YELLOW
                    continue
                w = FIG_WIDTH_IN
                if w / ratio > FIG_MAX_HEIGHT_IN:
                    w = FIG_MAX_HEIGHT_IN * ratio
                p.add_run().add_picture(img, width=Inches(w))

        self.figures += 1
        labels = [label if label else os.path.splitext(os.path.basename(path))[0].replace("_", " ")
                  for path, label, _ in resolved]
        caption = "; ".join(labels)
        if prose:
            caption = caption + (". " if caption and not caption.endswith(".") else " ") + prose
        if caption and not caption.endswith("."):
            caption += "."
        self.find_markers(caption, lineno)
        p = self.para()
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.space_before = Pt(2)
        p.paragraph_format.space_after = Pt(10)
        r = p.add_run(f"Figure {self.figures}. ")
        set_run_font(r, BODY_FONT, 10, True, True)
        add_inline(p, caption, size=10, italic=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", help="output .docx path")
    ap.add_argument("--src", default=os.path.join(REPO, "docs", "checkpoint2_draft.md"))
    args = ap.parse_args()

    with open(args.src, encoding="utf-8") as f:
        lines = f.readlines()
    b = Builder(args.src)
    b.build(parse_blocks(lines))
    if b.markers:
        warn(f"{len(b.markers)} unresolved marker(s): " +
             "; ".join(f"line {ln}: {m}" for ln, m in b.markers))
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    b.doc.save(out)
    print(f"wrote {out}")
    print(f"summary: {b.body_words} body words (excluding tables, captions, "
          f"references; {b.table_words} more in tables), {b.tables} tables, "
          f"{b.figures} figures, {len(warnings)} warnings")


if __name__ == "__main__":
    main()
