"""Measure the public listings API. Stdlib Python 3.8+ and system curl."""
import argparse
import csv
from datetime import datetime, timezone
import getpass
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit


def sample(curl, url, auth_header=None):
    # Send the header via stdin, never in the URL, argv or the benchmark CSV.
    extra = ['--header', '@-'] if auth_header else []
    # New curl process per request, matching the original PowerShell benchmark.
    p = subprocess.run(
        [curl, '-q', *extra, '-sS', '--max-time', '20', '-D', '-', '-o', os.devnull,
         '-w', '\\nH1 %{http_code} %{time_total}', '--', url],
        input=auth_header, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    data = p.stdout.decode('latin1')
    timings = re.search(r'\nH1 (\d{3}) ([\d.]+)\s*$', data)
    status, ms = (int(timings[1]), float(timings[2])*1000) if timings else (0, None)
    modes = re.findall(r'^X-WagonHub-DB-Mode:[ \t]*([^\r\n]+)', data, re.I | re.M)
    return status, ms, modes[-1].strip() if modes else '', p.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='Base URL of deployment, without path')
    parser.add_argument('--mode', choices=['direct', 'pool'], required=True)
    parser.add_argument('--requests', type=int, default=100)
    parser.add_argument('--preview-auth', action='store_true', help='Prompt for the WagonHub Vercel automation secret')
    parser.add_argument('--out', type=Path, help='New CSV, never overwrite an existing run')
    args = parser.parse_args()
    target = urlsplit(args.url)
    if (target.scheme not in ['http', 'https'] or not target.hostname or target.username is not None
            or target.path not in ('', '/') or target.query or target.fragment):
        parser.error('Use a base URL, for example https://www.wagonhub.ru')
    if not 1 <= args.requests <= 2000:
        parser.error('requests must be between 1 and 2000')
    curl = shutil.which('curl.exe' if os.name == 'nt' else 'curl')
    if not curl:
        parser.error('curl not found in PATH')
    url = args.url.rstrip('/') + '/api/listings?limit=50'
    auth_header = None
    if args.preview_auth:
        if (target.scheme != 'https' or target.port not in (443, None) or
            not re.fullmatch(r'wagonhub-vercel-[a-z0-9-]+-art4iba2s-projects\.vercel\.app', target.hostname)):
            parser.error('Use a verified HTTPS preview URL of the WagonHub project for --preview-auth')
        secret = getpass.getpass('Vercel automation secret (hidden): ').strip()
        if not re.fullmatch(r'[!-~]{1,2048}', secret):
            parser.error('Expected a non-empty printable ASCII secret')
        auth_header = ('x-vercel-protection-bypass: ' + secret + '\n').encode('ascii')
    for _ in range(5):
        code, _, mode, rc = sample(curl, url, auth_header)
        if rc or code != 200 or mode != args.mode:
            raise SystemExit(f'Warm-up failed: HTTP={code}, mode={mode!r}, curl={rc}. Check URL, access and deployment.')
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    dest = args.out or Path(f'h1-{args.mode}-{stamp}.csv')
    times = []
    with dest.open('x', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(['utc', 'request', 'url', 'expected_mode', 'actual_mode', 'http', 'curl_exit', 'total_ms'])
        for i in range(1, args.requests+1):
            code, ms, mode, rc = sample(curl, url, auth_header)
            writer.writerow([datetime.now(timezone.utc).isoformat(), i, url, args.mode, mode,
                             code, rc, '' if ms is None else f'{ms:.3f}'])
            if code == 200 and rc == 0:
                if mode != args.mode:
                    raise SystemExit(f'Mode changed during run; discard partial series {dest}')
                times.append(ms)
    times.sort()
    print(f'{args.mode}: OK={len(times)}/{args.requests}, errors={args.requests-len(times)}, CSV={dest}')
    if not times:
        raise SystemExit('No successful requests: p95 unavailable')
    for p in (50, 95):
        print(f'p{p}: {times[math.ceil(len(times)*p/100)-1]:.2f} ms')


if __name__ == '__main__':
    main()
