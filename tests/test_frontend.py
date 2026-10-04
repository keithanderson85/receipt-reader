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
