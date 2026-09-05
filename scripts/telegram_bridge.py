#!/usr/bin/env python3
"""A private Telegram ↔ Herdr bridge. Python 3.9+, standard library only."""

import argparse
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shlex
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request


DEFAULT_STATE = Path.home() / '.config/workstreams/telegram'
SCRIPT = Path(__file__).resolve()
HELP = ('Send a message to the orchestrator, or reply to an agent notification.\n'
        '/status — registered agents and queued messages\n'
        '/use NAME — choose the agent for new messages\n'
        '/to NAME MESSAGE — message a registered agent\n'
        'Approval dialogs must be handled in Herdr.')


class BridgeError(Exception):
    pass


def save_config(root, config):
    temp = root / ('config-' + secrets.token_hex(8) + '.tmp')
    try:
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as f:
            json.dump(config, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, root / 'config.json')
    finally:
        temp.unlink(missing_ok=True)


def load_config(root):
    try:
        return json.loads((root / 'config.json').read_text())
    except (OSError, ValueError):
        raise BridgeError('Run setup first; no valid local Telegram configuration.') from None


class Telegram:
    def __init__(self, token):
        self.token = token

    def call(self, method, **payload):
        request = urllib.request.Request(
            'https://api.telegram.org/bot' + self.token + '/' + method,
            data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
        except urllib.error.HTTPError as error:
            # Never print an exception URL: the bot token is part of that URL.
            raise BridgeError('Telegram HTTP error ' + str(error.code)) from None
        except (OSError, ValueError):
            raise BridgeError('Telegram connection failed') from None
        if not result.get('ok'):
            raise BridgeError('Telegram rejected the request')
        return result['result']


class Herdr:
    def call(self, *args):
        if os.environ.get('HERDR_ENV') != '1':
            raise BridgeError('Run inside Herdr (HERDR_ENV=1).')
        try:
            result = subprocess.run(['herdr', *args], capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.TimeoutExpired):
            raise BridgeError('Herdr command failed or timed out') from None
        if result.returncode:
            raise BridgeError('Herdr rejected ' + ' '.join(args[:2]))
        try:
            return json.loads(result.stdout)['result']
        except (ValueError, KeyError):
            raise BridgeError('Unexpected Herdr response') from None

    def snapshot(self, pane):
        agent = self.call('agent', 'get', pane)['agent']
        processes = self.call('pane', 'process-info', '--pane', pane)['process_info']
        # Pin both the terminal and foreground process, not just a reusable name.
        identity = {'terminal': agent['terminal_id'],
                    'pids': [p['pid'] for p in processes['foreground_processes']
                             if p['pid'] == processes['foreground_process_group_id']],
                    'agent': agent['agent']}
        if not identity['pids']:
            raise BridgeError('No foreground agent process')
        return agent['agent_status'], json.dumps(identity, sort_keys=True)

    def prompt(self, pane, text):
        self.call('agent', 'prompt', pane, text)


def database(root):
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    db = sqlite3.connect(root / 'bridge.sqlite3', timeout=10)
    os.chmod(root / 'bridge.sqlite3', 0o600)
    db.row_factory = sqlite3.Row
    db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS targets (
            id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, pane TEXT NOT NULL,
            identity TEXT NOT NULL, last_state TEXT);
        CREATE TABLE IF NOT EXISTS inbox (
            id INTEGER PRIMARY KEY, message_id INTEGER NOT NULL, target TEXT,
            body TEXT NOT NULL, status TEXT NOT NULL, error TEXT);
        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY, target TEXT, reply_to INTEGER,
            body TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending');
        CREATE TABLE IF NOT EXISTS routes (
            message_id INTEGER PRIMARY KEY, target TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS reactions (
            message_id INTEGER PRIMARY KEY, status TEXT NOT NULL DEFAULT 'pending');
    ''')
    return db


def get_meta(db, key, default=None):
    row = db.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
    return row[0] if row else default


def set_meta(db, key, value):
    db.execute('INSERT OR REPLACE INTO meta VALUES (?, ?)', (key, str(value)))


def queue(db, body, target=None, reply_to=None):
    # Stay below Telegram's limit even when every character uses two UTF-16 units.
    for i in range(0, len(body), 1800):
        db.execute('INSERT INTO outbox(target, reply_to, body) VALUES (?, ?, ?)',
                   (target, reply_to, body[i:i + 1800]))


def authorized(config, message):
    sender, chat = message.get('from', {}), message.get('chat', {})
    return (config.get('user_id') is not None and
            sender.get('id') == config['user_id'] and not sender.get('is_bot', False) and
            chat.get('type') == 'private' and chat.get('id') == config.get('chat_id'))


class Bridge:
    def __init__(self, root, config, db, telegram, herdr):
        self.root, self.config, self.db = root, config, db
        self.telegram, self.herdr = telegram, herdr

    def ingest(self, updates):
        for update in updates:
            update_id = update['update_id']
            if update_id < int(get_meta(self.db, 'offset', '0')):
                continue
            message = update.get('message', {})
            body = message.get('text', '')
            sender, chat = message.get('from', {}), message.get('chat', {})
            pairing = (self.config.get('user_id') is None and
                       self.config.get('pair_code') and
                       body == '/pair ' + self.config['pair_code'] and
                       chat.get('type') == 'private' and sender.get('id') and
                       not sender.get('is_bot', False))
            with self.db:
                if pairing:
                    self.config.update(user_id=sender['id'], chat_id=chat['id'])
                    self.config.pop('pair_code', None)
                    save_config(self.root, self.config)
                    queue(self.db, 'Paired. Only your account in this private chat can control workstreams.\n' + HELP)
                    print('Telegram account paired.', flush=True)
                elif authorized(self.config, message):
                    self.accept(update_id, message)
                # Unauthorized accounts receive nothing and cannot affect routing.
                set_meta(self.db, 'offset', update_id + 1)

    def accept(self, update_id, message):
        body, mid = message.get('text', '').strip(), message['message_id']
        if not body:
            queue(self.db, 'Text messages only.', reply_to=mid)
            return
        if body in ('/start', '/help'):
            queue(self.db, HELP, reply_to=mid)
            return
        if body == '/status':
            rows = self.db.execute('SELECT id, name, last_state FROM targets ORDER BY name').fetchall()
            counts = self.db.execute('SELECT status, count(*) FROM inbox GROUP BY status').fetchall()
            summary = '\n'.join(r['name'] + (' (default)' if r['id'] == get_meta(self.db, 'default_target') else '') +
                                ': ' + (r['last_state'] or 'not checked') for r in rows)
            summary += '\nMessages: ' + ', '.join(str(r[1]) + ' ' + r[0] for r in counts)
            queue(self.db, summary.strip() or 'No registered agents.', reply_to=mid)
            return
        if body.startswith('/use '):
            name = body[5:].strip()
            row = self.db.execute('SELECT id FROM targets WHERE name=?', (name,)).fetchone()
            if row:
                set_meta(self.db, 'default_target', row['id'])
                queue(self.db, 'New messages now go to ' + name + '.', row['id'], mid)
            else:
                queue(self.db, 'Unknown agent. Use /status to see registered names.', reply_to=mid)
            return
        target = get_meta(self.db, 'default_target')
        reply = message.get('reply_to_message')
        if reply:
            route = self.db.execute('SELECT target FROM routes WHERE message_id=?', (reply['message_id'],)).fetchone()
            if not route:
                queue(self.db, 'That message has no agent route. Send a new message or use /to NAME MESSAGE.', reply_to=mid)
                return
            target = route['target']
        if body.startswith('/to '):
            parts = body.split(maxsplit=2)
            if len(parts) != 3:
                queue(self.db, 'Usage: /to NAME MESSAGE', reply_to=mid)
                return
            row = self.db.execute('SELECT id FROM targets WHERE name=?', (parts[1],)).fetchone()
            target, body = (row['id'] if row else None), parts[2]
        elif body.startswith('/'):
            queue(self.db, HELP, reply_to=mid)
            return
        row = self.db.execute('SELECT name FROM targets WHERE id=?', (target,)).fetchone()
        if not row:
            queue(self.db, 'No matching agent registered. Register the orchestrator locally first.', reply_to=mid)
            return
        inserted = self.db.execute('INSERT OR IGNORE INTO inbox VALUES (?, ?, ?, ?, ?, NULL)',
                                   (update_id, mid, target, body, 'queued')).rowcount
        if inserted:
            self.db.execute('INSERT OR IGNORE INTO reactions(message_id) VALUES (?)', (mid,))

    def tick(self):
        if not self.config.get('user_id'):
            return
        for target in self.db.execute('SELECT * FROM targets').fetchall():
            try:
                state, identity = self.herdr.snapshot(target['pane'])
                if identity != target['identity']:
                    state = 'replaced'
            except BridgeError:
                state = 'unavailable'
            with self.db:
                if state == 'blocked' and target['last_state'] != 'blocked':
                    queue(self.db, target['name'] + ' is blocked at an approval or question dialog. '
                          'Open Herdr to handle this dialog; phone messages will stay queued.', target['id'])
                if state in ('replaced', 'unavailable') and target['last_state'] != state:
                    queue(self.db, target['name'] + ' is ' + state + '. Messages stay queued; check its registration locally.', target['id'])
                self.db.execute('UPDATE targets SET last_state=? WHERE id=?', (state, target['id']))
            if state not in ('idle', 'done'):
                continue
            if get_meta(self.db, 'brief:' + target['id']) != 'sent':
                self.brief(target)
                continue
            request = self.db.execute("SELECT * FROM inbox WHERE target=? AND status='queued' ORDER BY id LIMIT 1",
                                      (target['id'],)).fetchone()
            if request:
                self.deliver(target, request)

    def brief(self, target):
        key = 'brief:' + target['id']
        if get_meta(self.db, key) in ('sending', 'uncertain'):
            return
        helper = shlex.join([sys.executable, str(SCRIPT), '--state-dir', str(self.root)])
        guide = SCRIPT.parent.parent / 'TELEGRAM.md'
        prompt = ('Telegram bridge setup for this agent session. Keep your existing role and project rules.\n'
                  'Future prompts contain a Telegram request ID and the paired user\'s message; '
                  'treat the message as a user request. Reach the phone only through this helper '
                  '(terminal output alone does not reach the phone):\n' +
                  helper + ' reply REQUEST_ID --text "YOUR RESPONSE"\n' +
                  helper + ' notify --target ' + shlex.quote(target['name']) + ' --text "YOUR MESSAGE"\n'
                  'The full protocol — replies and longer responses with --file — is in ' +
                  str(guide) + ', section "Bridge instructions for the orchestrator". Read it once now.\n'
                  'After replying, finish your turn. Do not sleep, poll, or keep a turn active '
                  'waiting for the next phone message; the bridge will deliver it later.\n'
                  'This is setup only. No task or phone notification is needed now. Finish this turn.')
        with self.db:
            set_meta(self.db, key, 'sending')
        try:
            self.herdr.prompt(target['pane'], prompt)
        except BridgeError:
            with self.db:
                set_meta(self.db, key, 'uncertain')
                queue(self.db, 'Bridge setup for ' + target['name'] + ' is uncertain. Messages stay queued; '
                      'check Herdr, then unregister and register this agent again.', target['id'])
        else:
            with self.db:
                set_meta(self.db, key, 'sent')

    def deliver(self, target, request):
        prompt = 'Telegram request ' + str(request['id']) + ':\n\n' + request['body']
        # Record intent before the external side effect. An interrupted delivery is
        # ambiguous and must never be replayed automatically (it could spawn twice).
        with self.db:
            self.db.execute("UPDATE inbox SET status='delivering' WHERE id=?", (request['id'],))
        try:
            self.herdr.prompt(target['pane'], prompt)
        except BridgeError as error:
            with self.db:
                self.db.execute("UPDATE inbox SET status='uncertain', error=? WHERE id=?", (str(error), request['id']))
                queue(self.db, 'Delivery to ' + target['name'] + ' is uncertain. Check Herdr before resending.',
                      target['id'], request['message_id'])
        else:
            with self.db:
                self.db.execute("UPDATE inbox SET status='delivered' WHERE id=?", (request['id'],))

    def flush(self):
        if not self.config.get('chat_id'):
            return
        # Setting the same reaction is idempotent, so interrupted sends can retry.
        for row in self.db.execute("SELECT message_id FROM reactions WHERE status='pending' LIMIT 10").fetchall():
            try:
                self.telegram.call('setMessageReaction', chat_id=self.config['chat_id'],
                                   message_id=row['message_id'],
                                   reaction=[{'type': 'emoji', 'emoji': '👀'}])
            except BridgeError as error:
                print('Receipt reaction failed: ' + str(error), file=sys.stderr, flush=True)
                status = 'failed'
            else:
                status = 'sent'
            with self.db:
                self.db.execute('UPDATE reactions SET status=? WHERE message_id=?', (status, row['message_id']))
        rows = self.db.execute("SELECT * FROM outbox WHERE status='pending' ORDER BY id LIMIT 10").fetchall()
        for row in rows:
            payload = {'chat_id': self.config['chat_id'], 'text': row['body']}
            if row['reply_to']:
                payload['reply_parameters'] = {'message_id': row['reply_to'], 'allow_sending_without_reply': True}
            # Outbound sends also have an ambiguity window; expose rather than repeat.
            with self.db:
                self.db.execute("UPDATE outbox SET status='sending' WHERE id=?", (row['id'],))
            try:
                sent = self.telegram.call('sendMessage', **payload)
            except BridgeError:
                with self.db:
                    self.db.execute("UPDATE outbox SET status='uncertain' WHERE id=?", (row['id'],))
                raise
            with self.db:
                self.db.execute("UPDATE outbox SET status='sent' WHERE id=?", (row['id'],))
                if row['target']:
                    self.db.execute('INSERT OR REPLACE INTO routes VALUES (?, ?)', (sent['message_id'], row['target']))

    def recover(self):
        with self.db:
            for row in self.db.execute("SELECT id, name FROM targets").fetchall():
                key = 'brief:' + row['id']
                if get_meta(self.db, key) == 'sending':
                    set_meta(self.db, key, 'uncertain')
                    queue(self.db, 'Bridge setup for ' + row['name'] + ' was interrupted. Messages stay queued; '
                          'check Herdr, then unregister and register this agent again.', row['id'])
            rows = self.db.execute("SELECT * FROM inbox WHERE status='delivering'").fetchall()
            for row in rows:
                queue(self.db, 'Request ' + str(row['id']) + ' was interrupted during delivery. '
                      'Check Herdr before resending.', row['target'], row['message_id'])
            self.db.execute("UPDATE inbox SET status='uncertain' WHERE status='delivering'")
            self.db.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")

    def run(self):
        self.recover()
        print('Telegram bridge running. Ctrl-C to stop.', flush=True)
        while True:
            try:
                updates = self.telegram.call('getUpdates', offset=int(get_meta(self.db, 'offset', '0')),
                                             timeout=3, allowed_updates=['message'])
                self.ingest(updates)
                self.tick()
                self.flush()
            except BridgeError as error:
                print(str(error), file=sys.stderr, flush=True)
                time.sleep(3)


def unregister(db, name):
    target = db.execute('SELECT id FROM targets WHERE name=?', (name,)).fetchone()
    if target:
        db.execute("UPDATE inbox SET status='cancelled' WHERE target=? AND status='queued'", (target['id'],))
        db.execute('DELETE FROM targets WHERE id=?', (target['id'],))
        db.execute('DELETE FROM meta WHERE key=?', ('brief:' + target['id'],))
        db.execute('DELETE FROM routes WHERE target=?', (target['id'],))
        if get_meta(db, 'default_target') == target['id']:
            db.execute("DELETE FROM meta WHERE key='default_target'")


def register(db, herdr, name, pane, default=False):
    state, identity = herdr.snapshot(pane)
    target_id = hashlib.sha256((pane + identity).encode()).hexdigest()[:20]
    old = db.execute('SELECT id FROM targets WHERE name=?', (name,)).fetchone()
    with db:
        if old and old['id'] != target_id:
            default = default or get_meta(db, 'default_target') == old['id']
            unregister(db, name)
        db.execute('''INSERT INTO targets VALUES (?, ?, ?, ?, ?)
                      ON CONFLICT(id) DO UPDATE SET name=excluded.name''',
                   (target_id, name, pane, identity, state))
        if default:
            set_meta(db, 'default_target', target_id)
    return target_id


def connect(root, db, herdr):
    pane = os.environ.get('HERDR_PANE_ID')
    workspace = os.environ.get('HERDR_WORKSPACE_ID')
    if not pane or not workspace:
        raise BridgeError('Connect requires HERDR_PANE_ID and HERDR_WORKSPACE_ID; run inside Herdr.')
    if not load_config(root).get('user_id'):
        raise BridgeError('Pair your Telegram account first.')
    register(db, herdr, 'orchestrator', pane, default=True)
    result = herdr.call('tab', 'create', '--workspace', workspace, '--label', 'Telegram', '--no-focus')
    try:
        bridge_pane = result['root_pane']['pane_id']
        if not isinstance(bridge_pane, str) or not bridge_pane:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise BridgeError('Unexpected Herdr tab response; no pane id.') from None
    command = shlex.join([sys.executable, str(SCRIPT), '--state-dir', str(root), 'run'])
    herdr.call('pane', 'run', bridge_pane, command)
    print('Registered orchestrator; dispatched Telegram bridge to ' + bridge_pane + '.')


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--state-dir', type=Path, default=Path(os.environ.get('WORKSTREAMS_TELEGRAM_STATE', DEFAULT_STATE)))
    sub = p.add_subparsers(dest='command', required=True)
    sub.add_parser('setup', help='Enter bot token locally and generate a one-time pairing code')
    sub.add_parser('connect', help='Register this orchestrator and launch the bridge in a Telegram tab')
    sub.add_parser('run', help='Run the bridge in the foreground inside Herdr')
    sub.add_parser('status', help='Show local routing and delivery state (no credentials)')
    p_reg = sub.add_parser('register', help='Register an agent and pin its foreground process')
    p_reg.add_argument('name')
    p_reg.add_argument('--pane', required=True)
    p_reg.add_argument('--default', action='store_true')
    p_del = sub.add_parser('unregister')
    p_del.add_argument('name')
    for command in ('notify', 'reply'):
        p_send = sub.add_parser(command)
        if command == 'reply':
            p_send.add_argument('request', type=int)
        else:
            p_send.add_argument('--target', required=True)
        content = p_send.add_mutually_exclusive_group(required=True)
        content.add_argument('--text')
        content.add_argument('--file', type=Path)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    root = args.state_dir.expanduser().resolve()
    os.umask(0o077)
    db = database(root)
    if args.command == 'setup':
        if (root / 'config.json').exists():
            raise BridgeError('Configuration already exists; left unchanged.')
        token = getpass.getpass('Telegram bot token: ').strip()
        bot = Telegram(token).call('getMe')
        config = {'token': token, 'pair_code': secrets.token_hex(4)}
        save_config(root, config)
        print('Bot: @' + bot['username'] + '\nSend /pair ' + config['pair_code'] + ' in its private chat, then run the bridge.')
    elif args.command == 'connect':
        connect(root, db, Herdr())
    elif args.command == 'register':
        register(db, Herdr(), args.name, args.pane, args.default)
        print('Registered ' + args.name)
    elif args.command == 'unregister':
        with db:
            unregister(db, args.name)
        print('Unregistered ' + args.name)
    elif args.command == 'status':
        config = load_config(root)
        print('Paired: ' + str(bool(config.get('user_id'))))
        for row in db.execute('SELECT name, pane, last_state FROM targets'):
            print(dict(row))
        for table in ('inbox', 'outbox'):
            print(table + ': ' + str([dict(r) for r in db.execute('SELECT status, count(*) AS count FROM ' + table + ' GROUP BY status')]))
    elif args.command in ('notify', 'reply'):
        config = load_config(root)
        if not config.get('user_id'):
            raise BridgeError('Pair your Telegram account first.')
        body = args.file.read_text() if args.file else args.text
        if not body or len(body) > 20000:
            raise BridgeError('Message must contain 1–20000 characters.')
        reply_to = None
        if args.command == 'reply':
            row = db.execute('SELECT target, message_id FROM inbox WHERE id=?', (args.request,)).fetchone()
            if not row:
                raise BridgeError('Unknown request ID')
            target, reply_to = row['target'], row['message_id']
        else:
            row = db.execute('SELECT id FROM targets WHERE name=?', (args.target,)).fetchone()
            if not row:
                raise BridgeError('Unknown target')
            target = row['id']
        with db:
            queue(db, body, target, reply_to)
        print('Queued for Telegram.')
    elif args.command == 'run':
        if os.environ.get('HERDR_ENV') != '1':
            raise BridgeError('Run the bridge inside Herdr.')
        with (root / 'run.lock').open('w') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise BridgeError('A bridge is already running for this state directory.') from None
            config = load_config(root)
            Bridge(root, config, db, Telegram(config['token']), Herdr()).run()


if __name__ == '__main__':
    try:
        main()
    except (BridgeError, OSError) as error:
        print('workstreams: ' + str(error), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        pass
