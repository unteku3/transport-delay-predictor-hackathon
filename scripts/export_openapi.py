"""Export API schemas without starting services or reading the dataset."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.app import app as backend
from ml.service.app import app as ml

if __name__ == '__main__':
    directory = ROOT / 'docs/api'
    directory.mkdir(parents=True, exist_ok=True)
    for name, app in [('backend', backend), ('ml', ml)]:
        (directory / f'{name}.json').write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2) + '\n')
