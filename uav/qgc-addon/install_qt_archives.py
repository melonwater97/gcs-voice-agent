"""Install checksum-verified official Qt archives into an isolated Linux prefix."""
import argparse
import hashlib
import json
from pathlib import Path

import py7zr
from aqt.archives import TargetConfig
from aqt.updater import Updater


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archives', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--only', nargs='*')
    args = parser.parse_args()
    archives = args.archives.resolve(); output = args.output.resolve()
    prefix = output / '6.11.1/gcc_64'; prefix.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((archives / 'manifest.json').read_text(encoding='utf-8-sig'))
    for item in manifest:
        if args.only and item['Name'].split('-')[0] not in args.only:
            continue
        file = archives / item['Name']
        if file.parent != archives or file.suffix != '.7z':
            raise ValueError('Invalid archive path')
        with file.open('rb') as stream:
            checksum = hashlib.file_digest(stream, 'sha1').hexdigest() if hasattr(hashlib, 'file_digest') else hashlib.sha1(stream.read()).hexdigest()
        if checksum != item['Sha1']:
            raise ValueError('Archive checksum mismatch: ' + file.name)
        with py7zr.SevenZipFile(file) as archive:
            if any(Path(name).is_absolute() or '..' in Path(name).parts for name in archive.getnames()):
                raise ValueError('Archive contains an unsafe entry')
            archive.extractall(prefix / 'lib' if file.name.startswith('icu-') else prefix)
        print('Installed ' + file.name, flush=True)
    for directory in [prefix / 'bin', prefix / 'libexec']:
        for file in directory.glob('*'):
            if file.is_file():
                file.chmod(file.stat().st_mode | 0o111)
    Updater.update(TargetConfig('6.11.1', 'desktop', 'linux_gcc_64', 'linux'), output, None)
    print(prefix)


if __name__ == '__main__':
    main()
