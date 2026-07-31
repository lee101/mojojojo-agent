from stats import median, percentile


def test_median_even_length_averages_the_middle_pair():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_odd_length():
    assert median([5, 1, 3]) == 3


def test_percentile_does_not_run_off_the_end():
    assert percentile([1, 2, 3, 4], 1.0) == 4
