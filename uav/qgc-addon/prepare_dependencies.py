"""Copy downloaded dependency sources to Linux storage and print CPM override arguments."""
import argparse
import json
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archives', type=Path)
    parser.add_argument('cache', type=Path)
    args = parser.parse_args()
    manifest = args.archives / 'resolved.json'
    if not manifest.exists():
        return
    for item in json.loads(manifest.read_text(encoding='utf-8-sig')):
        if not all(character.isalnum() or character in '_.-' for character in item['Name']):
            raise ValueError('Invalid dependency name')
        source = args.archives / item['Name']; target = args.cache / item['Name']
        marker = target / '.linghang-source-commit'
        if not marker.exists() or marker.read_text(encoding='utf-8') != item['Commit']:
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns('.git', 'build'))
            marker.write_text(item['Commit'], encoding='utf-8')
        print(f'-DCPM_{item["Name"]}_SOURCE={target}')


if __name__ == '__main__':
    main()
