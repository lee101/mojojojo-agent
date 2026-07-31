set -e
test -f wc.py
python -m pytest tests -q
printf 'a b\nc\n' > /tmp/mjj-wc-in.txt
python wc.py /tmp/mjj-wc-in.txt | grep -qE '^\s*2\s+3\s+6'
