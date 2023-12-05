import argparse
from pathlib import Path
from pprint import pprint
import re
import tomllib  # 3.11

from jinja2 import Environment, PackageLoader, select_autoescape
from requests import Session
from requests_cache import CachedSession, CacheMixin
from requests_ratelimiter import LimiterMixin

from read_input import read_file


class CachedLimiterSession(CacheMixin, LimiterMixin, Session):
    """Session class with caching and rate-limiting behavior. Accepts
    arguments for both LimiterSession and CachedSession.

    """

    pass


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


def id_from_url(s, idtype):
    """Find an identifier in a URL

    Journal links often include an identifier (doi, pmid, pmcid) in
    their components, but url patterns differ. This function tries to
    match patterns for several known publishers, as well as pubmed and
    pubmed central.

    Although the patterns are very similar between publishers, there
    are differences in the number of components that are parts of the
    doi suffix versus parts of journal-specific url (a suffix can
    contain slashes) - compare oup and mit press for example. To avoid
    spurious matches, patterns are per-publisher.

    Only biorxiv doi pattern is based on their FAQ, others were
    created by inspecting several available URLs. Pubmed (central)
    seems the most obvious.

    """
    patterns = {
        "doi": [
            r"biorxiv\.org/content/(10\.\d{4,6}/\d{6})",  # biorxiv pre 2019-10-11
            r"biorxiv\.org/content/(10\.\d{4,6}/\d{4}\.\d{2}\.\d{2}\.\d{6})",  # biorxiv
            r"medrxiv\.org/content/(10\.\d{4,6}/\d{4}\.\d{2}\.\d{2}\.\d{8})",  # medrxiv
            r"link\.springer\.com/(?:content|article)(?:/pdf)?/(10\.\d+/s[\d\-]+)",  # springer
            r"onlinelibrary\.wiley\.com/doi(?:/e?pdf|/epub|/full)?/(10\.\d+/[a-z]+\.\d+)",  # wiley
            r"embopress\.org/doi(?:/e?pdf|/epub|/full)?/(10\.\d+/[a-z]+\.\d+)",  # embo press
            r"science\.org/doi(?:/e?pdf|/epub|/full)?/(10\.\d+/[a-z]+\.\w+)",  # science
            r"sagepub\.com/doi(?:/e?pdf|/epub|/full)?/(10\.\d+/\d+)",  # sage
            r"academic\.oup\.com/\w+/[\w\-]+/doi/(10\.\d+/\w+/\w+)",  # oup
            r"direct.mit.edu/\w+/[\w\-]+/doi/(10\.\d+/\w+/)",  # mit press
            r"ahajournals\.org/doi(?:/e?pdf|/epub|/full)?/(10\.\d+/\w+\.\d+\.\d+)",  # aha
            r"frontiersin\.org/articles/(10\.\d+/\w+\.\d+\.\d+)",  # frontiers
            r"pubs\.acs\.org/doi/(10\.\d+/\w+\.\w+(?:\.\w+)?)",  # acs
        ],
        "pmcid": [r"ncbi\.nlm\.nih\.gov/pmc/articles/PMC(\d+)"],
        "pmid": [r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)"],
    }

    for pat in patterns[idtype.lower()]:
        m = re.search(pat, s)
        if m is not None:
            return m.group(1)


def get_identifiers(entry):
    """Find identifiers in citation and/or url"""
    identifiers = {}
    for id_ in ("doi", "pmid", "pmcid"):
        # prefer "canonical" form included in citation
        identifiers[id_] = find_id(entry.citation, id_)
        if identifiers[id_] is None:
            # fall back to url patterns
            identifiers[id_] = id_from_url(entry.url, id_)
    return identifiers


def query_pubmed_ctxp(session, id_, db):
    """Query Pubmed Literature Citation Exporter

    Obtains a CSL json for a given PubMed or PMC id. The db argument
    needs to be set to "pubmed" or "pmc" respectively. Returns None if
    the response is not ok.

    See https://api.ncbi.nlm.nih.gov/lit/ctxp/

    """
    payload = {"format": "csl", "contenttype": "json", "id": id_}

    r = session.get(
        url=f"https://api.ncbi.nlm.nih.gov/lit/ctxp/v1/{db.lower()}/",
        headers={"user-agent": "sfbPublicationParser/0.1"},
        params=payload,
    )

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
        "tool": "sfbPublicationParser",
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
        url=f"https://api.crossref.org/works/{doi}?mailto={email}",
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


def query_doi_org(session, doi, useragent=None):
    """Perform a doi query at doi.org

    Queries doi.org about a given doi, using content negotiation to
    request CSL json. This should get redirected to crossref,
    datacite, or medra.

    See: https://citation.crosscite.org/docs.html

    """

    headers = {"Accept": "application/vnd.citationstyles.csl+json"}
    if useragent is not None:
        headers["User-Agent"] = useragent

    r = session.get(
        url=f"https://doi.org/{doi}",
        headers=headers,
    )

    if r.ok:
        pprint(r.json())
        return r.json()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("infile", type=Path)
    args = parser.parse_args()

    # Read items from a file that contains a copy-paste from word
    # Items contain citation, optional comment, and url, one per line
    # Items are delimited with a blank line
    items = read_file(args.infile)

    # Read authors who are SFB authors, and should be displayed in bold
    # For now, just a set of last names
    authors_file = Path("sfb_authors.txt")
    sfb_authors = set(authors_file.read_text().rstrip().split("\n"))

    # For some APIs, an e-mail is required to be polite
    with Path("userconfig.toml").open("rb") as f:
        user_config = tomllib.load(f)
        email = user_config.get("user").get("email")

    # User-agent string with mailto:, for use with crossref
    # https://api.crossref.org/swagger-ui/index.html
    appname = "sfbPublicationParser"
    appver = "0.1"
    comment = f"https://github.com/sfb1451/publication-parser; mailto: {email}"
    useragent = f"{appname}/{appver} ({comment})"

    # Have two cached requests session, one additionally throttled
    # pubmed suggests throttling to max 3 per second, crossref can take more
    throttled_session = CachedLimiterSession(cache_name="pubmed_cache", per_second=3)
    session = CachedSession("query_cache")

    citations = []

    for item in items["INF"]:

        identifiers = get_identifiers(item)

        if (pmid := identifiers["pmid"]) is not None:
            citations.append(query_pubmed_ctxp(throttled_session, pmid, "pubmed"))

        elif (pmcid := identifiers["pmcid"]) is not None:
            citations.append(query_pubmed_ctxp(throttled_session, pmcid, "pmc"))

        elif (doi := identifiers["doi"]) is not None:
            citations.append(query_doi_org(session, doi, useragent))

        else:
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
