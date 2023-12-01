from pathlib import Path
from pprint import pprint
import re
import tomllib  # 3.11

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


def query_pubmed_idconv(session, id_, email):
    """Query Pubmed ID Converter API

    Returns a dict with doi, pmid, and pmcid if found. Returns None
    otherwise.

    See: https://www.ncbi.nlm.nih.gov/pmc/tools/id-converter-api/

    """

    payload = {
        "tool": "mslw-paper-parser",
        "email": email,
        "format": "json",
        "versions": "no",
        "ids": id_,
    }

    r = session.get(
        url="https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/",
        params=payload,
    )

    rj = r.json()
    pprint(rj)

    record = rj.get("records")[0]
    if record.get("status") == "error":
        # query successful, but no results found for id
        # api returns a record with the original id, plus status & errmsg fields
        print("Pubmed idconv error:", record.get("errmsg"))
        return
    return record


def query_crossref(session, doi, email):
    """Perform crossref doi to metadata query

    Uses Crossref's REST api to retrieve metadata for a given
    doi. Returned metadata is a superset of the pubmed citation
    metadata, and can be used with little transformation.

    See:
    https://www.crossref.org/documentation/retrieve-metadata/rest-api/

    """

    r = session.get(
        url = f"https://api.crossref.org/works/{doi}?mailto={email}",
    )

    if r.ok:
        pprint(r.json())
        return r.json().get("message")


def check_ratings(items, close=0.75, almost=0.9):
    """Print warning if ratings are close, return one item

    If two ratings are almost the same, but the first item is a
    preprint and the second is not, returns the latter.

    If the first item found is a "peer-review" (happened for an author
    response), it is taken out of consideration.

    Otherwise, returns the first (i.e. higher-rated) item.

    """
    if len(items) < 2:
        return items[0]

    scores = tuple(item["score"] for item in items)
    similarity = scores[1] / scores[0]

    if similarity > close:
        print(
            f"ATTN: Similar scores ({round(similarity, 2)}) for",
            meta_item_to_str(items[0]),
            "and",
            meta_item_to_str(items[1]),
        )
        if items[0]["type"] == "peer-review":
            print("The former is a peer review, discarding")
            return check_ratings(items[1:], close, almost)
    if (
        similarity >= almost
        and items[0].get("subtype") == "preprint"
        and items[1].get("type") == "journal-article"
    ):
        print("The former is a preprint, taking the latter as it is a journal article")
        return items[1]
    return items[0]


def query_crossref_bibliographic(session, citation, email):
    """Perform bibliographic query in crossref api"""

    payload = {
        "mailto": email,
        "query.bibliographic": citation,
        "rows": 3,
    }

    r = session.get(
        url=f"https://api.crossref.org/works/",
        params=payload,
    )

    if r.ok:
        items = r.json().get("message").get("items")
        best = check_ratings(items)
        pprint(best)
        return best


def query_doi_org(session, doi):
    """Perform a doi query at doi.org

    Queries doi.org about a given doi, using content negotiation to
    request CSL json. This should get redirected to crossref,
    datacite, or medra.

    See: https://citation.crosscite.org/docs.html

    """

    r = session.get(
        url = f"https://doi.org/{doi}",
        headers={"Accept": "application/vnd.citationstyles.csl+json"},
    )
    # todo: include mailto header so that crossref lets us into polite pool
    # https://www.crossref.org/documentation/retrieve-metadata/content-negotiation/

    if r.ok:
        pprint(r.json())
        return r.json()


if __name__ == "__main__":
    # Read items from a file that contains a copy-paste of citation texts from e-mail
    # Items are delimited with a blank line
    sample_input = Path("sample.txt")
    items = read_file(sample_input)

    # Read authors who are SFB authors, and should be displayed in bold
    # For now, just a set of last names
    authors_file = Path("sfb_authors.txt")
    sfb_authors = set(authors_file.read_text().rstrip().split("\n"))

    # For some APIs, an e-mail is required to be polite
    with Path("userconfig.toml").open("rb") as f:
        user_config = tomllib.load(f)
        email = user_config.get("user").get("email")

    # Cache requests to avoid spamming Pubmed
    session = CachedSession('query_cache')

    citations = []

    for item in items:

        pmid = find_id(item, "pmid")

        if pmid is not None:
            citations.append(query_pubmed_ctxp(session, pmid))
            continue

        doi = find_id(item, "doi")

        if doi is not None:
            known_ids = query_pubmed_idconv(session, doi, email)
            if known_ids is not None and known_ids.get("pmid") is not None:
                pmid = known_ids.get("pmid")
                citations.append(query_pubmed_ctxp(session, pmid))
            else:
                #citations.append(query_crossref(session, doi, email))
                citations.append(query_doi_org(session, doi))

        if doi is None and pmid is None:
            citations.append(query_crossref_bibliographic(session, item, email))


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
