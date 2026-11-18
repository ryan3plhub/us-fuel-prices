import copy
import json
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from carrier_rates import assemble, parse_published, select_rate


class PublishedRateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pages = json.loads((Path(__file__).parent / 'carrier_fixture_tables.json').read_text(encoding='utf-8-sig'))
        cls.now = datetime(2026, 11, 18, 12, tzinfo=timezone.utc)

    def test_current_official_amounts_and_units(self):
        expected = {'ups': (30, 'percent'), 'fedex': (30, 'percent'),
                    'ontrac': (26.25, 'percent'), 'dhl': (.32, 'usd_per_lb')}
        for carrier, (amount, unit) in expected.items():
            with self.subTest(carrier=carrier):
                rows, warnings = parse_published(carrier, self.pages[carrier])
                current = select_rate(rows, self.now.date())
                self.assertEqual((current['amount'], current['unit']), (amount, unit))

    def test_ontrac_conflicting_upcoming_dates_are_excluded(self):
        rows, warnings = parse_published('ontrac', self.pages['ontrac'])
        self.assertTrue(any('2026-11-23' in w for w in warnings))
        self.assertIsNone(select_rate(rows, date(2026, 11, 23)))

    def test_sunday_valid_monday_expired_no_clamping(self):
        rows, _ = parse_published('fedex', self.pages['fedex'])
        self.assertEqual(select_rate(rows, date(2026, 11, 22))['amount'], 30)
        self.assertIsNone(select_rate(rows, date(2026, 11, 23)))

    def test_dhl_month_rollover_and_minimum_weight(self):
        rows, _ = parse_published('dhl', self.pages['dhl'])
        self.assertEqual(select_rate(rows, date(2026, 11, 1))['amount'], .32)
        self.assertEqual(select_rate(rows, date(2026, 10, 31))['amount'], .30)
        self.assertEqual(select_rate(rows, date(2026, 12, 1))['minimum_chargeable_lb'], 1)
        self.assertIsNone(select_rate(rows, date(2027, 1, 1)))

    def test_block_page_never_becomes_rate(self):
        with self.assertRaises(ValueError):
            parse_published('ups', '<html>Access denied. Please verify you are human.</html>')

    def test_failed_carrier_preserves_timestamp_and_does_not_block_others(self):
        first, _ = assemble(self.pages, self.now, fixture=True)
        pages = copy.copy(self.pages)
        pages['fedex'] = RuntimeError('source unavailable')
        later = datetime(2026, 11, 19, 12, tzinfo=timezone.utc)
        result, problems = assemble(pages, later, first, fixture=True)
        self.assertEqual(result['carriers']['fedex']['status'], 'check_failed')
        self.assertEqual(result['carriers']['fedex']['verified_at'], first['carriers']['fedex']['verified_at'])
        self.assertEqual(result['carriers']['ups']['status'], 'verified')
        self.assertTrue(problems)

    def test_new_york_midnight_not_utc_rollover(self):
        utc_monday = datetime(2026, 11, 23, 2, tzinfo=timezone.utc)
        result, _ = assemble(self.pages, utc_monday, fixture=True)
        self.assertEqual(result['carriers']['fedex']['status'], 'verified')

    def test_future_usps_policy_not_auto_promoted(self):
        january = datetime(2027, 1, 17, 12, tzinfo=timezone.utc)
        result, problems = assemble(self.pages, january, fixture=True)
        self.assertIsNone(select_rate(result['carriers']['usps']['records'], january.date()))
        self.assertTrue(any('USPS' in p for p in problems))

    def test_reviewed_fallback_is_dated_and_does_not_hide_check_failure(self):
        pages=copy.copy(self.pages)
        pages['ups']=RuntimeError('source unavailable')
        reviewed={'ups':{'source_url':'https://www.ups.com/us/en/support/shipping-support/shipping-costs-rates/fuel-surcharges',
                         'verified_at':self.now.isoformat(), 'records':[{'effective_from':'2026-11-16','effective_through':'2026-11-22','amount':30,'unit':'percent'}]}}
        result, problems=assemble(pages,self.now,reviewed=reviewed)
        self.assertEqual(result['carriers']['ups']['status'],'reviewed_fallback')
        self.assertEqual(select_rate(result['carriers']['ups']['records'],self.now.date())['amount'],30)
        self.assertFalse(any('ups:' in p for p in problems))
        self.assertTrue(result['carriers']['ups']['warnings'])
        expired,_=assemble(pages,datetime(2026,11,23,12,tzinfo=timezone.utc),reviewed=reviewed)
        self.assertEqual(expired['carriers']['ups']['status'],'check_failed')


if __name__ == '__main__':
    unittest.main()
