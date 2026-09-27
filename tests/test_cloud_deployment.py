"""Finished replay must reset both stream producer and consumer, in that order."""
import io
import json
import runpy
from pathlib import Path
from unittest.mock import patch
import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'deploy/restart_finished_replay.py'

@pytest.mark.parametrize('finished', [False, True])
def test_restart_only_after_end(finished):
    with patch('urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'finished':finished}).encode())), patch('subprocess.run') as run:
        runpy.run_path(str(SCRIPT), run_name='__main__')
    commands = [call.args[0] for call in run.call_args_list]
    if not finished:
        assert commands == []
    else:
        assert len(commands) == 2
        assert commands[0][-3:] == ['stop','emulator','backend']
        assert commands[1][-4:] == ['up','-d','backend','emulator']
