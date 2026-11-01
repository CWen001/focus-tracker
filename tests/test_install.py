import tempfile
import unittest
from pathlib import Path
from tools.install_host import install, uninstall, InstallError


class Registry:
    def __init__(self): self.value=None
    def get(self): return self.value
    def set(self,value): self.value=value
    def delete(self): self.value=None


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.directory=Path(self.temp.name)/'host'
        self.registry=Registry()

    def test_preview_has_no_mutation(self):
        plan=install(self.directory,'a'*32,self.registry,False)
        self.assertFalse(self.directory.exists())
        self.assertIsNone(self.registry.value)
        self.assertEqual(plan['allowed_origins'],['chrome-extension://'+'a'*32+'/'])

    def test_install_repeat_uninstall_preserves_unrelated_files(self):
        install(self.directory,'a'*32,self.registry,True)
        before={p.name:p.read_bytes() for p in self.directory.iterdir()}
        install(self.directory,'a'*32,self.registry,True)
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.directory.iterdir()})
        extra=self.directory/'keep.txt'; extra.write_text('preserve')
        uninstall(self.directory,self.registry,False)
        self.assertEqual(len(list(self.directory.iterdir())),4)
        uninstall(self.directory,self.registry,True)
        self.assertIsNone(self.registry.value)
        self.assertEqual(list(self.directory.iterdir()),[extra])

    def test_foreign_registry_or_changed_files_are_not_overwritten(self):
        self.registry.value='C:/someone-else/manifest.json'
        with self.assertRaises(InstallError): install(self.directory,'a'*32,self.registry,True)
        self.assertFalse(self.directory.exists())
        self.registry.value=None
        install(self.directory,'a'*32,self.registry,True)
        (self.directory/'host.cmd').write_text('user change')
        with self.assertRaises(InstallError): uninstall(self.directory,self.registry,True)
        self.assertIsNotNone(self.registry.value)

    def test_registry_failure_rolls_back_new_files(self):
        def fail(value): raise OSError('synthetic')
        self.registry.set=fail
        with self.assertRaises(InstallError): install(self.directory,'a'*32,self.registry,True)
        self.assertFalse(self.directory.exists())

    def test_windows_launcher_handles_unicode_source_path(self):
        import subprocess
        import sys
        from unittest.mock import patch
        root=Path(self.temp.name)/'中文 source'
        (root/'tools').mkdir(parents=True)
        (root/'tools/native_host.py').write_text("import sys; print('SYNTHETIC_ORIGIN', sys.argv[-1])",encoding='utf-8')
        with patch('tools.install_host.PROJECT_ROOT',root):
            install(self.directory,'a'*32,self.registry,True)
        command='chcp 437 >nul & call "'+str(self.directory/'host.cmd')+'" "chrome-extension://'+'a'*32+'/"'
        result=subprocess.run('cmd.exe /d /c '+command,capture_output=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertIn(b'SYNTHETIC_ORIGIN chrome-extension://',result.stdout)

    def test_invalid_id_and_shell_paths_rejected(self):
        with self.assertRaises(InstallError): install(self.directory,'bad',self.registry,False)
        with self.assertRaises(InstallError): install(self.directory/'bad%path','a'*32,self.registry,False)
