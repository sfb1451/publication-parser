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


def query_pubmed_ctxp(session, id_):
    """Query Pubmed Literature Citation Exporter

    Obtains a CSL json for a given PubMed id. Returns None if the
    response is not ok.

    See https://api.ncbi.nlm.nih.gov/lit/ctxp/

    """
    payload = {
        "format": "csl",
        "contenttype": "json",
        "id": id_
    }

    r = session.get(
        url="https://api.ncbi.nlm.nih.gov/lit/ctxp/v1/pubmed/",
        headers={"user-agent": "mslw-paper-parser/0.0.1"},
        params=payload,
    )

    # todo: back off on error?
    # we don't want to sleep when caching, only for real queries

    pprint(r.json())
    if r.ok:
        return r.json()


# Read items from a file that contains a copy-paste of citation texts from e-mail
# Items are delimited with a blank line
sample_input = Path("sample.txt")
items = read_file(sample_input)

# Read authors who are SFB authors, and should be displayed in bold
# For now, just a set of last names
authors_file = Path("sfb_authors.txt")
sfb_authors = set(authors_file.read_text().rstrip().split("\n"))

# Cache requests to avoid spamming Pubmed
session = CachedSession('query_cache')

citations = []

for item in items:

    pmid = find_id(item, "pmid")

    if pmid is not None:
        citations.append(query_pubmed_ctxp(session, pmid))

# Jinja
env = Environment(
    loader=PackageLoader("parsepapers"),
    autoescape=select_autoescape(),
    trim_blocks=True,
    lstrip_blocks=True,
)
template = env.get_template("template.html")
Path("publications.html").write_text(
    template.render(citations=citations, sfb_authors=sfb_authors)
)


# TODO: decide between using citeproc-py or hand-formatting the response - maybe in a template?
# citeproc-py process is a bit involved and focused on creating standard bibliographies (think LaTeX document)
# and we probably want to play with formatting (SFB authors in bold, etc).
#
# a possible alternative with pubmed is to ask for a formatted citation e.g. in AMA or MLA format
