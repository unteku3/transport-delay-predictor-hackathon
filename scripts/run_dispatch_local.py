"""Start the same four modules without Docker; Ctrl-C stops all owned processes."""
import os
from pathlib import Path
import subprocess
import sys
import time

root = Path(__file__).resolve().parents[1]
env = dict(os.environ, DATA_ROOT=os.getenv('DATA_ROOT', str(root)), MODEL_DIR=str(root/'ml/models/optuna_features_v2'),
           ML_URL='http://127.0.0.1:8001', EMULATOR_URL='http://127.0.0.1:18080',
           NDTP_HOST='127.0.0.1', NDTP_BIND='127.0.0.1', PYTHONDONTWRITEBYTECODE='1', MPLCONFIGDIR='/tmp/predictor-mpl')
children = []
try:
    for module, port in [('ml.service.app',8001), ('backend.app',8000),
                         ('backend.emulator.app',18080), ('dashboard.server',8080)]:
        children.append(subprocess.Popen([sys.executable,'-m','uvicorn',module+':app',
            '--host','127.0.0.1','--port',str(port),'--no-access-log','--timeout-graceful-shutdown','3'], cwd=root, env=env))
    print('Dashboard http://localhost:8080 · Swagger http://localhost:8000/docs', flush=True)
    while all(p.poll() is None for p in children):
        time.sleep(.5)
    raise SystemExit('A service stopped; shutting down the stack')
except KeyboardInterrupt:
    pass
finally:
    for p in children:
        if p.poll() is None:
            p.terminate()
    for p in children:
        try:
            p.wait(timeout=8)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
