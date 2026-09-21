import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.identity_store import AuthenticationError, IdentityError, accept_invitation, authenticate, create_invitation, create_session, create_workspace, list_workspaces, register_user, revoke_membership, rotate_session
from app.storage import reset


class IdentityStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'APP_DB': str(Path(self.tmp.name) / 'app.db')})
        self.env.start(); reset()
        self.owner = register_user('owner@example.com', 'correct horse battery', 'Owner')
        self.member = register_user('member@example.com', 'correct horse battery', 'Member')
        create_workspace(self.owner['id'], 'acme-main', 'Acme')

    def tearDown(self):
        reset(); self.env.stop(); self.tmp.cleanup()

    def test_password_is_hashed_and_login_is_case_insensitive(self):
        self.assertNotEqual('correct horse battery', authenticate('OWNER@example.com', 'correct horse battery').get('password'))
        with self.assertRaises(AuthenticationError): authenticate('owner@example.com', 'wrong password')

    def test_invite_accept_and_membership_listing(self):
        invitation, raw = create_invitation(self.owner['id'], 'acme-main', 'member@example.com', 'operator')
        self.assertEqual('operator', invitation['role'])
        accepted = accept_invitation(self.member['id'], raw)
        self.assertEqual('operator', accepted['role'])
        self.assertEqual('acme-main', list_workspaces(self.member['id'])[0]['id'])
        with self.assertRaises(AuthenticationError): accept_invitation(self.member['id'], raw)

    def test_invite_cannot_be_used_by_other_email(self):
        other = register_user('other@example.com', 'correct horse battery', 'Other')
        _, raw = create_invitation(self.owner['id'], 'acme-main', 'member@example.com', 'viewer')
        with self.assertRaises(AuthenticationError): accept_invitation(other['id'], raw)

    def test_refresh_rotation_and_revocation(self):
        raw, _ = create_session(self.owner['id'], 'acme-main')
        rotated, claims = rotate_session(raw)
        self.assertEqual('owner', claims['role'])
        with self.assertRaises(AuthenticationError): rotate_session(raw)
        with self.assertRaises(AuthenticationError): rotate_session(rotated)

    def test_last_owner_cannot_be_revoked(self):
        with self.assertRaises(IdentityError): revoke_membership(self.owner['id'], 'acme-main', self.owner['id'])

    def test_revoked_member_cannot_refresh(self):
        _, raw = create_invitation(self.owner['id'], 'acme-main', 'member@example.com', 'viewer')
        accept_invitation(self.member['id'], raw)
        refresh, _ = create_session(self.member['id'], 'acme-main')
        revoke_membership(self.owner['id'], 'acme-main', self.member['id'])
        with self.assertRaises(AuthenticationError): rotate_session(refresh)


if __name__ == '__main__': unittest.main()
