import copy
import unittest
from scripts.pipelines.promotion import require_run_identity, require_pinned_approval


class PendingBindings(unittest.TestCase):
    def test_identity_current_run(self):
        require_run_identity({'run_id': 10}, 10)
        with self.assertRaises(AssertionError):
            require_run_identity({'run_id': 9}, 10)

    def test_approval_complete_pinned_reference(self):
        expected = {'id': 1, 'type_id': 'approval-type', 'version': 1,
                    'resource': {'id': '2', 'type': 'environment'}}
        run = {'checkConfigurationRef': {'id': 1, 'type': {'id': 'approval-type'},
               'version': 1, 'resource': {'id': '2', 'type': 'environment'}}}
        require_pinned_approval(run, expected)
        for path, wrong in [(('id',), 2), (('type', 'id'), 'wrong'),
                            (('version',), 2), (('resource', 'id'), '3'),
                            (('resource', 'type'), 'endpoint')]:
            with self.subTest(path=path):
                bad = copy.deepcopy(run)
                target = bad['checkConfigurationRef']
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = wrong
                with self.assertRaises(AssertionError):
                    require_pinned_approval(bad, expected)


if __name__ == '__main__':
    unittest.main()
