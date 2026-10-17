import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import updater as u


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='updater-test-')
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def archive(self, files=None, version='5.0', checksum=None):
        files = files or {u.EXE_NAME: b'new-exe', 'config.example.json': b'{}', 'templates/test.png': b'image'}
        meta = {'version': version, 'executable': u.EXE_NAME, 'files': {name: checksum or hashlib.sha256(content).hexdigest() for name, content in files.items()}}
        target = self.root / 'test.zip'
        with zipfile.ZipFile(target, 'w') as z:
            for name, content in files.items():
                z.writestr(name, content)
            z.writestr('update.json', json.dumps(meta))
        return target

    def release(self):
        return {'tag_name': 'v5.0', 'assets': [{'name': u.ASSET_NAME, 'state': 'uploaded', 'size': 100, 'digest': 'sha256:' + 'a'*64,
                'browser_download_url': f'https://github.com/{u.REPOSITORY}/releases/download/v5.0/{u.ASSET_NAME}'}]}

    def test_version_numeric(self):
        self.assertGreater(u.version_tuple('4.10'), u.version_tuple('4.9'))
        self.assertEqual(u.version_tuple('v4.9.0'), u.version_tuple('4.9'))
        with self.assertRaises(ValueError): u.version_tuple('v5.0-beta')

    def test_release_valid(self):
        self.assertEqual(u.release_info(self.release(), '4.9')['version'], '5.0')
        self.assertIsNone(u.release_info(self.release(), '5.0'))

    def test_release_rejects_missing_digest_and_foreign_url(self):
        for field, value in [('digest', ''), ('browser_download_url', 'https://evil.test/a.zip'), ('size', u.MAX_DOWNLOAD+1)]:
            data = self.release(); data['assets'][0][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError): u.release_info(data, '4.9')

    def test_drafts_and_prereleases(self):
        for field in ('draft', 'prerelease'):
            data = self.release(); data[field] = True
            self.assertIsNone(u.release_info(data, '4.9'))

    def test_unsafe_paths_and_personal_files(self):
        for name in ('../x', '/x', 'C:/x', 'templates/../x', 'templates/a\\b', 'templates/NUL.png', 'templates/a:stream',
                     'templates/./x', 'templates/a.', 'templates//x', 'config.json', 'battle_history.json', 'local_learning.json', 'combat_capture/x', 'source/main.py'):
            with self.subTest(name=name): self.assertFalse(u.allowed_file(name))

    def test_stage_valid(self):
        dest = self.root/'payload'
        u.stage_archive(self.archive(), dest, '5.0')
        self.assertEqual((dest/u.EXE_NAME).read_bytes(), b'new-exe')

    def test_corrupt_file(self):
        with self.assertRaises(ValueError): u.stage_archive(self.archive(checksum='0'*64), self.root/'p', '5.0')

    def test_wrong_version(self):
        with self.assertRaises(ValueError): u.stage_archive(self.archive(), self.root/'p', '5.1')

    def test_forbidden_archive(self):
        with self.assertRaises(ValueError): u.stage_archive(self.archive({u.EXE_NAME:b'x', 'config.json':b'{}'}), self.root/'p', '5.0')

    def test_unlisted_and_duplicate(self):
        archive = self.archive()
        with zipfile.ZipFile(archive, 'a') as z: z.writestr('assets/extra.png', b'x')
        with self.assertRaises(ValueError): u.stage_archive(archive, self.root/'p', '5.0')
        archive = self.archive()
        with zipfile.ZipFile(archive, 'a') as z: z.writestr('TEMPLATES/test.png', b'x')
        with self.assertRaises(ValueError): u.stage_archive(archive, self.root/'p2', '5.0')

    def test_download_and_digest_failure(self):
        data = self.archive().read_bytes()
        info = {'url': 'https://example.test', 'size': len(data), 'sha256':hashlib.sha256(data).hexdigest(), 'version':'5.0'}
        with patch.object(u, 'request', return_value=io.BytesIO(data)):
            staged = u.prepare_update(info)
        self.assertTrue((staged/'payload'/u.EXE_NAME).exists())
        self.assertTrue((staged/'install.ps1').read_bytes().startswith(b'\xef\xbb\xbf'))
        import shutil
        shutil.rmtree(staged)
        info['sha256'] = '0'*64
        with patch.object(u, 'request', return_value=io.BytesIO(data)), self.assertRaises(ValueError): u.prepare_update(info)

    def run_installer(self, stage, target):
        helper = stage/'install.ps1'
        helper.write_text((Path(__file__).parent/'updater_install.ps1').read_text(encoding='utf-8'), encoding='utf-8-sig')
        return subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(helper),
                               '-Stage', str(stage), '-Target', str(target), '-ProcessId', '2147483646', '-NoRestart'], capture_output=True, text=True)

    def test_real_windows_install_preserves_data(self):
        stage = self.root/'stage'; stage.mkdir()
        u.stage_archive(self.archive(), stage/'payload', '5.0')
        target = self.root/'app'; target.mkdir()
        for name in (u.EXE_NAME, 'config.json', 'battle_history.json', 'local_learning.json'):
            (target/name).write_bytes(b'original')
        result = self.run_installer(stage, target)
        report = (target/'update_error.txt').read_text(encoding='utf-8-sig') if (target/'update_error.txt').exists() else ''
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr + report)
        self.assertEqual((target/u.EXE_NAME).read_bytes(), b'new-exe')
        for name in ('config.json', 'battle_history.json', 'local_learning.json'):
            self.assertEqual((target/name).read_bytes(), b'original')
        self.assertEqual((stage/'backup'/u.EXE_NAME).read_bytes(), b'original')

    def test_installer_tamper_does_not_replace(self):
        stage=self.root/'stage'; stage.mkdir()
        u.stage_archive(self.archive(), stage/'payload', '5.0')
        (stage/'payload'/u.EXE_NAME).write_bytes(b'tampered')
        target=self.root/'app'; target.mkdir(); (target/u.EXE_NAME).write_bytes(b'original')
        result=self.run_installer(stage,target)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((target/u.EXE_NAME).read_bytes(),b'original')

    def test_locked_resource_rolls_back_executable(self):
        import ctypes
        from ctypes import wintypes
        stage=self.root/'stage'; stage.mkdir()
        u.stage_archive(self.archive(), stage/'payload', '5.0')
        target=self.root/'app'; (target/'templates').mkdir(parents=True)
        (target/u.EXE_NAME).write_bytes(b'old-exe')
        locked=target/'templates/test.png'; locked.write_bytes(b'old-image')
        create=ctypes.windll.kernel32.CreateFileW
        create.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        create.restype=wintypes.HANDLE
        handle=create(str(locked),0x80000000,1,None,3,0,None)
        self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
        try:
            result=self.run_installer(stage,target)
            self.assertNotEqual(result.returncode,0)
            self.assertEqual((target/u.EXE_NAME).read_bytes(),b'old-exe')
            self.assertEqual(locked.read_bytes(),b'old-image')
            self.assertFalse((target/'config.example.json').exists())
        finally:
            close=ctypes.windll.kernel32.CloseHandle
            close.argtypes=[wintypes.HANDLE]
            close(handle)


if __name__ == '__main__': unittest.main(verbosity=2)
