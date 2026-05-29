import json
import os
import sys
from datetime import datetime

import requests
from bs4 import BeautifulSoup

EIA_URL = "https://www.eia.gov/petroleum/gasdiesel/"

# Keys that aren't a date column -- so we ignore them when deciding
# whether the data is "new".
NON_DATE_KEYS = {"region", "date_scraped", "week ago", "year ago", "2 year ago"}


def fetch_eia():
    """Download the EIA gas+diesel page."""
    print(f"GET {EIA_URL}")
    response = requests.get(EIA_URL, timeout=30)
    print(f"Status: {response.status_code}")
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def parse_table(table, label):
    """
    Extract the U.S. row + date column headers from an EIA basic-table.

    Returns a dict like:
      {"region": "U.S.", "05/04/26": "5.640", "05/11/26": "5.639", ...,
       "week ago": "-0.043", "year ago": "2.06"}
    """
    thead = table.find("thead")
    if not thead:
        raise RuntimeError(f"[{label}] no <thead>")

    header_rows = thead.find_all("tr")
    if len(header_rows) < 2:
        raise RuntimeError(f"[{label}] <thead> doesn't have 2 rows")

    # Second header row carries the actual date columns + week ago / year ago.
    # First TH is a blank region-label cell, so we skip index 0.
    date_header_row = header_rows[1]
    header_ths = date_header_row.find_all("th")
    column_headers = [th.get_text(strip=True) for th in header_ths[1:]]
    print(f"[{label}] columns: {column_headers}")

    tbody = table.find("tbody")
    if not tbody:
        raise RuntimeError(f"[{label}] no <tbody>")

    rows = tbody.find_all("tr", recursive=False)
    us_row = None
    for tr in rows:
        first_td = tr.find("td")
        if first_td and "U.S." in first_td.get_text(strip=True):
            us_row = tr
            break
    if not us_row:
        raise RuntimeError(f"[{label}] could not find U.S. row")

    us_cells = us_row.find_all("td")
    if len(us_cells) < len(column_headers) + 1:
        raise RuntimeError(
            f"[{label}] U.S. row has {len(us_cells)} cells, "
            f"expected at least {len(column_headers) + 1}"
        )

    data = {"region": us_cells[0].get_text(strip=True)}
    for i, header_name in enumerate(column_headers, start=1):
        data[header_name] = us_cells[i].get_text(strip=True)
    return data


def scrape_eia():
    """
    Returns {"gasoline": {...}, "diesel": {...}}.

    EIA's gasdiesel page renders three "basic-table" tables:
      [0] Retail prices: Gasoline
      [1] Wholesale / spot
      [2] Retail prices: Diesel
    """
    soup = fetch_eia()
    all_tables = soup.find_all("table", class_="basic-table")
    print(f"Found {len(all_tables)} basic-tables")
    if len(all_tables) < 3:
        raise RuntimeError(
            f"Expected >=3 basic-tables on EIA page, found {len(all_tables)}. "
            "Page structure may have changed."
        )
    return {
        "gasoline": parse_table(all_tables[0], "gasoline"),
        "diesel": parse_table(all_tables[2], "diesel"),
    }


def load_existing(filename):
    """Return the existing JSON or {} if missing/unreadable."""
    if not os.path.exists(filename):
        return {}
    try:
        with open(filename) as f:
            return json.load(f)
    except Exception as e:
        print(f"Could not read {filename}: {e}")
        return {}


def has_real_change(new_data, existing):
    """
    True when the new scrape contains a date column the existing JSON
    doesn't have, or when a shared date column has a different value.

    We deliberately ignore date_scraped / week ago / year ago when
    comparing -- those derived values can shift even when the weekly
    snapshot hasn't been updated yet.
    """
    new_dates = {k: v for k, v in new_data.items() if k not in NON_DATE_KEYS}
    old_dates = {k: v for k, v in existing.items() if k not in NON_DATE_KEYS}
    return new_dates != old_dates


def save_if_new(data, filename):
    """
    Write the JSON only if the date columns have actually changed.
    Returns True when a write happened.
    """
    existing = load_existing(filename)
    if existing and not has_real_change(data, existing):
        print(
            f"{filename}: no new data from EIA "
            f"(date columns unchanged). Skipping write."
        )
        return False
    data["date_scraped"] = datetime.utcnow().isoformat() + "Z"
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"{filename}: wrote new data.")
    return True


if __name__ == "__main__":
    try:
        result = scrape_eia()
    except Exception as e:
        print(f"ERROR: scrape failed: {e}", file=sys.stderr)
        sys.exit(1)

    diesel_updated = save_if_new(result["diesel"], "diesel_prices.json")
    gasoline_updated = save_if_new(result["gasoline"], "gasoline_prices.json")

    if not (diesel_updated or gasoline_updated):
        print("No new data this run. (EIA may not have published yet.)")
        # Exit 0 -- the commit step will detect "no changes" and skip.
        sys.exit(0)

    print("Done.")
