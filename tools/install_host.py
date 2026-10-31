"""Preview by default. Only --apply mutates HKCU or creates host files."""
import hashlib
import json
from pathlib import Path
import re
import sys
from activity_log.__main__ import HOST_NAME, PROJECT_ROOT, Parser, data_path_check

KEY = 'Software\\Google\\Chrome\\NativeMessagingHosts\\'+HOST_NAME


class InstallError(Exception):
    pass


class Registry:
    def get(self):
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as key:
                value, kind = winreg.QueryValueEx(key, '')
            if kind != winreg.REG_SZ:
                raise InstallError('FOREIGN_REGISTRATION')
            return value
        except FileNotFoundError:
            return None

    def set(self, value):
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, KEY) as key:
            winreg.SetValueEx(key, '', 0, winreg.REG_SZ, value)

    def delete(self):
        import winreg
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, KEY)


def _safe(directory):
    if data_path_check(directory).state != 'OK':
        raise InstallError('UNSAFE_HOST_PATH')
    for path in (directory, PROJECT_ROOT, Path(sys.executable)):
        if any(c in str(path) for c in '%!&|<>^\r\n"'):
            raise InstallError('UNSUPPORTED_SHELL_PATH')


def _files(directory, extension_id):
    if not re.fullmatch('[a-p]{32}', extension_id):
        raise InstallError('INVALID_EXTENSION_ID')
    manifest = {'name': HOST_NAME, 'description': 'Local distraction tracker bridge',
                'path': str(directory/'host.cmd'), 'type': 'stdio',
                'allowed_origins': ['chrome-extension://'+extension_id+'/']}
    # Launcher changes no cwd/PATH and passes only Chrome's origin, not arbitrary arguments.
    launcher = f'@echo off\r\nchcp 65001 >nul\r\n"{sys.executable}" -B "{PROJECT_ROOT / "tools/native_host.py"}" --extension-id "{extension_id}" --origin "%~1"\r\n'
    files = {'manifest.json': (json.dumps(manifest, indent=2)+'\n').encode('utf-8'),
             'host.cmd': launcher.encode('utf-8')}
    receipt = {'hashes': {name: hashlib.sha256(raw).hexdigest() for name, raw in files.items()},
               'manifest': str(directory/'manifest.json')}
    files['receipt.json'] = (json.dumps(receipt, indent=2)+'\n').encode('utf-8')
    return manifest, files


def _read_file(path):
    if path.is_symlink() or path.is_junction():
        raise InstallError('UNSAFE_HOST_FILE')
    return path.read_bytes()


def install(directory, extension_id, registry, apply=False):
    _safe(directory)
    directory = directory.resolve()
    manifest, files = _files(directory, extension_id)
    expected = str(directory/'manifest.json')
    registered = registry.get()
    if registered is not None and registered != expected:
        raise InstallError('FOREIGN_REGISTRATION')
    for name, content in files.items():
        path = directory/name
        if path.exists() and _read_file(path) != content:
            raise InstallError('HOST_FILE_CONFLICT')
    if not apply:
        return manifest
    created = []
    existed = directory.exists()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            path = directory/name
            if not path.exists():
                with path.open('xb') as output:
                    output.write(content)
                created.append(path)
        if registered is None:
            registry.set(expected)
        return manifest
    except OSError:
        # Do not delete files if a partially completed registration now references them.
        if registry.get() != expected:
            for path in reversed(created):
                path.unlink()
            if not existed and directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise InstallError('INSTALL_FAILED_REVIEW_STATE') from None


def uninstall(directory, registry, apply=False):
    _safe(directory)
    directory = directory.resolve()
    expected = str(directory/'manifest.json')
    registered = registry.get()
    if registered not in (None, expected):
        raise InstallError('FOREIGN_REGISTRATION')
    try:
        receipt = json.loads(_read_file(directory/'receipt.json'))
        if receipt.keys() != {'hashes', 'manifest'} or receipt['manifest'] != expected or receipt['hashes'].keys() != {'manifest.json', 'host.cmd'}:
            raise InstallError('INVALID_RECEIPT')
        for name, digest in receipt['hashes'].items():
            if hashlib.sha256(_read_file(directory/name)).hexdigest() != digest:
                raise InstallError('HOST_FILE_CONFLICT')
    except (OSError, ValueError, KeyError, TypeError):
        raise InstallError('INVALID_RECEIPT') from None
    if not apply:
        return {'remove': ['manifest.json', 'host.cmd', 'receipt.json'], 'database': 'preserved'}
    if registered == expected:
        registry.delete()
    for name in ('manifest.json', 'host.cmd', 'receipt.json'):
        (directory/name).unlink()
    if not any(directory.iterdir()):
        directory.rmdir()
    return {'removed': True, 'database': 'preserved'}


def main(argv=None):
    parser = Parser(description='Preview native-host install/removal. --apply requires prior permission.')
    parser.add_argument('action', choices=['install', 'uninstall'])
    parser.add_argument('--host-dir', type=Path, required=True)
    parser.add_argument('--extension-id')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    if args.action == 'install' and not args.extension_id:
        parser.error('extension-id required')
    try:
        registry = Registry()
        result = install(args.host_dir, args.extension_id, registry, args.apply) if args.action == 'install' else uninstall(args.host_dir, registry, args.apply)
        print(json.dumps({'applied': args.apply, 'registry': 'HKCU\\'+KEY, 'plan': result}, indent=2))
        return 0
    except (InstallError, OSError):
        print('ERROR HOST_SETUP_FAILED No overwrite authorized; inspect preview and existing files.', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
