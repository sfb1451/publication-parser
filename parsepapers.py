from pathlib import Path
from pprint import pprint
import re

from jinja2 import Environment, PackageLoader, select_autoescape
from requests_cache import CachedSession


def find_id(citation_text, idtype="pmid"):
    """Find an identifier in a citation text

    Uses regular expressions to find a matching identifier. Expects
    identifiers to be preceded by either of: "PMID:", "DOI:",
    "doi.org/", "PMCID:", with space after colon being optional. DOI
    regex uses a negative lookbehind to avoid capturing trailing
    punctuation, and allows any characters in the suffix.

    """
    patterns = {
        "pmid": r"PMID: ?(\d+)",
        "doi": r"(?:doi: ?|doi.org/)(10\.[\d.]+/\S+)(?<![\.,;])",
        "pmcid": r"PMCID: ?(PMC\d+)",
    }

    pat = patterns[idtype.lower()]
    m = re.search(pat, citation_text)
    return m.group(1) if m is not None else m


def read_file(fp):
    """Read file, splitting on blank lines"""
    items = re.split(r"\n{2,}", fp.read_text().strip())
    return [i.replace("\n", " ") for i in items]


# Read items from a file that contains a copy-paste of citation texts from e-mail
# Items are delimited with a blank line
sample_input = Path("sample.txt")
items = read_file(sample_input)

# Cache requests to avoid spamming Pubmed
session = CachedSession('query_cache')

# Request a CSL file
payload = {
    "format": "csl",
    "contenttype": "json",
    "id": find_id(items[0])
}

r = session.get(
    url="https://api.ncbi.nlm.nih.gov/lit/ctxp/v1/pubmed/",
    headers={"user-agent": "mslw-paper-parser/0.0.1"},
    params=payload,
)


rj = r.json()
pprint(rj)

# Jinja
env = Environment(
    loader=PackageLoader("parsepapers"),
    autoescape=select_autoescape()
)
template = env.get_template("template.html")
print(template.render(citation=rj))


# TODO: decide between using citeproc-py or hand-formatting the response - maybe in a template?
# citeproc-py process is a bit involved and focused on creating standard bibliographies (think LaTeX document)
# and we probably want to play with formatting (SFB authors in bold, etc).
#
# a possible alternative with pubmed is to ask for a formatted citation e.g. in AMA or MLA format
