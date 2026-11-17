"""Build the place tables `domain/places.py` reads (SRCH-11), from GeoNames.

Run it again only to refresh the data; its output is committed, so WERA never downloads anything:

    cd services/agent
    uv run python scripts/build_places.py [cities15000.txt countryInfo.txt]

Without arguments it fetches both files from download.geonames.org. GeoNames data is CC BY 4.0
(https://www.geonames.org/), credited in the README.

Two files land in `wera_agent/domain/places_data/`:

* `countries.tsv`: ISO code, name, continent and ISO3 code, for every country GeoNames lists.
* `cities.tsv.gz`: every place of 15,000 people or more, with the names a posting might write
  for it. Only Latin-script names are kept, since a job ad WERA reads writes "Zürich" or "Zurich"
  and never "チューリッヒ". Codes ("ZRH") and names with digits are dropped. That is what stops
  34,000 places, with a hundred names each, from being 17 MB.
"""

import gzip
import io
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

GEONAMES = "https://download.geonames.org/export/dump/"
OUT = Path(__file__).resolve().parents[1] / "wera_agent" / "domain" / "places_data"


def fold(name: str) -> str:
    """Lower case without accents: "Zürich" and "Zurich" are one name."""
    decomposed = unicodedata.normalize("NFKD", name)
    return " ".join("".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower().split())


def latin(name: str) -> bool:
    return all(ch.isspace() or ch in "-'’.()" or ("LATIN" in unicodedata.name(ch, "")) for ch in name)


def wanted(name: str) -> bool:
    return len(name) >= 3 and latin(name) and not any(ch.isdigit() for ch in name) and not (name.isupper() and len(name) <= 4)


def read(arg: str | None, name: str) -> str:
    if arg:
        return Path(arg).read_text(encoding="utf-8")
    with urllib.request.urlopen(f"{GEONAMES}{name}", timeout=120) as response:  # noqa: S310 — a fixed https URL
        body = response.read()
    if name.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            return archive.read(name.replace(".zip", ".txt")).decode("utf-8")
    return body.decode("utf-8")


def main(argv: list[str]) -> int:
    cities = read(argv[0] if argv else None, "cities15000.zip")
    countries = read(argv[1] if len(argv) > 1 else None, "countryInfo.txt")
    OUT.mkdir(parents=True, exist_ok=True)

    rows = []
    for line in countries.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        cols = line.split("\t")
        rows.append(f"{cols[0]}\t{cols[4]}\t{cols[8]}\t{cols[1]}")
    (OUT / "countries.tsv").write_text("\n".join(sorted(rows)) + "\n", encoding="utf-8")

    out = []
    for line in cities.splitlines():
        cols = line.split("\t")
        if len(cols) < 15:
            continue
        name, ascii_name, alternates, country, population = cols[1], cols[2], cols[3], cols[8], cols[14]
        names = {fold(name), fold(ascii_name)}
        names |= {fold(a) for a in alternates.split(",") if wanted(a)}
        names.discard("")
        others = sorted(names - {fold(name)})
        out.append(f"{country}\t{int(population or 0)}\t{name}\t{'|'.join(others)}")
    # Largest first, so a reader that stops at its first match finds the city most people mean.
    out.sort(key=lambda r: -int(r.split("\t")[1]))
    # mtime 0: the same data builds the same bytes, so a rebuild that changed nothing changes no file.
    with gzip.GzipFile(OUT / "cities.tsv.gz", "wb", compresslevel=9, mtime=0) as raw:
        raw.write(("\n".join(out) + "\n").encode("utf-8"))
    print(f"{len(rows)} countries, {len(out)} places → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
