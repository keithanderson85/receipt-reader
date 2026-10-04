"""Browser behaviour of the capture screen, run in jsdom (needs Node: `cd tests/frontend && npm install`)."""
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).parent / 'frontend'

pytestmark = pytest.mark.skipif(
    not shutil.which('node') or not (FRONTEND / 'node_modules' / 'jsdom').exists(),
    reason='Node and jsdom not installed (cd tests/frontend && npm install)')


def test_capture_screen_flow_in_a_simulated_browser(client, tmp_path):
    """Add a photo -> Save -> live verdict (new / duplicate / maybe / unreadable) -> Scan another."""
    html = tmp_path / 'capture.html'
    html.write_text(client.get('/capture').get_data(as_text=True), encoding='utf-8')

    result = subprocess.run(['node', str(FRONTEND / 'capture_flow.js'), str(html)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ALL PASSED' in result.stdout


def test_spending_chart_interactions_in_a_simulated_browser(client, add_expense, tmp_path):
    """Hover/focus tooltips, Chart <-> Table, and that a hostile store name stays plain text."""
    from datetime import datetime
    today = datetime.now().date().isoformat()
    add_expense(merchant='Metro Pawn #E0205', amount=500.0, date=today)
    add_expense(merchant='Home Depot', amount=120.0, date=today)
    add_expense(merchant='<img src=x onerror=alert(1)>', amount=9.0, date=today)

    html = tmp_path / 'spending.html'
    html.write_text(client.get('/spending?period=all').get_data(as_text=True), encoding='utf-8')
    result = subprocess.run(['node', str(FRONTEND / 'spending_flow.js'), str(html)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ALL PASSED' in result.stdout
