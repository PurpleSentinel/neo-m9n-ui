import unittest

from m9n.gui.layout import balanced_split


class BalancedSplitTests(unittest.TestCase):
    def test_minimises_the_tallest_column_in_order(self):
        # Dashboard-like groups: Position, Motion, DOP, Time, Identity, Session, Stream.
        heights = [380, 160, 215, 325, 230, 215, 545]
        self.assertEqual(balanced_split(heights, 2), [4, 3])
        self.assertEqual(balanced_split(heights, 3), [3, 2, 2])  # 760 tallest, not 770.

    def test_one_column_and_more_columns_than_groups(self):
        self.assertEqual(balanced_split([1, 2, 3], 1), [3])
        self.assertEqual(balanced_split([5, 5], 3), [1, 1])
        self.assertEqual(balanced_split([], 2), [])

    def test_ties_keep_left_columns_fuller(self):
        self.assertEqual(balanced_split([1, 1, 1], 2), [2, 1])

    def test_hidden_groups_weigh_nothing(self):
        self.assertEqual(sum(balanced_split([300, 0, 300], 2)), 3)


if __name__ == "__main__":
    unittest.main()
