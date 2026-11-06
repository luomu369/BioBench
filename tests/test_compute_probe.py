"""Compute readiness must use the model environment and report probe failures."""
import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'skills/boltzgen-deploy/scripts/preflight.py'
spec = importlib.util.spec_from_file_location('boltzgen_preflight', path)
probe = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(path.parents[2] / '_shared'))
with patch('biobench_paths.env_python', return_value=Path('/missing/compute/python')):
    spec.loader.exec_module(probe)

class ComputeProbeTests(unittest.TestCase):
    def test_missing_environment_does_not_run(self):
        with patch.object(probe, 'PY') as python, patch.object(probe, 'run_py') as run:
            python.is_file.return_value = False
            with redirect_stdout(io.StringIO()):
                self.assertFalse(probe.check_gpu())
            run.assert_not_called()

    def test_only_successful_positive_probe_passes(self):
        for code, output, expected in [(0, 'True\n', True), (0, 'False\n', False),
                                       (1, 'True\n', False), (0, '', False)]:
            with self.subTest(code=code, output=output), patch.object(probe, 'PY') as python, \
                 patch.object(probe, 'run_py', return_value=SimpleNamespace(returncode=code, stdout=output)) as run:
                python.is_file.return_value = True
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(probe.check_gpu(), expected)
                self.assertIs(run.call_args.args[0], python)
                self.assertEqual(run.call_args.kwargs['timeout'], 15)

    def test_launch_failure_is_reported(self):
        with patch.object(probe, 'PY') as python, patch.object(probe, 'run_py', side_effect=OSError('cannot launch')):
            python.is_file.return_value = True
            with redirect_stdout(io.StringIO()):
                self.assertFalse(probe.check_gpu())
