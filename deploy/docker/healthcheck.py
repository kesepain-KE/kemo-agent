import urllib.request

with urllib.request.urlopen('http://127.0.0.1:1357/api/health', timeout=3) as response:
    if response.status != 200:
        raise SystemExit(1)

