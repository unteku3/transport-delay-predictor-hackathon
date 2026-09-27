"""Generate the jury's PyDoc pages from the final runtime modules."""
from pathlib import Path
import pydoc
import os
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == '__main__':
    output = ROOT/'deploy/site/code'
    output.mkdir(parents=True, exist_ok=True)
    os.chdir(output)
    for name in ['backend.ndtp', 'ml.service.model', 'cleaning.telemetry']:
        pydoc.writedoc(name)
