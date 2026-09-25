"""Linux execution of shared POSIX helper; Darwin fcntl is a mocked contract."""
import importlib.util
import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
import tempfile
from types import SimpleNamespace
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


class DeclaredBoundTests(unittest.TestCase):
    """The helper's declared numbers, pinned where this platform can measure them.

    The file-behaviour half of this module is Windows-blocked (see
    runtime_tests/test_platform_baseline.py: O_NOFOLLOW, mkfifo, symlink
    privilege). The validation half is not: these bounds decide what the helper
    will even attempt, they never open a descriptor, and they run everywhere.
    """

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();self.safe=self.root/'safe';self.safe.mkdir()
        self.config={'folders':[str(self.safe)],'deny_always':['private']}

    def test_the_declared_bounds(self):
        self.assertEqual(16000,helper.MAX_BYTES)
        self.assertEqual(200,helper.MAX_ENTRIES)
        self.assertEqual(1000,helper.MAX_VISITED)

    def test_the_literals_are_named_not_inline(self):
        text=path.read_text(encoding='utf-8')
        for literal in ('MAX_BYTES = 16000','MAX_ENTRIES = 200','MAX_VISITED = 1000'):
            self.assertIn(literal,text)

    def test_the_deny_list_speaks_both_languages(self):
        self.assertEqual(('.env','.ssh','.aws','credentials','password','parol','cvv'),helper.DENY)

    def test_the_root_count_is_one_to_thirty_two(self):
        def attempt(count):return helper.roots_and_deny({'folders':[str(self.safe)]*count})
        with self.assertRaises(ValueError):attempt(0)
        self.assertEqual(32,len(attempt(32)[0]))
        with self.assertRaises(ValueError):attempt(33)

    def test_a_root_must_be_an_existing_canonical_absolute_directory(self):
        for folder in ('relative/path',str(self.root/'missing')):
            with self.subTest(folder=folder):
                with self.assertRaises(ValueError):helper.roots_and_deny({'folders':[folder]})

    def test_the_deny_list_is_bounded_at_a_hundred_entries(self):
        roots,deny=helper.roots_and_deny({'folders':[str(self.safe)],'deny_always':['x']*100})
        self.assertEqual(100,len(deny)-len(helper.DENY))
        with self.assertRaises(ValueError):
            helper.roots_and_deny({'folders':[str(self.safe)],'deny_always':['x']*101})

    def test_a_deny_entry_is_bounded_at_128_characters(self):
        helper.roots_and_deny({'folders':[str(self.safe)],'deny_always':['x'*128]})
        with self.assertRaises(ValueError):
            helper.roots_and_deny({'folders':[str(self.safe)],'deny_always':['x'*129]})

    def test_an_unknown_configuration_key_is_refused(self):
        with self.assertRaises(ValueError):
            helper.roots_and_deny({'folders':[str(self.safe)],'shell':'x'})

    def test_a_path_is_bounded_before_any_lookup(self):
        roots,deny=helper.roots_and_deny(self.config)
        for bad in ('relative.txt','/tmp/x'+chr(0)+'y'):
            with self.subTest(value=bad[:20]):
                with self.assertRaises(ValueError):helper.permitted(bad,roots,deny)
        # A path inside an allowed root, long enough to cross the ceiling: if the
        # length check is widened the refusal changes TYPE (the commonpath check
        # answers instead), so the boundary is the one being measured.
        long_path=str(self.safe)+os.sep+'a'*2000
        self.assertGreater(len(long_path),2000)
        with self.assertRaises(ValueError):helper.permitted(long_path,roots,deny)

    def test_a_sensitive_word_is_denied_inside_an_allowed_root(self):
        roots,deny=helper.roots_and_deny(self.config)
        with self.assertRaises(PermissionError):
            helper.permitted(str(self.safe/'my-passwords.txt'),roots,deny)

    def test_only_the_two_read_tools_exist(self):
        for tool in ('fs.write_text','fs.delete','shell.exec'):
            with self.subTest(tool=tool):
                with self.assertRaises(PermissionError):helper.execute(tool,{},self.config)

    def test_the_stdin_request_is_bounded_at_64000_bytes(self):
        self.assertIn('if len(data) > 64000:',path.read_text(encoding='utf-8'))
        def run(payload):
            out=io.StringIO()
            with patch.object(helper.sys,'stdin',SimpleNamespace(buffer=io.BytesIO(payload))),\
                    redirect_stdout(out):
                code=helper.main()
            return code,out.getvalue()
        code,output=run(b'{}')
        self.assertEqual(1,code);self.assertNotIn('Traceback',output)
        code,output=run(b'x'*64001)
        self.assertEqual(1,code);self.assertIn('local_policy_or_execution_error',output)
        # The refusal at the ceiling is the bound, not the parser: 64000 bytes of
        # junk passes the size check and dies in json.loads instead.
        code,_=run(b'x'*64000)
        self.assertEqual(1,code)
        if os.name=='posix':
            # The success shape needs fs.list, which is Windows-blocked (O_NOFOLLOW).
            request=json.dumps({'tool':'fs.list','args':{'dir':str(self.safe)},
                                'config':{'folders':[str(self.safe)]}}).encode()
            code,output=run(request)
            self.assertEqual(0,code);self.assertIn('"ok": true',output)
