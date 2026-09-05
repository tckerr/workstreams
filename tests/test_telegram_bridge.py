import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock


spec = importlib.util.spec_from_file_location('bridge', Path(__file__).parents[1] / 'scripts/telegram_bridge.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class FakeTelegram:
    def __init__(self):
        self.sent = []
        self.reactions = []

    def call(self, method, **payload):
        if method == 'setMessageReaction':
            self.reactions.append(payload)
            return True
        assert method == 'sendMessage'
        self.sent.append(payload)
        return {'message_id': 1000 + len(self.sent)}


class FakeHerdr:
    def __init__(self):
        self.state = 'idle'
        self.identity = 'original-process'
        self.prompts = []
        self.fail = False

    def snapshot(self, pane):
        return self.state, self.identity

    def prompt(self, pane, text):
        self.prompts.append((pane, text))
        if self.fail:
            raise bridge.BridgeError('Timed out after possible delivery')


def update(uid, text, user=10, chat=10, kind='private', reply=None):
    message = {'message_id': uid + 100, 'from': {'id': user},
               'chat': {'id': chat, 'type': kind}, 'text': text}
    if reply:
        message['reply_to_message'] = {'message_id': reply}
    return {'update_id': uid, 'message': message}


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = bridge.database(self.root)
        self.addCleanup(self.db.close)
        self.config = {'token': 'test-token', 'user_id': 10, 'chat_id': 10}
        self.telegram, self.herdr = FakeTelegram(), FakeHerdr()
        self.app = bridge.Bridge(self.root, self.config, self.db, self.telegram, self.herdr)
        self.target = bridge.register(self.db, self.herdr, 'orchestrator', 'w1:p1', True)
        with self.db:
            bridge.set_meta(self.db, 'brief:' + self.target, 'sent')

    def statuses(self):
        return [r[0] for r in self.db.execute('SELECT status FROM inbox ORDER BY id')]

    def test_only_paired_user_and_private_chat_can_issue_any_command(self):
        for i, text in enumerate(('/status', '/to orchestrator spawn something', 'spawn something', '/pair secret')):
            self.app.ingest([update(i * 3 + 1, text, user=99),
                             update(i * 3 + 2, text, chat=99),
                             update(i * 3 + 3, text, kind='group')])
        self.app.tick()
        self.app.flush()
        self.assertEqual([], self.herdr.prompts)
        self.assertEqual([], self.telegram.sent)
        self.assertEqual([], self.telegram.reactions)
        self.assertEqual([], self.statuses())

    def test_pairing_requires_secret_and_cannot_be_replaced(self):
        self.config.clear()
        self.config.update(token='test-token', pair_code='secret')
        self.app.ingest([update(1, '/pair wrong'), update(2, '/pair secret', kind='group')])
        self.assertNotIn('user_id', self.config)
        self.app.ingest([update(3, '/pair secret')])
        self.assertEqual(10, self.config['user_id'])
        self.assertNotIn('pair_code', self.config)
        self.assertEqual(0o600, (self.root / 'config.json').stat().st_mode & 0o777)
        self.app.ingest([update(4, '/pair secret', user=99, chat=99)])
        self.assertEqual(10, json.loads((self.root / 'config.json').read_text())['user_id'])

    def test_duplicate_updates_do_not_duplicate_agent_work(self):
        message = update(1, 'start a workstream')
        self.app.ingest([message, message])
        self.app.tick()
        self.app.ingest([message])
        self.app.tick()
        self.assertEqual(['delivered'], self.statuses())
        self.assertEqual(1, len(self.herdr.prompts))
        self.assertEqual('Telegram request 1:\n\nstart a workstream', self.herdr.prompts[0][1])
        self.app.flush()
        self.assertEqual([], self.telegram.sent)
        self.assertEqual([{'chat_id': 10, 'message_id': 101,
                           'reaction': [{'type': 'emoji', 'emoji': '👀'}]}], self.telegram.reactions)

    def test_busy_unknown_and_blocked_agents_keep_messages_queued(self):
        self.app.ingest([update(1, 'do the work')])
        for state in ('working', 'unknown', 'blocked'):
            self.herdr.state = state
            self.app.tick()
            self.assertEqual(['queued'], self.statuses())
        self.assertEqual([], self.herdr.prompts)
        self.herdr.state = 'done'
        self.app.tick()
        self.assertEqual(['delivered'], self.statuses())

    def test_reply_to_notification_routes_to_implementer(self):
        other = bridge.register(self.db, self.herdr, 'fix-ui', 'w2:p1')
        with self.db:
            bridge.set_meta(self.db, 'brief:' + other, 'sent')
            bridge.queue(self.db, 'Which layout?', other)
        self.app.flush()
        self.app.ingest([update(1, 'Use the compact layout', reply=1001)])
        self.app.tick()
        self.assertEqual('w2:p1', self.herdr.prompts[0][0])

    def test_reply_helper_sends_answer_to_original_phone_message(self):
        bridge.save_config(self.root, self.config)
        self.app.ingest([update(1, 'Which project?')])
        self.app.tick()
        answer = self.root / 'answer.md'
        answer.write_text('Working on rundown.\nReceived from Telegram.')
        bridge.main(['--state-dir', str(self.root), 'reply', '1', '--file', str(answer)])
        self.app.flush()
        sent = self.telegram.sent[-1]
        self.assertEqual(10, sent['chat_id'])
        self.assertEqual(101, sent['reply_parameters']['message_id'])
        self.assertEqual(answer.read_text(), sent['text'])
        self.app.ingest([update(2, 'Thanks', reply=1001, user=99)])
        self.assertEqual(1, len(self.statuses()))

    def test_unknown_reply_never_falls_back_to_orchestrator(self):
        self.app.ingest([update(1, 'yes', reply=9876)])
        self.app.tick()
        self.assertEqual([], self.herdr.prompts)

    def test_switch_default_requires_paired_account(self):
        other = bridge.register(self.db, self.herdr, 'codex-test', 'w2:p1')
        with self.db:
            bridge.set_meta(self.db, 'brief:' + other, 'sent')
        self.app.ingest([update(1, '/use codex-test', user=99)])
        self.assertEqual(self.target, bridge.get_meta(self.db, 'default_target'))
        self.app.ingest([update(2, '/use codex-test'), update(3, 'How many files?')])
        self.assertEqual(other, bridge.get_meta(self.db, 'default_target'))
        self.app.tick()
        self.assertEqual('w2:p1', self.herdr.prompts[0][0])

    def test_replacement_process_never_receives_queued_message(self):
        self.app.ingest([update(1, 'spawn a stream')])
        self.herdr.identity = 'replacement-process'
        self.app.tick()
        self.assertEqual([], self.herdr.prompts)
        self.assertEqual(['queued'], self.statuses())
        with self.assertRaises(bridge.BridgeError):
            bridge.register(self.db, self.herdr, 'orchestrator', 'w1:p1')

    def test_agent_helpers_do_not_change_process_identity(self):
        herdr = bridge.Herdr()
        agent = {'agent': {'terminal_id': 'term1', 'agent': 'claude', 'agent_status': 'idle'}}
        leader = {'process_info': {'foreground_process_group_id': 123,
                                  'foreground_processes': [{'pid': 123}]}}
        helpers = {'process_info': {'foreground_process_group_id': 123,
                                   'foreground_processes': [{'pid': 456}, {'pid': 123}]}}
        herdr.call = Mock(side_effect=[agent, leader, agent, helpers])
        self.assertEqual(herdr.snapshot('w1:p1'), herdr.snapshot('w1:p1'))

    def test_delivery_failure_is_not_automatically_replayed(self):
        self.app.ingest([update(1, 'spawn a stream')])
        self.herdr.fail = True
        self.app.tick()
        self.app.tick()
        self.assertEqual(['uncertain'], self.statuses())
        self.assertEqual(1, len(self.herdr.prompts))

    def test_restart_does_not_replay_ambiguous_delivery(self):
        self.app.ingest([update(1, 'spawn a stream')])
        with self.db:
            self.db.execute("UPDATE inbox SET status='delivering'")
        self.app.recover()
        self.app.tick()
        self.assertEqual(['uncertain'], self.statuses())
        self.assertEqual([], self.herdr.prompts)

    def test_brief_sent_once_before_messages_and_not_on_bridge_restart(self):
        with self.db:
            self.db.execute('DELETE FROM meta WHERE key=?', ('brief:' + self.target,))
        self.app.tick()
        self.assertIn('Telegram bridge setup', self.herdr.prompts[0][1])
        self.app.ingest([update(1, 'First message')])
        self.app.tick()
        restarted = bridge.Bridge(self.root, self.config, self.db, self.telegram, self.herdr)
        restarted.recover()
        restarted.ingest([update(2, 'Second message')])
        restarted.tick()
        self.assertEqual(3, len(self.herdr.prompts))
        self.assertEqual('Telegram request 1:\n\nFirst message', self.herdr.prompts[1][1])
        self.assertEqual('Telegram request 2:\n\nSecond message', self.herdr.prompts[2][1])

    def test_uncertain_brief_keeps_messages_queued_without_retry(self):
        with self.db:
            self.db.execute('DELETE FROM meta WHERE key=?', ('brief:' + self.target,))
        self.herdr.fail = True
        self.app.ingest([update(1, 'Spawn a stream')])
        self.app.tick()
        self.app.tick()
        self.assertEqual(['queued'], self.statuses())
        self.assertEqual(1, len(self.herdr.prompts))

    def test_interrupted_brief_is_not_replayed_on_restart(self):
        with self.db:
            bridge.set_meta(self.db, 'brief:' + self.target, 'sending')
        self.app.recover()
        self.app.tick()
        self.assertEqual('uncertain', bridge.get_meta(self.db, 'brief:' + self.target))
        self.assertEqual([], self.herdr.prompts)

    def test_blocked_alert_is_sent_once_per_transition(self):
        self.herdr.state = 'blocked'
        self.app.tick()
        self.app.tick()
        self.app.flush()
        self.assertEqual(1, len(self.telegram.sent))
        self.herdr.state = 'idle'
        self.app.tick()
        self.herdr.state = 'blocked'
        self.app.tick()
        self.app.flush()
        self.assertEqual(2, len(self.telegram.sent))

    def test_long_unicode_message_stays_under_telegram_limit(self):
        text = '😀' * 4100
        with self.db:
            bridge.queue(self.db, text, self.target)
        self.app.flush()
        self.assertEqual(text, ''.join(p['text'] for p in self.telegram.sent))
        for p in self.telegram.sent:
            self.assertLessEqual(len(p['text'].encode('utf-16-le')) // 2, 4096)


if __name__ == '__main__':
    unittest.main()
