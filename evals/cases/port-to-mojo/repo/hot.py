import math


def haversine_batch(lat1, lon1, lat2, lon2):
    """Great-circle distance in km for parallel coordinate arrays."""
    out = []
    for i in range(len(lat1)):
        p1 = math.radians(lat1[i])
        p2 = math.radians(lat2[i])
        dp = p2 - p1
        dl = math.radians(lon2[i] - lon1[i])
        a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        out.append(2 * 6371.0 * math.asin(math.sqrt(a)))
    return out
