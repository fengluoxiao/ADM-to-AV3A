"""Independent original-Rust receipts and dynamic streaming invariants."""
import json
from pathlib import Path
import unittest
import numpy as np
from av3s import jocforge as core


class JocforgeTests(unittest.TestCase):
    def test_matches_unmodified_rust_reducer(self):
        receipts = json.loads(Path(__file__).with_name('fixtures').joinpath('jocforge-reference.json').read_text())
        for case in receipts['cases']:
            with self.subTest(mode=case['mode']):
                i = np.arange(28)
                signal = .02 * np.sin((np.arange(96)[:, None] + i[None, :] * 7) * .17) + .001 * (i + 1)
                if case['mode'] == 1:
                    signal *= 100
                positions = np.stack([-1 + i * .07, -.4 + i * .02, 1 + i * .01], axis=1)
                energy = (signal ** 2).mean(axis=0)
                cost = core.distances(core.signature(signal), energy, positions)
                semantics = [j if j < 7 else None for j in range(28)] if case['mode'] == 2 else None
                seeds = core.choose_seeds(cost, energy, 15, semantics)
                assigned = core.assign(cost, seeds)
                gains, centers, mixed = core.aggregate(signal, energy, positions, assigned, 15)
                np.testing.assert_array_equal(seeds, case['seeds'])
                np.testing.assert_array_equal(assigned, case['assignments'])
                np.testing.assert_allclose(gains, case['gains'], atol=1e-12)
                np.testing.assert_allclose(centers, case['positions'], atol=1e-12)
                np.testing.assert_allclose(mixed[[0, 47, 95]].T, case['signal'], atol=1e-12)

    def test_continuity_changes_only_when_benefit_exceeds_penalty(self):
        seeds = np.array([0, 2])
        cost = np.array([[0, .2, .3], [.15, 0, .1], [.3, .1, 0.]])
        np.testing.assert_array_equal(core.assign(cost, seeds, np.array([0, 0, 1])), [0, 0, 1])
        np.testing.assert_array_equal(core.assign(cost, seeds, np.array([0, 0, 1]), continuity=0), [0, 1, 1])

    def test_silent_sources_are_accounted_for(self):
        signal = np.zeros((7, 20))
        energy = np.zeros(20)
        positions = np.zeros((20, 3))
        seeds = core.choose_seeds(core.distances(core.signature(signal), energy, positions), energy, 15)
        assigned = core.assign(core.distances(core.signature(signal), energy, positions), seeds)
        gains, _, mixed = core.aggregate(signal, energy, positions, assigned, 15)
        self.assertTrue(np.all(gains > 0))
        self.assertEqual(len(set(assigned)), 15)
        self.assertTrue(np.isfinite(mixed).all())
