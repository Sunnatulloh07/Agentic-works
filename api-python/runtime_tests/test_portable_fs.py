"""Linux execution of shared POSIX helper; Darwin fcntl is a mocked contract."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

path=Path(__file__).resolve().parents[2]/'apps/runner/portable_fs.py'
spec=importlib.util.spec_from_file_location('portable_fs',path)
helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)


class PortableFSTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.safe=self.root/'safe';self.safe.mkdir()
        self.file=self.safe/'hello.txt';self.file.write_text('Salom, o‘zbekcha')
        self.config={'folders':[str(self.safe)],'deny_always':['private']}
    def read(self,p=None):return helper.execute('fs.read_text',{'file':str(p or self.file)},self.config)
    def test_read_real_file(self):self.assertEqual('Salom, o‘zbekcha',self.read()['text'])
    def test_list_real_directory(self):
        self.assertEqual([{'name':'hello.txt','type':'file'}],helper.execute('fs.list',{'dir':str(self.safe)},self.config)['entries'])
    def test_outside_denied(self):
        outside=self.root/'other';outside.write_text('secret')
        with self.assertRaises(PermissionError):self.read(outside)
    def test_escape_symlink_denied(self):
        outside=self.root/'other';outside.write_text('secret');link=self.safe/'link';link.symlink_to(outside)
        with self.assertRaises(PermissionError):self.read(link)
    def test_sensitive_file_denied(self):
        f=self.safe/'.env';f.write_text('private')
        with self.assertRaises(PermissionError):self.read(f)
    def test_hardlink_denied(self):
        f=self.safe/'link';os.link(self.file,f)
        with self.assertRaises(PermissionError):self.read(f)
    def test_fifo_does_not_block(self):
        f=self.safe/'pipe';os.mkfifo(f)
        with self.assertRaises(PermissionError):self.read(f)
    def test_binary_and_large_files_denied(self):
        for data in [b'x'*16001,b'\x00x',b'\xc3(']:
            self.file.write_bytes(data)
            with self.assertRaises((ValueError,PermissionError)):self.read()
    def test_invalid_args_denied(self):
        with self.assertRaises(ValueError):helper.execute('fs.read_text',{'file':str(self.file),'shell':'x'},self.config)
    def test_whole_root_denied(self):
        with self.assertRaises(ValueError):helper.roots_and_deny({'folders':['/']})
    def test_root_symlink_replacement_denied(self):
        other=self.root/'other';other.mkdir();self.file.unlink();self.safe.rmdir();self.safe.symlink_to(other)
        with self.assertRaises(ValueError):self.read()
    def test_descriptor_identity_rechecked(self):
        with patch.object(helper,'fd_path',return_value=str(self.root/'missing')):
            with self.assertRaises((PermissionError,OSError)):self.read()
    def test_unrecognized_os_denied(self):
        with patch.object(helper.sys,'platform','win32'):
            with self.assertRaises(ValueError):helper.fd_path(3)
    def test_darwin_getpath_contract(self):
        import fcntl
        with patch.object(helper.sys,'platform','darwin'),patch.object(fcntl,'fcntl',return_value=b'/safe/file\x00'+b'x'*20) as call:
            self.assertEqual('/safe/file',helper.fd_path(3));call.assert_called_once_with(3,50,bytes(1024))
    def test_unsafe_entries_hidden(self):
        (self.safe/'password.txt').write_text('x');os.mkfifo(self.safe/'pipe')
        (self.safe/'symlink').symlink_to(self.file)
        names=[e['name'] for e in helper.execute('fs.list',{'dir':str(self.safe)},self.config)['entries']]
        self.assertEqual(['hello.txt'],names)
    def test_directory_bounded(self):
        for n in range(205):(self.safe/str(n)).write_text('x')
        out=helper.execute('fs.list',{'dir':str(self.safe)},self.config)
        self.assertEqual(200,len(out['entries']));self.assertTrue(out['truncated'])
