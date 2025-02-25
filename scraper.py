import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime

def scrape_diesel_prices():
    url = "https://www.eia.gov/petroleum/gasdiesel/"
    print(f"Requesting URL: {url}")
    response = requests.get(url)
    print(f"Response status code: {response.status_code}")

    if response.status_code != 200:
        print(f"Failed to fetch page. Status code: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")

    # Find all tables with class="basic-table"
    all_tables = soup.find_all("table", class_="basic-table")
    print(f"Found {len(all_tables)} tables with class='basic-table'")

    if len(all_tables) < 3:
        # We’re looking for all_tables[2] (the third table),
        # so ensure at least 3 tables exist.
        print("ERROR: Could not find at least three tables. Page structure may have changed.")
        return None

    # The THIRD table is the Diesel table (index 2):
    diesel_table = all_tables[2]

    # 1) Get HEADERS from <thead>
    thead = diesel_table.find("thead")
    if not thead:
        print("ERROR: No <thead> in the Diesel table.")
        return None
    
    header_rows = thead.find_all("tr")
    # Typically there's a "double-header" row first, then the date headers second
    if len(header_rows) < 2:
        print("ERROR: Thead does not have at least 2 rows. The Diesel table structure may have changed.")
        return None
    
    # The second row in <thead> usually has the dates and "week ago", "year ago"
    date_header_row = header_rows[1]
    header_ths = date_header_row.find_all("th")
    print(f"Found {len(header_ths)} <th> in the second header row: {[th.get_text(strip=True) for th in header_ths]}")

    # The first TH is blank (region label). The rest are the actual columns we want.
    # So let's skip index 0:
    column_headers = [th.get_text(strip=True) for th in header_ths[1:]]
    print("Scraped column headers:", column_headers)

    # 2) Find 'U.S.' row in <tbody>
    tbody = diesel_table.find("tbody")
    if not tbody:
        print("ERROR: No <tbody> in the Diesel table.")
        return None

    rows = tbody.find_all("tr", recursive=False)
    print(f"Found {len(rows)} rows in diesel table's <tbody>.")

    us_row = None
    for i, tr in enumerate(rows):
        first_td = tr.find("td")
        txt = first_td.get_text(strip=True) if first_td else ""
        print(f"Row #{i} first cell: '{txt}'")
        if "U.S." in txt:
            us_row = tr
            print(f"FOUND the 'U.S.' row at index {i}.")
            break

    if not us_row:
        print("ERROR: Could not find the 'U.S.' row in the Diesel table.")
        return None

    # Extract the data cells
    us_cells = us_row.find_all("td")
    print(f"U.S. row has {len(us_cells)} <td> cells.")
    for idx, td in enumerate(us_cells):
        print(f"Cell #{idx} => '{td.get_text(strip=True)}'")

    if len(us_cells) < 6:
        print("ERROR: Expected at least 6 <td> in the U.S. row (one for 'U.S.', plus 5 columns).")
        return None

    # The first cell is "U.S.", the rest match each header
    data = {
        "region": us_cells[0].get_text(strip=True),
        "date_scraped": datetime.now().isoformat()
    }

    # Pair each column header with the corresponding <td>
    for i, header_name in enumerate(column_headers, start=1):
        data[header_name] = us_cells[i].get_text(strip=True)

    return data


def save_to_json(data, filename="fuel_prices.json"):
    """
    Overwrite any existing file with the new diesel data in JSON format.
    """
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)
    print(f"JSON saved to '{filename}'")


if __name__ == "__main__":
    diesel_data = scrape_diesel_prices()
    if diesel_data:
        print("\nSuccessfully scraped Diesel Data:")
        print(diesel_data)
        save_to_json(diesel_data, "diesel_prices.json")
    else:
        print("\nNo data scraped (something went wrong).")
