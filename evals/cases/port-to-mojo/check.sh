set -e
test -f bench.py
python -c "
import random, hot
random.seed(7)
n=1000
a=[random.uniform(-89,89) for _ in range(n)]
b=[random.uniform(-179,179) for _ in range(n)]
c=[random.uniform(-89,89) for _ in range(n)]
d=[random.uniform(-179,179) for _ in range(n)]
r=hot.haversine_batch(a,b,c,d)
assert len(r)==n and all(0<=x<20100 for x in r)
assert abs(hot.haversine_batch([0.0],[0.0],[0.0],[1.0])[0]-111.19)<0.05
print('numerics ok')
"
python bench.py | grep -qiE 'ms|us|s\b'
