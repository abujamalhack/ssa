import unittest

import geo


class TestGeo(unittest.TestCase):
    def test_same_point(self):
        self.assertAlmostEqual(geo.haversine(15.0, 44.0, 15.0, 44.0), 0)

    def test_known_distance(self):
        self.assertTrue(110000 < geo.haversine(0, 0, 0, 1) < 112000)

    def test_accuracy_score(self):
        self.assertGreater(geo.quality(5), geo.quality(100))

    def test_jump(self):
        a = {'lat': 0, 'lon': 0, 'acc': 10}
        b = {'lat': 60, 'lon': 60, 'acc': 10}
        self.assertFalse(geo.plausible(a, b, 1))


if __name__ == '__main__':
    unittest.main()
