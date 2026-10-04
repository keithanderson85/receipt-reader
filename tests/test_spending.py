"""Spending by store / city: grouping, periods, drill-down, trend buckets, and the chart's accessible twin."""
import re
from datetime import datetime, timedelta

import pytest

TODAY = datetime.now().date()


def day(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


def month_start(months_back=0):
    d = TODAY.replace(day=1)
    for _ in range(months_back):
        d = (d - timedelta(days=1)).replace(day=1)
    return d


def page(client, query=''):
    res = client.get('/spending' + query)
    assert res.status_code == 200
    return res.get_data(as_text=True)


def bars(html):
    """[(store, total_text)] from the chart rows, in display order."""
    return re.findall(r'data-name="([^"]*)" data-value="(\$[\d,\.]+)"\s+data-detail="[^"]*receipt', html)


def table_rows(html):
    section = html.split('data-viz="table"')[1]
    return re.findall(r'<td>(?:<a[^>]*>)?([^<]+)(?:</a>)?</td>\s*<td class="text-end fw-semibold">(\$[\d,\.]+)</td>', section)


@pytest.fixture
def stores(add_expense):
    add_expense(merchant='Metro Pawn', amount=500.0, date=day(-2), location='Reno, NV')
    add_expense(merchant='  metro pawn ', amount=250.0, date=day(-3), location='Reno, NV')        # same store, sloppy spelling
    add_expense(merchant='Home Depot', amount=120.5, date=day(-1), location='Sparks, NV')
    add_expense(merchant='Starbucks', amount=8.25, date=day(-1), location='Reno, NV')
    add_expense(merchant='Metro Pawn', amount=40.0, date=(month_start(1) + timedelta(days=3)).isoformat(), location='Reno, NV')
    add_expense(merchant='Old Shop', amount=75.0, date='2020-06-15', location='')


# ---- access / rendering ------------------------------------------------------------------

def test_requires_login(anon):
    assert anon.get('/spending').status_code == 302


def test_empty_state(client):
    html = page(client)
    assert 'No spending in this period' in html


@pytest.mark.parametrize('query', ['', '?period=all', '?period=last_30', '?group=city', '?period=custom&start_date=2020-01-01',
                                   '?store=Nobody', '?period=last_year&group=city'])
def test_every_view_renders_with_data(client, stores, query):
    page(client, query)


# ---- grouping ---------------------------------------------------------------------------------

def test_stores_are_grouped_case_and_whitespace_insensitively_and_ranked(client, stores):
    html = page(client, '?period=all')
    assert bars(html) == [('Metro Pawn', '$790.00'), ('Home Depot', '$120.50'), ('Old Shop', '$75.00'), ('Starbucks', '$8.25')]


def test_totals_receipts_average_and_share(client, stores):
    html = page(client, '?period=this_year')
    assert 'class="hero-figure">$' in html
    assert '3 receipts · $250.00 avg' in html or '3 receipts · $263.33 avg' in html           # Metro Pawn this year (or this month only)
    shares = [float(s) for s in re.findall(r'([\d\.]+)% of total', html)]
    assert 99.0 <= sum(shares) <= 100.1


def test_city_grouping_normalises_reno_and_sparks(client, stores):
    html = page(client, '?period=all&group=city')
    names = [n for n, _ in bars(html)]
    assert names[:2] == ['Reno, NV', 'Sparks, NV'] and 'Unknown' in names
    assert '<a class="hbar-row"' not in html                                     # cities don't drill down


def test_blank_and_missing_locations_become_unknown(client, add_expense):
    add_expense(merchant='A', amount=1.0, date=day(0), location=None)
    add_expense(merchant='B', amount=2.0, date=day(0), location='   ')
    assert bars(page(client, '?period=all&group=city')) == [('Unknown', '$3.00')]


# ---- periods ----------------------------------------------------------------------------------

@pytest.mark.parametrize('period, expected', [
    ('this_month', {'Metro Pawn': 750.0, 'Home Depot': 120.5, 'Starbucks': 8.25}),
    ('last_month', {'Metro Pawn': 40.0}),
    ('all', {'Metro Pawn': 790.0, 'Home Depot': 120.5, 'Starbucks': 8.25, 'Old Shop': 75.0}),
])
def test_periods_include_only_their_dates(client, add_expense, period, expected):
    # dates are pinned inside the periods, independent of which day of the month the test runs on
    add_expense(merchant='Metro Pawn', amount=500.0, date=month_start().isoformat())
    add_expense(merchant='Metro Pawn', amount=250.0, date=month_start().isoformat())
    add_expense(merchant='Home Depot', amount=120.5, date=month_start().isoformat())
    add_expense(merchant='Starbucks', amount=8.25, date=month_start().isoformat())
    add_expense(merchant='Metro Pawn', amount=40.0, date=(month_start(1) + timedelta(days=2)).isoformat())
    add_expense(merchant='Old Shop', amount=75.0, date='2020-06-15')
    got = {n: float(v.replace('$', '').replace(',', '')) for n, v in bars(page(client, f'?period={period}'))}
    assert got == expected


def test_this_year_and_last_year_boundaries(client, add_expense):
    add_expense(merchant='NewYearsDay', amount=1.0, date=f'{TODAY.year}-01-01')
    add_expense(merchant='LastDayOfLastYear', amount=2.0, date=f'{TODAY.year - 1}-12-31')
    add_expense(merchant='FirstDayOfLastYear', amount=4.0, date=f'{TODAY.year - 1}-01-01')
    assert [n for n, _ in bars(page(client, '?period=this_year'))] == ['NewYearsDay']
    assert [n for n, _ in bars(page(client, '?period=last_year'))] == ['FirstDayOfLastYear', 'LastDayOfLastYear']


def test_last_30_days_is_inclusive_of_today_and_29_days_back(client, add_expense):
    add_expense(merchant='Today', amount=1.0, date=day(0))
    add_expense(merchant='Edge', amount=2.0, date=day(-29))
    add_expense(merchant='TooOld', amount=4.0, date=day(-30))
    assert {n for n, _ in bars(page(client, '?period=last_30'))} == {'Today', 'Edge'}


def test_custom_range_is_inclusive_and_swaps_reversed_dates(client, add_expense):
    add_expense(merchant='Before', amount=1.0, date='2025-02-28')
    add_expense(merchant='First', amount=2.0, date='2025-03-01')
    add_expense(merchant='Last', amount=4.0, date='2025-03-31')
    add_expense(merchant='After', amount=8.0, date='2025-04-01')
    for query in ('?period=custom&start_date=2025-03-01&end_date=2025-03-31', '?period=custom&start_date=2025-03-31&end_date=2025-03-01'):
        assert {n for n, _ in bars(page(client, query))} == {'First', 'Last'}
    assert {n for n, _ in bars(page(client, '?period=custom&start_date=2025-03-31'))} == {'Last', 'After'}      # open-ended
    assert 'Mar 1, 2025 to Mar 31, 2025' in page(client, '?period=custom&start_date=2025-03-01&end_date=2025-03-31')


@pytest.mark.parametrize('query', ['?period=banana', '?period=custom', '?period=custom&start_date=nope&end_date=also-nope',
                                   '?group=sideways', '?period=', '?store=%27%3B+DROP+TABLE+expenses%3B--'])
def test_garbage_parameters_fall_back_safely(client, stores, db, query):
    page(client, query)
    assert db('select count(*) from expenses')[0][0] == 6


# ---- drill-down -----------------------------------------------------------------------------------

def test_store_drilldown_scopes_everything_to_that_store(client, stores):
    html = page(client, '?period=all&store=Metro Pawn')
    assert 'Spent at Metro Pawn' in html and '$790.00' in html
    assert 'Home Depot' not in html.split('<h1')[1].split('Latest receipts')[0]              # nothing else in the headline/chart
    assert 'Latest receipts' in html and 'View all receipts' in html
    assert html.count('class="row-item"') == 3                                              # its three receipts


def test_drilldown_matches_regardless_of_case(client, stores):
    assert '$790.00' in page(client, '?period=all&store=METRO PAWN')


def test_drilldown_for_a_store_with_no_receipts_in_the_period(client, stores):
    html = page(client, '?period=last_30&store=Old Shop')
    assert 'No receipts from Old Shop in this period' in html and '$0.00' in html


def test_row_links_drill_down_and_keep_the_period(client, stores):
    html = page(client, '?period=all')
    href = re.search(r'class="hbar-row" href="(/spending\?[^"]*)"', html).group(1).replace('&amp;', '&')
    assert 'period=all' in href and 'group=store' in href and 'store=' in href
    assert client.get(href).status_code == 200


def test_custom_period_survives_the_drilldown_link(client, stores):
    html = page(client, '?period=custom&start_date=2020-01-01&end_date=2030-01-01')
    assert 'start_date=2020-01-01' in html and 'end_date=2030-01-01' in html


def test_view_all_receipts_link_carries_the_store_and_dates(client, stores):
    html = page(client, '?period=this_year&store=Starbucks')
    link = re.search(r'href="(/expenses\?[^"]*)"[^>]*>View all receipts', html).group(1).replace('&amp;', '&')
    assert 'location=Starbucks' in link and f'start_date={TODAY.year}-01-01' in link
    assert client.get(link).status_code == 200


# ---- trend buckets -------------------------------------------------------------------------------

def columns(html):
    return re.findall(r'data-name="([^"]*)" data-value="(\$[\d,\.]+)" data-detail=""', html)


def test_a_single_month_shows_one_column_per_day_zero_filled(client, add_expense):
    start = month_start()
    add_expense(merchant='A', amount=10.0, date=start.isoformat())
    add_expense(merchant='A', amount=5.0, date=start.isoformat())
    cols = columns(page(client, '?period=this_month'))
    last_day = ((start.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).day
    assert len(cols) == last_day
    assert cols[0][1] == '$15.00' and all(v == '$0.00' for _, v in cols[1:])


def test_a_long_period_shows_months(client, add_expense):
    add_expense(merchant='A', amount=10.0, date=f'{TODAY.year}-01-15')
    add_expense(merchant='A', amount=30.0, date=f'{TODAY.year}-03-02')
    cols = columns(page(client, '?period=this_year'))
    assert len(cols) == 12
    assert cols[0] == ('January ' + str(TODAY.year), '$10.00') and cols[1][1] == '$0.00' and cols[2][1] == '$30.00'


def test_trend_columns_add_up_to_the_headline_total(client, stores):
    for query in ('?period=this_year', '?period=all', '?period=last_30', '?period=all&store=Metro Pawn'):
        html = page(client, query)
        total = float(re.search(r'class="hero-figure">\$([\d,\.]+)', html).group(1).replace(',', ''))
        in_trend = sum(float(v.replace('$', '').replace(',', '')) for _, v in columns(html))
        assert round(in_trend, 2) == round(total, 2), query


def test_histories_longer_than_three_years_switch_to_yearly_columns_and_lose_nothing(client, add_expense):
    add_expense(merchant='A', amount=1.0, date='2010-01-01')
    add_expense(merchant='A', amount=2.5, date=TODAY.isoformat())
    html = page(client, '?period=all')
    cols = columns(html)
    assert len(cols) == TODAY.year - 2010 + 1 and cols[0] == ('2010', '$1.00') and cols[-1] == (str(TODAY.year), '$2.50')
    assert 'Per year' in html


def test_open_ended_custom_ranges_work(client, add_expense):
    add_expense(merchant='A', amount=1.0, date='2025-03-01')
    add_expense(merchant='A', amount=2.0, date='2025-05-20')
    assert columns(page(client, '?period=custom&start_date=2025-03-15')) == [
        ('March 2025', '$0.00'), ('April 2025', '$0.00'), ('May 2025', '$2.00')]       # from the start date to the last receipt
    only_end = columns(page(client, '?period=custom&end_date=2025-04-01'))
    assert sum(float(v.replace('$', '')) for _, v in only_end) == 1.0


def test_busiest_column_is_labelled_on_its_cap(client, add_expense):
    add_expense(merchant='A', amount=10.0, date=f'{TODAY.year}-01-15')
    add_expense(merchant='A', amount=99.0, date=f'{TODAY.year}-02-15')
    html = page(client, '?period=this_year')
    assert html.count('class="col-cap"') == 1 and 'class="col-cap">$99.00' in html


# ---- accessibility, safety, isolation -----------------------------------------------------------------

def test_every_chart_has_a_table_twin_with_the_same_numbers(client, stores):
    html = page(client, '?period=all')
    assert html.count('class="viz-chart" data-viz="chart"') == 2 and html.count('class="viz-table" data-viz="table"') == 2
    assert dict(table_rows(html)) == dict(bars(html))
    assert re.search(r'aria-label="Columns of spending per (day|month|year); busiest: ', html)


def test_the_chart_does_not_depend_on_the_tooltip_to_show_values(client, stores):
    html = page(client, '?period=all')
    assert html.count('class="hbar-value"') == 4                              # value printed at every bar tip


def test_store_names_are_escaped(client, add_expense):
    add_expense(merchant='<script>alert(1)</script>', amount=5.0, date=day(0))
    html = page(client, '?period=all')
    assert '<script>alert(1)</script>' not in html and '&lt;script&gt;alert(1)&lt;/script&gt;' in html


def test_only_my_receipts_are_counted(client, other_client, stores, add_expense):
    add_expense(user_id=2, merchant='BobsSecretShop', amount=9999.0, date=day(0))
    html = page(client, '?period=all')
    assert 'BobsSecretShop' not in html and '$9,999' not in html
    assert 'BobsSecretShop' in page(other_client, '?period=all')


def test_long_lists_chart_the_top_fifteen_but_the_table_has_everyone(client, add_expense):
    for i in range(20):
        add_expense(merchant=f'Store {i:02d}', amount=100.0 - i, date=day(0))
    html = page(client, '?period=all')
    assert len(bars(html)) == 15 and len(table_rows(html)) == 20
    assert 'top 15 of 20' in html


def test_dashboard_and_navigation_link_to_spending(client, stores):
    html = client.get('/').get_data(as_text=True)
    assert 'href="/spending"' in html and 'Spending by store' in html
