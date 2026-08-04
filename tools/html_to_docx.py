"""Turn RULES.html into a .docx that Google Docs imports with tables intact.

macOS `textutil` silently flattens HTML tables into runs of text, which loses
the point-value and tiebreak tables, and there is no pandoc here. This writes
the WordprocessingML directly instead, using nothing but the standard library.

    python3 tools/html_to_docx.py RULES.html "Scoring Rules.docx"

Handles the subset RULES.html uses: h1-h3, p, ul/ol, table, blockquote, hr and
inline strong/em/code. Lists are drawn with a bullet or number and a hanging
indent rather than real Word numbering, which keeps the file simple and looks
the same on the page.
"""

import sys
import zipfile
from html.parser import HTMLParser
from xml.sax.saxutils import escape

W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'

BLOCKS = {'h1', 'h2', 'h3', 'p', 'li', 'blockquote', 'td', 'th'}


class Doc(HTMLParser):
    """Collect the document as a flat list of blocks and tables."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.body = []          # ('para', style, runs) | ('table', rows) | ('hr',)
        self.runs = []          # runs of the block being read
        self.style = None
        self.fmt = {'b': 0, 'i': 0, 'code': 0}
        self.list_stack = []    # ('ul'|'ol', counter)
        self.table = None       # rows being built
        self.row = None
        self.in_cell = False

    # -- helpers ----------------------------------------------------------
    def flush(self):
        runs, self.runs = self.runs, []
        style = self.style
        self.style = None
        if any(t.strip() for t, _ in runs):
            return ('para', style, runs)
        return None

    def emit(self):
        block = self.flush()
        if block:
            self.body.append(block)

    def add_text(self, text):
        if not text:
            return
        self.runs.append((text, {k: v > 0 for k, v in self.fmt.items()}))

    # -- parser -----------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        if tag in ('strong', 'b'):
            self.fmt['b'] += 1
        elif tag in ('em', 'i'):
            self.fmt['i'] += 1
        elif tag == 'code':
            self.fmt['code'] += 1
        elif tag == 'br':
            self.add_text('\n')
        elif tag == 'hr':
            self.emit()
            self.body.append(('hr',))
        elif tag in ('ul', 'ol'):
            self.emit()
            self.list_stack.append([tag, 0])
        elif tag == 'li':
            self.emit()
            if self.list_stack:
                self.list_stack[-1][1] += 1
                kind, n = self.list_stack[-1]
                self.style = 'bullet' if kind == 'ul' else 'number'
                self.add_text('\u2022\t' if kind == 'ul' else f'{n}.\t')
        elif tag == 'table':
            self.emit()
            self.table = []
        elif tag == 'tr':
            self.row = []
        elif tag in ('td', 'th'):
            self.in_cell = True
            self.runs = []
            if tag == 'th':
                self.fmt['b'] += 1
        elif tag in BLOCKS:
            self.emit()
            self.style = tag

    def handle_endtag(self, tag):
        if tag in ('strong', 'b'):
            self.fmt['b'] = max(0, self.fmt['b'] - 1)
        elif tag in ('em', 'i'):
            self.fmt['i'] = max(0, self.fmt['i'] - 1)
        elif tag == 'code':
            self.fmt['code'] = max(0, self.fmt['code'] - 1)
        elif tag in ('ul', 'ol'):
            self.emit()
            if self.list_stack:
                self.list_stack.pop()
        elif tag in ('td', 'th'):
            if tag == 'th':
                self.fmt['b'] = max(0, self.fmt['b'] - 1)
            self.row.append(self.runs)
            self.runs = []
            self.in_cell = False
        elif tag == 'tr':
            if self.row:
                self.table.append(self.row)
            self.row = None
        elif tag == 'table':
            self.body.append(('table', self.table))
            self.table = None
        elif tag in BLOCKS:
            self.emit()

    def handle_data(self, data):
        text = ' '.join(data.split())
        if not text:
            return
        if self.style is None and not self.in_cell:
            return          # stray text outside a block
        # Re-space across inline tags, without pushing punctuation off the
        # word it belongs to: "<strong>x</strong>: y" must not become "x : y".
        if (self.runs and not self.runs[-1][0].endswith(('\t', '\n', '(', ' '))
                and text[0] not in ',.;:!?)%'):
            text = ' ' + text
        self.add_text(text)


# --- WordprocessingML -------------------------------------------------------

STYLE_MAP = {'h1': 'Title', 'h2': 'Heading1', 'h3': 'Heading2',
             'blockquote': 'Quote'}


def runs_xml(runs):
    out = []
    for text, fmt in runs:
        props = ''
        if fmt.get('b'):
            props += '<w:b/>'
        if fmt.get('i'):
            props += '<w:i/>'
        if fmt.get('code'):
            props += '<w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/>'
        pieces = text.split('\t')
        body = ''
        for i, piece in enumerate(pieces):
            if i:
                body += '<w:tab/>'
            if piece:
                body += (f'<w:t xml:space="preserve">{escape(piece)}</w:t>')
        out.append(f'<w:r>{f"<w:rPr>{props}</w:rPr>" if props else ""}{body}</w:r>')
    return ''.join(out)


def para_xml(style, runs, in_table=False):
    props = []
    if style in STYLE_MAP:
        props.append(f'<w:pStyle w:val="{STYLE_MAP[style]}"/>')
    if style in ('bullet', 'number'):
        props.append('<w:ind w:left="720" w:hanging="360"/>'
                     '<w:tabs><w:tab w:val="left" w:pos="720"/></w:tabs>')
    if style == 'blockquote':
        props.append('<w:ind w:left="720"/>')
    if not in_table:
        props.append('<w:spacing w:after="140"/>')
    pr = f'<w:pPr>{"".join(props)}</w:pPr>' if props else ''
    return f'<w:p>{pr}{runs_xml(runs)}</w:p>'


def table_xml(rows):
    borders = ''.join(
        f'<w:{e} w:val="single" w:sz="6" w:space="0" w:color="999999"/>'
        for e in ('top', 'left', 'bottom', 'right', 'insideH', 'insideV'))
    width = int(9360 / max(len(r) for r in rows))
    grid = ''.join(f'<w:gridCol w:w="{width}"/>'
                   for _ in range(max(len(r) for r in rows)))
    body = ''
    for i, row in enumerate(rows):
        cells = ''
        for cell in row:
            shade = ('<w:shd w:val="clear" w:fill="EFEFEF"/>' if i == 0 else '')
            cells += (f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/>'
                      f'{shade}</w:tcPr>{para_xml(None, cell, True)}</w:tc>')
        header = '<w:trPr><w:tblHeader/></w:trPr>' if i == 0 else ''
        body += f'<w:tr>{header}{cells}</w:tr>'
    return (f'<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/>'
            f'<w:tblBorders>{borders}</w:tblBorders>'
            f'<w:tblCellMar><w:left w:w="90" w:type="dxa"/>'
            f'<w:right w:w="90" w:type="dxa"/></w:tblCellMar></w:tblPr>'
            f'<w:tblGrid>{grid}</w:tblGrid>{body}</w:tbl>'
            f'<w:p><w:pPr><w:spacing w:after="140"/></w:pPr></w:p>')


def rule_xml():
    return ('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" '
            'w:space="1" w:color="BBBBBB"/></w:pBdr>'
            '<w:spacing w:after="200"/></w:pPr></w:p>')


STYLES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="%s">
<w:docDefaults><w:rPrDefault><w:rPr>
<w:rFonts w:ascii="Calibri" w:hAnsi="Calibri"/><w:sz w:val="22"/>
</w:rPr></w:rPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal">
<w:name w:val="Normal"/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/>
<w:pPr><w:spacing w:after="240"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="48"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/>
<w:pPr><w:outlineLvl w:val="0"/><w:spacing w:before="320" w:after="140"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="32"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/>
<w:pPr><w:outlineLvl w:val="1"/><w:spacing w:before="240" w:after="120"/></w:pPr>
<w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/>
<w:rPr><w:i/></w:rPr></w:style>
</w:styles>''' % W

CONTENT_TYPES = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>'''

RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>'''

DOC_RELS = '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>
</Relationships>'''


def build(html_path, out_path):
    parser = Doc()
    with open(html_path, encoding='utf-8') as f:
        parser.feed(f.read())
    parser.emit()

    parts = []
    for block in parser.body:
        if block[0] == 'para':
            parts.append(para_xml(block[1], block[2]))
        elif block[0] == 'table':
            parts.append(table_xml(block[1]))
        else:
            parts.append(rule_xml())

    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document xmlns:w="{W}"><w:body>{"".join(parts)}'
                f'<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
                f'<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" '
                f'w:left="1080"/></w:sectPr></w:body></w:document>')

    with zipfile.ZipFile(out_path, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', CONTENT_TYPES)
        z.writestr('_rels/.rels', RELS)
        z.writestr('word/_rels/document.xml.rels', DOC_RELS)
        z.writestr('word/styles.xml', STYLES)
        z.writestr('word/document.xml', document)

    tables = sum(1 for b in parser.body if b[0] == 'table')
    paras = sum(1 for b in parser.body if b[0] == 'para')
    print(f"{out_path}: {paras} paragraphs, {tables} tables")


if __name__ == '__main__':
    build(sys.argv[1], sys.argv[2])
