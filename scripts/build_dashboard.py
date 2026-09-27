"""Embed the live adapter in the preserved Claude Design v3 layout (no npm build)."""
from pathlib import Path

root = Path(__file__).resolve().parents[1] / 'dashboard'
layout = (root / 'layout.html').read_text()
logic = (root / 'live.js').read_text()
(root / 'index.html').write_text(layout.replace('<!-- LIVE_LOGIC -->',
    '<script type="text/x-dc" data-dc-script>\n' + logic + '\n</script>'))
