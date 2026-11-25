"""A large matrix must not hide the decision for which the tool was called."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


class CompactComparisonReceipt(unittest.TestCase):
    def test_decision_survives_large_matrix(self):
        labels = list('ABCDE')
        for order, expected in ((labels, 'different'), (labels[::-1], 'equal'), (None, 'underdetermined')):
            with self.subTest(expected=expected), tempfile.TemporaryDirectory() as temp:
                directory = Path(temp)
                payload = {'operation': 'hamiltonian', 'n_qubits': 5, 'energy_unit': 'eV',
                           'terms': [{'pauli': 'XIIII', 'coefficient': 1}], 'compare': {
                               'energy_unit': 'eV', 'left_order': labels, 'right_order': order,
                               'terms': [{'pauli': 'IIIIX', 'coefficient': 1}]}}
                request = directory / 'request.json'
                request.write_text(json.dumps(payload), encoding='utf-8')
                call = subprocess.run([sys.executable, '-B', str(Path(__file__).with_name('action.py')),
                                       '--request', str(request), '--output', str(directory / 'output')],
                                      capture_output=True, text=True, timeout=15, check=True)
                receipt = json.loads(call.stdout)
                detailed = json.loads((directory / 'output' / 'result.json').read_text(encoding='utf-8'))
                self.assertEqual(receipt['artifact'], 'result.json')
                self.assertLessEqual(len(call.stdout.strip()), 3000)
                self.assertNotIn('matrix', receipt)
                self.assertEqual(detailed['result']['dimension'], 32)
                self.assertEqual(receipt['comparison']['verdict'], expected)
                self.assertEqual(detailed['result']['comparison']['verdict'], expected)
                if expected == 'different':
                    self.assertEqual(receipt['comparison']['witness'],
                                     {'pauli': 'IIIIX', 'left_coefficient_exact': '0', 'right_coefficient_exact': '1'})
                if expected == 'underdetermined':
                    self.assertEqual(receipt['comparison']['reason'], detailed['result']['comparison']['reason'])


if __name__ == '__main__':
    unittest.main()