"""Column balancing for multi-column pages. GTK-free so it can be unit-tested."""
from itertools import combinations


def balanced_split(heights, count):
    """Cut heights, in order, into at most count runs minimising the tallest; returns the run lengths.

    Reading order is kept: the first run fills the first column, the next run the next.
    Ties go to the latest cuts, which keeps the left columns the fuller ones.
    """
    if not heights:
        return []
    count = max(1, min(count, len(heights)))
    best = None
    for cuts in combinations(range(1, len(heights)), count - 1):
        bounds = (0, *cuts, len(heights))
        tallest = max(sum(heights[bounds[i]:bounds[i + 1]]) for i in range(count))
        if best is None or tallest <= best[0]:
            best = (tallest, [bounds[i + 1] - bounds[i] for i in range(count)])
    return best[1]
