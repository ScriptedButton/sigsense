#!/usr/bin/env python3
"""Resume and checksum-verify a size-bounded RF dataset bundle."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import subprocess
import time
import shutil

REPO = Path(__file__).resolve().parents[3]


def fetch(url, path, size=None, md5=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and (size is None or path.stat().st_size == size):
        if md5 is None or digest(path) == md5:
            print(f'VERIFIED {path.name}', flush=True)
            return str(path)
    partial = path.with_name(path.name + '.part')
    print(f'DOWNLOADING {path.name} expected_bytes={size}', flush=True)
    subprocess.run(['curl', '--fail', '--location', '--silent', '--show-error',
        '--retry', '5', '--retry-delay', '5', '--connect-timeout', '20',
        '--speed-time', '120', '--speed-limit', '1024', '--continue-at', '-',
        '--output', str(partial), url], check=True)
    if size is not None and partial.stat().st_size != size:
        raise ValueError(f'Size mismatch: {path.name}')
    if md5 is not None and digest(partial) != md5:
        raise ValueError(f'Checksum mismatch: {path.name}; keep partial for inspection')
    partial.replace(path)
    print(f'VERIFIED {path.name} bytes={path.stat().st_size}', flush=True)
    return str(path)


def digest(path):
    h = hashlib.md5()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=REPO / 'data' / 'raw')
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--bundle', choices=['compact', 'all'], default='compact')
    parser.add_argument('--max-gb', type=float, default=3., help='Refuse bundles exceeding this download budget (decimal GB)')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    radio = args.root / 'radiopile'
    for name in ('validation_report.json', 'README.md', 'LICENSES.md'):
        if not (radio / name).is_file():
            fetch(f'https://zenodo.org/records/22877108/files/{name}', radio / name)
    report = json.loads((radio / 'validation_report.json').read_text())
    compact = {'rml2016_10a', 'its_band', 'lte_wifi_dvbt'}
    selected = compact if args.bundle == 'compact' else compact | {'nist_wifi_bluetooth', 'radchar', 'rml2018_01a', 'sub_ghz'}
    if not selected <= report['datasets'].keys():
        parser.error('Publisher validation report is missing requested sources')
    jobs = []
    for name, info in report['datasets'].items():
        if name not in selected:
            continue
        file = info['file']
        jobs.append((f'https://zenodo.org/records/22877108/files/{name}.h5', radio / f'{name}.h5', file['size_bytes'], file['md5']))
    jobs.sort(key=lambda row: row[2])
    if args.bundle == 'all':
        jobs.insert(3, ('https://www.panoradio-sdr.de/wp-content/uploads/dataset_panoradio_hf.npy', args.root / 'panoradio' / 'iq.npy', 5662310528, None))
    total = sum(job[2] for job in jobs)
    if total > args.max_gb * 1e9:
        parser.error(f'Bundle needs {total / 1e9:.2f} GB, above --max-gb={args.max_gb}')
    if shutil.disk_usage(args.root).free < total + 5 * 1024**3:
        parser.error('Not enough space for bundle plus 5 GiB reserve')
    print(f'Bundle={args.bundle} total_bytes={total}', flush=True)
    status = {'bundle': args.bundle, 'total_bytes': total, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'completed': [], 'failed': []}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(fetch, *job): job[1].name for job in jobs}
        for future in as_completed(futures):
            try:
                status['completed'].append(future.result())
            except Exception as exc:
                status['failed'].append({'file': futures[future], 'error': str(exc)})
                print(f'FAILED {futures[future]}: {exc}', flush=True)
            (args.root / 'download_status.json').write_text(json.dumps(status, indent=2))
    if status['failed']:
        raise SystemExit('Some sources failed; rerun to resume. See download_status.json.')
    print('All source downloads completed and RadioPile MD5 checks passed.', flush=True)


if __name__ == '__main__':
    main()
