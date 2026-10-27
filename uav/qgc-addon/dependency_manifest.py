"""Resolve QGC's pinned GitHub dependencies for an offline CMake source override."""
import argparse
import json
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    variables = dict(re.findall(r'set\((QGC_MAVLINK_GIT_\w+)\s+"([^"\n]+)"', (args.source / 'cmake/CustomOptions.cmake').read_text(encoding='utf-8')))
    packages = {}
    for directory in ['src', 'test']:
        for file in (args.source / directory).rglob('CMakeLists.txt'):
            if 'Android' in file.parts:
                continue
            for block in re.findall(r'CPMAddPackage\((.*?)\n\s*\)', file.read_text(encoding='utf-8'), re.S):
                def field(name):
                    found = re.search(r'^\s*' + name + r'\s+([^\s]+)', block, re.M)
                    return found[1].strip('"') if found else ''
                name, repo, ref = field('NAME'), field('GITHUB_REPOSITORY'), field('GIT_TAG')
                if name in ['lz4', 'BZip2', 'ArduPilotParams']:
                    continue
                if not repo and name == 'mavlink':
                    repo = variables['QGC_MAVLINK_GIT_REPO'].removeprefix('https://github.com/').removesuffix('.git')
                    ref = variables['QGC_MAVLINK_GIT_TAG']
                if name and re.fullmatch(r'[\w.-]+/[\w.-]+', repo) and re.fullmatch(r'[\w./+-]+', ref):
                    packages[name] = {'Name': name, 'Repository': 'https://github.com/' + repo + '.git', 'Ref': ref}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(list(packages.values()), indent=2), encoding='utf-8')
    print(f'Resolved {len(packages)} pinned GitHub dependencies')


if __name__ == '__main__':
    main()
