"""Build a clean full ZIP and the fixed-name release asset for one-click updates."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile

from updater import ASSET_NAME, EXE_NAME, allowed_file, stage_archive, version_tuple


def package(root: Path, output: Path, version: str):
    version_tuple(version)
    source = (root / 'source/main.py').read_text(encoding='utf-8-sig')
    if not re.search(r'APP_VERSION = "' + re.escape(version) + r'"', source):
        raise ValueError('源码版本与发布版本不一致')
    required = [EXE_NAME, 'config.example.json']
    for name in required:
        if not (root / name).is_file():
            raise ValueError(f'缺少 {name}，请先打包程序')
    files = {name: root / name for name in required}
    if (root / '使用说明.md').exists():
        files['使用说明.md'] = root / '使用说明.md'
    for folder in ('templates', 'assets'):
        for path in sorted((root / folder).rglob('*')):
            if path.is_file():
                name = path.relative_to(root).as_posix()
                if not allowed_file(name):
                    raise ValueError(f'不允许的资源路径：{name}')
                files[name] = path
    if not any(name.startswith('templates/') for name in files):
        raise ValueError('缺少识别模板')
    manifest = {'version': version, 'executable': EXE_NAME,
                'files': {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in files.items()}}
    output.mkdir(parents=True, exist_ok=True)
    update = output / ASSET_NAME
    with zipfile.ZipFile(update, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, path in files.items():
            bundle.write(path, name)
        bundle.writestr('update.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    full = output / f'MengSanGuoBot-{version}.zip'
    with zipfile.ZipFile(full, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for name, path in files.items():
            bundle.write(path, name)
        bundle.write(root / 'config.example.json', 'config.json')
        for path in (root / 'source').glob('*'):
            if path.is_file() and path.suffix in ('.py', '.ps1', '.txt'):
                bundle.write(path, 'source/' + path.name)
    for path in (update, full):
        with zipfile.ZipFile(path) as bundle:
            if bundle.testzip() is not None:
                raise ValueError('压缩包校验失败')
    (output / 'SHA256.txt').write_text('\n'.join(f'{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}' for path in (update, full)) + '\n', encoding='utf-8')
    print(f'Full: {full}\nUpdate: {update}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--version', required=True)
    args = parser.parse_args()
    package(args.root, args.output, args.version)
