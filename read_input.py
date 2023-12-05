import argparse
from collections import namedtuple
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("path", type=Path)
args = parser.parse_args()

Entry = namedtuple("Entry", ["citation", "url", "comment"], defaults=[None, None])


def process_buffer(buf, n=None):
    if len(buf) == 1:
        return Entry(buf)
    elif len(buf) == 2:
        if buf[1].startswith("http"):
            return Entry(buf[0], url=buf[1])
        else:
            return Entry(buf[0], comment=buf[1])
    elif len(buf) == 3:
        if buf[1].startswith("http") and not buf[2].startswith("http"):
            return Entry(buf[0], url=buf[1], comment=buf[2])
        elif buf[2].startswith("http") and not buf[1].startswith("http"):
            return Entry(buf[0], url=buf[2], comment=buf[1])
        else:
            raise ValueError(
                f"Found two URLs for a publication on line {n}"
            )
    else:
        print(buf)
        raise ValueError(f"Too many lines for a publication on line {n}")


entries = dict()

with args.path.open("rt") as f:
    line_buffer = []
    project = None
    for n, line in enumerate(f):
        line = line.strip()
        if line.startswith("*"):
            project = line.replace("*", "").lstrip()
            if project not in entries:
                entries[project] = []
        elif line == "" and len(line_buffer) > 0:
            entries[project].append(process_buffer(line_buffer, n))
            line_buffer = []
        elif line != "":
            line_buffer.append(line)
