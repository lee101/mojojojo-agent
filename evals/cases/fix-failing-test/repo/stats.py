def median(values):
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def percentile(values, p):
    ordered = sorted(values)
    return ordered[int(len(ordered) * p)]
