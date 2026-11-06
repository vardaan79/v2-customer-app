"""
serve.py — production entry point.

    python serve.py                 # reads host/port from config.json
    python serve.py --port 8080
    python serve.py --check         # validate config and exit, send nothing

Runs the app under waitress, not Flask's development server. Flask's own server
is single-process, single-threaded by default and explicitly not for production;
waitress is a real WSGI server, runs on Windows, and needs no C compiler.

Put a reverse proxy (nginx / IIS / Caddy) in front of this for TLS. The app sets
no cookies — the shopper's session token lives in localStorage and travels in an
Authorization header — but the OTP, the bills and the customer's details all
cross the wire, so HTTPS is not optional.
"""
import os
import sys
import socket
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))
sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')

import sms                         # noqa: E402
import store                       # noqa: E402
import admin as admin_mod          # noqa: E402
import snowflake_data as data      # noqa: E402
from server import app             # noqa: E402


def preflight(cfg):
    """Everything that should be true before customers arrive. Returns a list of
    (level, message); 'BLOCK' entries stop the boot."""
    out = []

    if cfg.get('dev_mode'):
        out.append(('BLOCK', 'dev_mode is ON — a failed SMS would print the OTP '
                             'on the customer\'s screen. Turn it off in '
                             'config.json or the admin panel.'))
    if not cfg.get('admin_password_hash'):
        out.append(('BLOCK', 'No admin password is set.'))
    if not sms.is_configured(cfg):
        gaps = sms.missing_fields(cfg) or ['sending is switched off']
        out.append(('BLOCK', 'OTP SMS is not ready: ' + ', '.join(gaps)))
    else:
        s = sms.settings(cfg)
        out.append(('OK', f"OTP SMS live via {sms.DOMAIN} · sender {s['sender']} "
                          f"· template {s['template_id']}"))
        for w in sms.check_template(s['otp_template'], s['sender']):
            out.append(('WARN', w))

    try:
        through = data.data_through()
        out.append(('OK', f'Snowflake reachable · sales data through {through}'))
    except Exception as exc:
        out.append(('BLOCK', f'Snowflake unreachable: {exc}'))

    try:
        import content
        n = len(content.store_directory())
        out.append(('OK' if n else 'WARN',
                    f'{n} stores published from the uploaded sheet'
                    + ('' if n else ' — Find My Store will be empty')))
    except Exception as exc:
        out.append(('WARN', f'Store list unavailable: {exc}'))

    out.append(('OK', f'Purchases visible from {data.purchase_cutoff()} onwards'))
    out.append(('OK', f'Sessions last {store.SESSION_DAYS} days and renew on use'))

    pending = store.sync_status()['pending_sync']
    if pending:
        out.append(('WARN', f'{pending} customer profile(s) still queued for Snowflake'))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--host')
    ap.add_argument('--port', type=int)
    ap.add_argument('--threads', type=int, default=8)
    ap.add_argument('--check', action='store_true', help='run preflight and exit')
    ap.add_argument('--force', action='store_true',
                    help='start even if preflight found blockers')
    args = ap.parse_args()

    store.init()
    cfg = store.config()
    host = args.host or cfg.get('bind_host') or '0.0.0.0'
    port = args.port or int(cfg.get('port', 5017))

    print('=' * 70)
    print(' V2 Retail — Customer App  (production)')
    print('=' * 70)
    checks = preflight(cfg)
    blockers = [m for lvl, m in checks if lvl == 'BLOCK']
    for lvl, msg in checks:
        print(f'  [{lvl:<5}] {msg}')

    if blockers and not args.force:
        print('\n Not starting. Fix the BLOCK items above, or pass --force if you')
        print(' are deliberately running an internal-only instance.')
        return 1
    if args.check:
        print('\n --check only; not starting.')
        return 0 if not blockers else 1

    try:
        lan = socket.gethostbyname(socket.gethostname())
    except OSError:
        lan = host
    print(f'\n  listening on   http://{host}:{port}')
    print(f'  customer app   http://{lan}:{port}/')
    print(f'  admin panel    http://{lan}:{port}/admin')
    print('  Put TLS in front of this — never expose it to the internet directly.')
    print('=' * 70, flush=True)

    from waitress import serve
    serve(app, host=host, port=port, threads=args.threads,
          ident='V2-Customer-App', channel_timeout=120)
    return 0


if __name__ == '__main__':
    sys.exit(main())
