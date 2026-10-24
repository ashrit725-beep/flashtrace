"""Print the W3C passages quoted verbatim in docs/SPEC.md section 3, from a local download.

Lets a reader re-verify the quotes against a fresh copy of the W3C documents:

    mkdir -p /tmp/w3c && cd /tmp/w3c
    curl -sL -o wcag22.html https://www.w3.org/TR/WCAG22/
    curl -sL -o understanding-231.html \
        https://www.w3.org/WAI/WCAG22/Understanding/three-flashes-or-below-threshold.html
    python docs/tools/w3c_extract.py /tmp/w3c

Standard library only. No network access from this script.
"""
import re
import sys
from html.parser import HTMLParser
from pathlib import Path


class _Text(HTMLParser):
    """Collapse HTML to readable text, one line per block element."""

    BLOCK = {"p", "li", "dd", "dt", "div", "h1", "h2", "h3", "h4", "br", "tr", "section"}

    def __init__(self):
        super().__init__()
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.skip -= 1
        if tag in self.BLOCK - {"br", "tr"}:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t]+", " ", t)
        t = re.sub(r" ?\n ?", "\n", t)
        t = re.sub(r"\n\s*\n+", "\n", t)
        return t.strip()


def _slice_text(html, start, stop_pattern):
    m = re.search(stop_pattern, html[start + 10:])
    end = start + 10 + m.start() if m else len(html)
    p = _Text()
    p.feed(html[start:end])
    return p.text()


def glossary_entry(html, dfn_id=None, dfn_text=None):
    if dfn_id:
        m = re.search(r'<dt[^>]*>\s*<dfn[^>]*id="%s"' % re.escape(dfn_id), html)
    else:
        m = re.search(r"<dt[^>]*>\s*<dfn[^>]*>\s*%s\s*</dfn>" % re.escape(dfn_text), html)
    if not m:
        raise SystemExit("glossary entry not found: %s" % (dfn_id or dfn_text))
    return _slice_text(html, m.start(), r"<dt")


def sc_section(html, section_id):
    m = re.search(r'<section[^>]*id="%s"' % re.escape(section_id), html)
    if not m:
        raise SystemExit("section not found: " + section_id)
    return _slice_text(html, m.start(), r"</section>")


def understanding_intent(html):
    p = _Text()
    p.feed(html)
    t = p.text()
    i = t.find("\nIntent\n", t.find("Success Criterion (SC)"))
    j = t.find("\nBenefits\n", i + 10)
    return t[i + 1:j].strip()


def main(folder):
    folder = Path(folder)
    wcag = (folder / "wcag22.html").read_text(encoding="utf-8")
    und = (folder / "understanding-231.html").read_text(encoding="utf-8")
    status = re.search(r"W3C Recommendation \d+ \w+ \d{4}", wcag).group(0)
    print("# Document status:", status)
    blocks = [
        ("SC 2.3.1", sc_section(wcag, "three-flashes-or-below-threshold")),
        ("flash", glossary_entry(wcag, dfn_text="flash")),
        ("general flash and red flash thresholds",
         glossary_entry(wcag, dfn_id="dfn-general-flash-and-red-flash-thresholds")),
        ("relative luminance", glossary_entry(wcag, dfn_id="dfn-relative-luminance")),
        ("blinking", glossary_entry(wcag, dfn_id="dfn-blinking")),
        ("Understanding 2.3.1 - Intent", understanding_intent(und)),
    ]
    for title, body in blocks:
        print("\n## " + title + "\n")
        print(body)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
