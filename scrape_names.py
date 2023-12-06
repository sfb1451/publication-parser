import argparse
from pathlib import Path
import re

from bs4 import BeautifulSoup
from requests_cache import CachedSession

urls = [
    "https://www.crc1451.uni-koeln.de/principal-investigators/",
    "https://www.crc1451.uni-koeln.de/clinician-scientists/",
    "https://www.crc1451.uni-koeln.de/post-docs/",
    "https://www.crc1451.uni-koeln.de/phd-students/",
    "https://www.crc1451.uni-koeln.de/management-committee/",
]

session = CachedSession("sfb_cache")
out_file = Path("sfb_authors.txt")
proj_pat = re.compile("[ABCZ]0\d|MGK")

people = []
for url in urls:
    request = session.get(url)
    soup = BeautifulSoup(request.text, "html.parser")

    for tag in soup.find_all("h4"):
        x = tag.text.rstrip()
        if len(x) == 0 or re.match(proj_pat, x):
            pass
        else:
            notitle = re.sub(r"^Dr\. ", "", x)
            people.append(notitle)

# don't have to be perfect with suffixes & the like
# can be hand-edited later

lastnames = set(x.split()[-1] for x in people)
out_file.write_text("\n".join(sorted(lastnames)))
