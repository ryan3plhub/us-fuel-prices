/* Browser/Node shared selection logic; never display expired or test data. */
(function (root) {
  'use strict';
  function localDate(now) {
    var parts = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit'
    }).formatToParts(now);
    function part(type) { return parts.filter(function (p) { return p.type === type; })[0].value; }
    return part('year') + '-' + part('month') + '-' + part('day');
  }
  function select(feed, carrier, now) {
    now = now || new Date();
    if (!feed || feed.schema_version !== 1 || feed.test_data !== false) return null;
    var entry = feed.carriers && feed.carriers[carrier];
    if (!entry || !Array.isArray(entry.records)) return null;
    if (carrier !== 'usps') {
      if (['verified', 'check_failed'].indexOf(entry.status) === -1) return null;
      var age = now.getTime() - Date.parse(entry.verified_at);
      if (!Number.isFinite(age) || age < -300000 || age > 72 * 60 * 60 * 1000) return null;
    } else if (entry.status !== 'reviewed_policy') return null;
    var day = localDate(now);
    var matches = entry.records.filter(function (r) {
      return r.effective_from <= day && day <= r.effective_through;
    });
    if (matches.length !== 1) return null;
    var rate = matches[0];
    var expected = carrier === 'dhl' ? 'usd_per_lb' : carrier === 'usps' ? 'percent_base_price_adjustment' : 'percent';
    if (rate.unit !== expected || !Number.isFinite(rate.amount) || rate.amount < 0 || rate.amount > (carrier === 'dhl' ? 5 : 100)) return null;
    return { amount: rate.amount, unit: rate.unit, effective_from: rate.effective_from,
      effective_through: rate.effective_through, source_url: entry.source_url,
      verified_at: entry.verified_at || entry.reviewed_on,
      check_failed: entry.status === 'check_failed' };
  }
  var api = {select: select, localDate: localDate};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.CarrierRates = api;
})(typeof window !== 'undefined' ? window : this);
