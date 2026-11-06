"""
find_sender.py — work out which DLT sender ID (header) this Aura account accepts.

    python find_sender.py <your-mobile> V2RETL V2SALE V2CLUB V2OFFR
    python find_sender.py <your-mobile> --from-file headers.txt

Why a script and not a button: this is a one-time setup question, and it is the
only check that cannot be done without a real send.

Where those four candidates come from: `E:\\ANTIGRAVITY\\CDP_Mine\\src\\pages\\
Campaigns\\SMS.jsx` hardcodes `DLT_HEADERS = ['V2RETL','V2SALE','V2CLUB','V2OFFR']`.
The DLT template ids sitting beside them in that file are obviously fabricated,
so treat the headers as a LEAD, not a record — somebody may have typed them from
the real approved list, or may have invented them. Trying them costs nothing.
The authoritative source is the Aura portal (Sender IDs / Headers) or the DLT
entity registration with your telecom vendor.

How it stays cheap and safe
---------------------------
The gateway validates in this order: recipient -> sender -> template. So with a
VALID recipient, a wrong header is rejected with code 600 **before the message
is submitted** — nothing is delivered and nothing is charged. Only the correct
header gets through, and the run stops the moment that happens. Worst case the
whole sweep sends exactly ONE message: the successful one.

Use YOUR OWN mobile number. Candidates are tried in the order given.
"""
import os
import sys
import json
import time
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'app'))
sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')

import sms  # noqa: E402

CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('mobile', help='your own 10-digit mobile number')
    ap.add_argument('senders', nargs='*', help='candidate sender IDs to try')
    ap.add_argument('--from-file', help='file with one candidate per line')
    ap.add_argument('--save', action='store_true',
                    help='write the winning sender into config.json')
    args = ap.parse_args()

    candidates = [s.strip().upper() for s in args.senders if s.strip()]
    if args.from_file:
        with open(args.from_file, encoding='utf-8') as f:
            candidates += [ln.strip().upper() for ln in f if ln.strip()
                           and not ln.startswith('#')]
    seen, ordered = set(), []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    if not ordered:
        ap.error('Give at least one candidate sender ID, e.g.\n'
                 '  python find_sender.py <your-mobile> V2RETL V2SALE V2CLUB V2OFFR')

    cfg = json.load(open(CONFIG, encoding='utf-8'))
    if not sms.settings(cfg)['key']:
        sys.exit('No API key in config.json — save it in the admin panel first.')

    print(f'Trying {len(ordered)} sender ID(s) against +91 {args.mobile}.')
    print('A wrong header is rejected before submission — nothing is sent for it.')
    print('The run stops at the first one that is accepted.\n')

    for i, sender in enumerate(ordered, 1):
        probe = {**cfg, 'sms': {**cfg['sms'], 'sender': sender}}
        label = f'[{i}/{len(ordered)}] {sender:<8}'
        try:
            res = sms.send_otp(probe, args.mobile, '123456')
        except sms.SmsError as exc:
            if exc.code == 600:
                print(f'{label} rejected — not a registered header (nothing sent)')
            else:
                print(f'{label} stopped: {exc} (code {exc.code})')
                print('\nThat is not a sender problem — fix it before continuing.')
                return 1
            time.sleep(0.8)
            continue

        print(f'{label} ACCEPTED — tracking id {res.get("uid") or "n/a"}')
        print(f'\nCheck your phone. If the message arrives, "{sender}" is the header.')
        print('If it does NOT arrive, the header is right but the message text does '
              'not match the registered DLT template.')
        if args.save:
            cfg['sms']['sender'] = sender
            json.dump(cfg, open(CONFIG, 'w', encoding='utf-8'), indent=2)
            print(f'\nSaved sender "{sender}" to config.json. Restart the app.')
        else:
            print(f'\nTo keep it:  python find_sender.py {args.mobile} {sender} --save')
        return 0

    print('\nNone of those were accepted. The header has to come from the Aura '
          'portal — look under Sender IDs / Headers, or in any DLT-approved '
          'header list your telecom vendor sent you.')
    return 2


if __name__ == '__main__':
    sys.exit(main())
