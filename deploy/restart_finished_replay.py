#!/usr/bin/env python3
"""Start a fresh demo session after a finite replay ends; never rewind live state."""
import json
import subprocess
import urllib.request

try:
    with urllib.request.urlopen('http://127.0.0.1:18080/status', timeout=10) as response:
        state = json.load(response)
except (OSError, ValueError):
    raise SystemExit(0)
if state.get('finished'):
    subprocess.run(['docker', 'compose', '-f', 'compose.yaml', '-f',
                    'deploy/compose.cloud.yaml', 'stop', 'emulator', 'backend'], check=True)
    subprocess.run(['docker', 'compose', '-f', 'compose.yaml', '-f',
                    'deploy/compose.cloud.yaml', 'up', '-d', 'backend', 'emulator'], check=True)
