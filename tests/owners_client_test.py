# Copyright (c) 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import os
import sys
import unittest

if sys.version_info.major == 2:
  import mock
else:
  from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gerrit_util
import owners_client

from testing_support import filesystem_mock


alice = 'alice@example.com'
bob = 'bob@example.com'
chris = 'chris@example.com'
dave = 'dave@example.com'
emily = 'emily@example.com'


class DepotToolsClientTest(unittest.TestCase):
  def setUp(self):
    self.repo = filesystem_mock.MockFileSystem(files={
        '/OWNERS': '\n'.join([
            'per-file approved.cc=approver@example.com',
            'per-file reviewed.h=reviewer@example.com',
            'missing@example.com',
        ]),
        '/approved.cc': '',
        '/reviewed.h': '',
        '/bar/insufficient_reviewers.py': '',
        '/bar/everyone/OWNERS': '*',
        '/bar/everyone/foo.txt': '',
    })
    self.root = '/'
    self.fopen = self.repo.open_for_reading
    self.addCleanup(mock.patch.stopall)
    self.client = owners_client.DepotToolsClient(
        '/', 'branch', self.fopen, self.repo)

  @mock.patch('scm.GIT.CaptureStatus')
  @mock.patch('scm.GIT.GetOldContents')
  def testListOwners(self, mockGetOldContents, mockCaptureStatus):
    mockGetOldContents.side_effect = lambda r, f, _b: self.repo.files[r + f]
    mockCaptureStatus.return_value = [
        ('M', 'bar/everyone/foo.txt'),
        ('M', 'OWNERS'),
    ]

    self.assertEqual(
        ['*', 'missing@example.com'],
        self.client.ListOwners('bar/everyone/foo.txt'))
    mockCaptureStatus.assert_called_once_with('/', 'branch')


class GerritClientTest(unittest.TestCase):
  def setUp(self):
    self.client = owners_client.GerritClient('host', 'project', 'branch')
    self.addCleanup(mock.patch.stopall)

  def testListOwners(self):
    mock.patch(
        'gerrit_util.GetOwnersForFile',
        return_value={
          "code_owners": [
            {
              "account": {
                "email": 'approver@example.com'
              }
            },
            {
              "account": {
                "email": 'reviewer@example.com'
              },
            },
            {
              "account": {
                "email": 'missing@example.com'
              },
            },
            {
              "account": {},
            }
          ]
        }).start()
    self.assertEquals(
        ['approver@example.com', 'reviewer@example.com', 'missing@example.com'],
        self.client.ListOwners(os.path.join('bar', 'everyone', 'foo.txt')))

    # Result should be cached.
    self.assertEquals(
        ['approver@example.com', 'reviewer@example.com', 'missing@example.com'],
        self.client.ListOwners(os.path.join('bar', 'everyone', 'foo.txt')))
    # Always use slashes as separators.
    gerrit_util.GetOwnersForFile.assert_called_once_with(
        'host', 'project', 'branch', 'bar/everyone/foo.txt',
        resolve_all_users=False)

  def testListOwnersOwnedByAll(self):
    mock.patch(
      'gerrit_util.GetOwnersForFile',
      side_effect=[
        {
          "code_owners": [
            {
              "account": {
                "email": 'foo@example.com'
              },
            },
          ],
          "owned_by_all_users": True,
        },
        {
          "code_owners": [
            {
              "account": {
                "email": 'bar@example.com'
              },
            },
          ],
          "owned_by_all_users": False,
        },
      ]
    ).start()
    self.assertEquals(
        ['foo@example.com', self.client.EVERYONE],
        self.client.ListOwners('foo.txt'))
    self.assertEquals(
        ['bar@example.com'],
        self.client.ListOwners('bar.txt'))


class TestClient(owners_client.OwnersClient):
  def __init__(self, owners_by_path):
    super(TestClient, self).__init__()
    self.owners_by_path = owners_by_path

  def ListOwners(self, path):
    return self.owners_by_path[path]


class OwnersClientTest(unittest.TestCase):
  def setUp(self):
    self.owners = {}
    self.client = TestClient(self.owners)

  def testGetFilesApprovalStatus(self):
    self.client.owners_by_path = {
      'approved': ['approver@example.com'],
      'pending': ['reviewer@example.com'],
      'insufficient': ['insufficient@example.com'],
      'everyone': [owners_client.OwnersClient.EVERYONE],
    }
    self.assertEqual(
        self.client.GetFilesApprovalStatus(
            ['approved', 'pending', 'insufficient'],
            ['approver@example.com'], ['reviewer@example.com']),
        {
          'approved': owners_client.OwnersClient.APPROVED,
          'pending': owners_client.OwnersClient.PENDING,
          'insufficient': owners_client.OwnersClient.INSUFFICIENT_REVIEWERS,
        })
    self.assertEqual(
        self.client.GetFilesApprovalStatus(
            ['everyone'], ['anyone@example.com'], []),
        {'everyone': owners_client.OwnersClient.APPROVED})
    self.assertEqual(
        self.client.GetFilesApprovalStatus(
            ['everyone'], [], ['anyone@example.com']),
        {'everyone': owners_client.OwnersClient.PENDING})
    self.assertEqual(
        self.client.GetFilesApprovalStatus(['everyone'], [], []),
        {'everyone': owners_client.OwnersClient.INSUFFICIENT_REVIEWERS})

  def test_owner_combinations(self):
    owners = [alice, bob, chris, dave, emily]
    self.assertEqual(
        list(owners_client._owner_combinations(owners, 2)),
        [(bob, alice),
         (chris, alice),
         (chris, bob),
         (dave, alice),
         (dave, bob),
         (dave, chris),
         (emily, alice),
         (emily, bob),
         (emily, chris),
         (emily, dave)])

  def testScoreOwners(self):
    self.client.owners_by_path = {
        'a': [alice, bob, chris]
    }
    self.assertEqual(
      self.client.ScoreOwners(self.client.owners_by_path.keys()),
      [alice, bob, chris]
    )

    self.client.owners_by_path = {
        'a': [alice, bob],
        'b': [bob],
        'c': [bob, chris]
    }
    self.assertEqual(
      self.client.ScoreOwners(self.client.owners_by_path.keys()),
      [bob, alice, chris]
    )

    self.client.owners_by_path = {
        'a': [alice, bob],
        'b': [bob],
        'c': [bob, chris]
    }
    self.assertEqual(
      self.client.ScoreOwners(
          self.client.owners_by_path.keys(), exclude=[chris]),
      [bob, alice],
    )

    self.client.owners_by_path = {
        'a': [alice, bob, chris, dave],
        'b': [chris, bob, dave],
        'c': [chris, dave],
        'd': [alice, chris, dave]
    }
    self.assertEqual(
      self.client.ScoreOwners(self.client.owners_by_path.keys()),
      [chris, dave, alice, bob]
    )

  def testSuggestOwners(self):
    self.client.owners_by_path = {'a': [alice]}
    self.assertEqual(
        self.client.SuggestOwners(['a']),
        [alice])

    self.client.owners_by_path = {'abcd': [alice, bob, chris, dave]}
    self.assertEqual(
        sorted(self.client.SuggestOwners(['abcd'])),
        [alice, bob])

    self.client.owners_by_path = {'abcd': [alice, bob, chris, dave]}
    self.assertEqual(
        sorted(self.client.SuggestOwners(['abcd'], exclude=[alice, bob])),
        [chris, dave])

    self.client.owners_by_path = {
        'ae': [alice, emily],
        'be': [bob, emily],
        'ce': [chris, emily],
        'de': [dave, emily],
    }
    suggested = self.client.SuggestOwners(['ae', 'be', 'ce', 'de'])
    # emily should be selected along with anyone else.
    self.assertIn(emily, suggested)
    self.assertEqual(2, len(suggested))

    self.client.owners_by_path = {
        'ad': [alice, dave],
        'cad': [chris, alice, dave],
        'ead': [emily, alice, dave],
        'bd': [bob, dave],
    }
    self.assertEqual(
        sorted(self.client.SuggestOwners(['ad', 'cad', 'ead', 'bd'])),
        [alice, dave])

    self.client.owners_by_path = {
        'a': [alice],
        'b': [bob],
        'c': [chris],
        'ad': [alice, dave],
    }
    self.assertEqual(
        sorted(self.client.SuggestOwners(['a', 'b', 'c', 'ad'])),
        [alice, bob, chris])

    self.client.owners_by_path = {
        'abc': [alice, bob, chris],
        'acb': [alice, chris, bob],
        'bac': [bob, alice, chris],
        'bca': [bob, chris, alice],
        'cab': [chris, alice, bob],
        'cba': [chris, bob, alice]
    }
    suggested = self.client.SuggestOwners(
        ['abc', 'acb', 'bac', 'bca', 'cab', 'cba'])
    # Any two owners.
    self.assertEqual(2, len(suggested))

  def testBatchListOwners(self):
    self.client.owners_by_path = {
        'bar/everyone/foo.txt': [alice, bob],
        'bar/everyone/bar.txt': [bob],
        'bar/foo/': [bob, chris]
    }

    self.assertEquals(
        {
            'bar/everyone/foo.txt': [alice, bob],
            'bar/everyone/bar.txt': [bob],
            'bar/foo/': [bob, chris]
        },
        self.client.BatchListOwners(
            ['bar/everyone/foo.txt', 'bar/everyone/bar.txt', 'bar/foo/']))


class GetCodeOwnersClientTest(unittest.TestCase):
  def setUp(self):
    mock.patch('gerrit_util.IsCodeOwnersEnabled').start()
    self.addCleanup(mock.patch.stopall)

  def testGetCodeOwnersClient_GerritClient(self):
    # TODO(crbug.com/1183447): Check that code-owners is used if available once
    # code-owners plugin issues have been fixed.
    pass

  def testGetCodeOwnersClient_DepotToolsClient(self):
    gerrit_util.IsCodeOwnersEnabled.return_value = False
    self.assertIsInstance(
        owners_client.GetCodeOwnersClient('root', 'branch', '', '', ''),
        owners_client.DepotToolsClient)


if __name__ == '__main__':
  unittest.main()
