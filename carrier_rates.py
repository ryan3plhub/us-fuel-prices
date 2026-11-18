"""Published domestic carrier fuel rates. Never infer rates from latest EIA prices.

Run with --fixtures DIR for offline verification; production renders official
pages with Chromium because some carrier tables are populated by JavaScript.
Output includes explicit validity windows and per-carrier failure status.
"""
import argparse
import hashlib
import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

SOURCES = {
    'ups': 'https://www.ups.com/us/en/support/shipping-support/shipping-costs-rates/fuel-surcharges',
    'fedex': 'https://www.fedex.com/en-us/shipping/fuel-surcharge.html',
    'ontrac': 'https://www.ontrac.com/surchargesandrates/',
    'dhl': 'https://www.dhl.com/us-en/home/ecommerce/business-help-center/surcharge-policies.html',
}
SERVICES = {
    'ups': 'UPS Domestic Ground',
    'fedex': 'FedEx Ground / Home Delivery',
    'ontrac': 'OnTrac national diesel-based schedule',
    'dhl': 'DHL eCommerce domestic',
}
DATE_TOKEN = r'(?:[A-Za-z]+\.?\s+\d{1,2},?\s+\d{4}|\d{1,2}/\d{1,2}/\d{4})'


def parse_date(value):
    cleaned = re.sub(r'[.,]', '', value.strip())
    for fmt in ('%B %d %Y', '%b %d %Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            pass
    raise ValueError(f'Unrecognized date: {value}')


def period(value, carrier):
    if carrier == 'dhl':
        m = re.fullmatch(r'([A-Za-z]+)\s+(\d{1,2})\s*-\s*(\d{1,2}),\s*(\d{4})', value)
        if not m:
            raise ValueError('Unrecognized monthly date range')
        month, start, end, year = m.groups()
        return parse_date(f'{month} {start} {year}'), parse_date(f'{month} {end} {year}')
    dates = re.findall(DATE_TOKEN, value)
    if carrier == 'ups' and len(dates) == 1:
        start = parse_date(dates[0])
        # UPS weekly history publishes start dates only; do not carry forward indefinitely.
        return start, start + timedelta(days=6)
    if len(dates) != 2:
        raise ValueError('Expected explicit start and end dates')
    return tuple(map(parse_date, dates))


def parse_published(carrier, html):
    soup = BeautifulSoup(html, 'html.parser')
    records = []
    for table in soup.find_all('table'):
        rows = [[c.get_text(' ', strip=True).replace('\xa0', ' ')
                 for c in tr.find_all(['th', 'td'], recursive=False)]
                for tr in table.find_all('tr')]
        header = ' '.join(' '.join(r) for r in rows[:2]).lower()
        required = {
            'ups': ('effective start date', 'domestic ground surcharge'),
            'fedex': ('effective date', 'fedex ground', 'diesel'),
            'ontrac': ('effective date', 'fuel surcharge'),
            'dhl': ('effective dates', 'domestic products', 'international products'),
        }[carrier]
        if not all(s in header for s in required):
            continue
        for cells in rows:
            if len(cells) < 2 or not re.search(r'\d{4}', cells[0]):
                continue
            start, end = period(cells[0], carrier)
            if start > end or (end - start).days > (30 if carrier == 'dhl' else 6):
                raise ValueError('Invalid effective period')
            if carrier == 'ups' and start.weekday() != 0:
                raise ValueError('UPS weekly start is not Monday')
            pattern = r'\$(\d+(?:\.\d+)?)\s+USD\s+per\s+pound' if carrier == 'dhl' else r'(\d+(?:\.\d+)?)\s*%'
            match = re.fullmatch(pattern, cells[1], re.I)
            if not match:
                raise ValueError('Unexpected rate units or column layout')
            amount = float(match[1])
            if not 0 <= amount <= (5 if carrier == 'dhl' else 100):
                raise ValueError('Rate outside validation bounds')
            record = {'effective_from': start.isoformat(), 'effective_through': end.isoformat(),
                      'amount': amount, 'unit': 'usd_per_lb' if carrier == 'dhl' else 'percent'}
            if carrier == 'dhl':
                record['minimum_chargeable_lb'] = 1
            if record not in records:
                records.append(record)
    if not records:
        raise ValueError('No recognizable published-rate table; refusing to guess')
    # Reject all rows for a start date if two source tables disagree on amount OR end date.
    starts = {r['effective_from'] for r in records}
    conflicting = [s for s in starts if sum(r['effective_from'] == s for r in records) > 1]
    warnings = [f'Conflicting published period or amount starting {s}; excluded' for s in sorted(conflicting)]
    records = sorted([r for r in records if r['effective_from'] not in conflicting], key=lambda r: r['effective_from'])
    for left, right in zip(records, records[1:]):
        if left['effective_through'] >= right['effective_from']:
            raise ValueError('Overlapping published periods')
    return records, warnings


def select_rate(records, today):
    day = today.isoformat()
    matches = [r for r in records if r['effective_from'] <= day <= r['effective_through']]
    if len(matches) > 1:
        raise ValueError('Ambiguous effective rate')
    return matches[0] if matches else None


def assemble(pages, now, previous=None, fixture=False):
    today = now.astimezone(ZoneInfo('America/New_York')).date()
    result = {'schema_version': 1, 'generated_at': now.isoformat(),
              'timezone': 'America/New_York', 'test_data': fixture, 'carriers': {}}
    problems = []
    previous = previous or {}
    for carrier, url in SOURCES.items():
        entry = {'service': SERVICES[carrier], 'source_url': url, 'last_attempt_at': now.isoformat()}
        try:
            html = pages[carrier]
            if isinstance(html, Exception):
                raise html
            records, warnings = parse_published(carrier, html)
            entry.update(records=records, verified_at=now.isoformat(),
                         source_sha256=hashlib.sha256(html.encode()).hexdigest(), warnings=warnings)
            entry['status'] = 'verified' if select_rate(records, today) else 'unavailable'
            if entry['status'] != 'verified':
                problems.append(f'{carrier}: no published rate covering {today}')
            problems.extend(f'{carrier}: {w}' for w in warnings)
        except Exception as error:
            old = previous.get('carriers', {}).get(carrier, {})
            entry.update(records=old.get('records', []), verified_at=old.get('verified_at'),
                         status='check_failed', error=str(error)[:250])
            problems.append(f'{carrier}: {error}')
        result['carriers'][carrier] = entry
    # USPS is a dated policy, not an EIA-derived rate. Never auto-promote a proposal.
    result['carriers']['usps'] = {
        'service': 'USPS specified domestic competitive products',
        'source_url': 'https://about.usps.com/newsroom/national-releases/2026/0325-usps-announces-transportation-related-time-limited-price-change.htm',
        'status': 'reviewed_policy', 'reviewed_on': '2026-11-18',
        'records': [{'effective_from': '2026-04-26', 'effective_through': '2027-01-16',
                     'amount': 8, 'unit': 'percent_base_price_adjustment'}],
        'note': 'Already incorporated in affected base postage prices; do not add again. Proposed January 17 changes require review before activation.'}
    if today >= date(2027, 1, 3):
        problems.append('USPS: review January policy before January 17; no future rate inferred')
    return result, problems


def fetch_pages():
    from playwright.sync_api import sync_playwright
    pages = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(channel=os.environ.get('CARRIER_CHROME_CHANNEL') or None)
        try:
            for carrier, url in SOURCES.items():
                page = browser.new_page(locale='en-US')
                try:
                    response = page.goto(url, wait_until='domcontentloaded', timeout=60000)
                    if not response or response.status >= 400:
                        raise RuntimeError('Official source returned HTTP error')
                    # Data rows must exist; a challenge page is a failure, not a source.
                    page.locator('table').filter(has_text=re.compile('Effective', re.I)).first.wait_for(timeout=30000)
                    page.wait_for_function("() => Array.from(document.querySelectorAll('table')).some(t => /20[0-9]{2}/.test(t.innerText) && /%|per pound/i.test(t.innerText))", timeout=30000)
                    pages[carrier] = page.content()
                except Exception as error:
                    pages[carrier] = RuntimeError(type(error).__name__ + ': carrier source unavailable')
                finally:
                    page.close()
        finally:
            browser.close()
    return pages


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixtures', type=Path)
    parser.add_argument('--at', help='ISO timestamp; only allowed with --fixtures')
    parser.add_argument('--output', type=Path, default=Path('carrier_rates.json'))
    args = parser.parse_args()
    if args.at and not args.fixtures:
        parser.error('--at is only for offline tests')
    now = datetime.fromisoformat(args.at) if args.at else datetime.now(timezone.utc)
    if now.tzinfo is None:
        parser.error('Timestamp must include timezone')
    pages = {c: (args.fixtures / f'{c}.html').read_text(encoding='utf-8') for c in SOURCES} if args.fixtures else fetch_pages()
    previous = json.loads(args.output.read_text()) if args.output.exists() else {}
    result, problems = assemble(pages, now, previous, bool(args.fixtures))
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    temporary.replace(args.output)
    for problem in problems:
        print('::error::' + problem.replace('\n', ' '))
    return 1 if problems else 0


if __name__ == '__main__':
    raise SystemExit(main())
