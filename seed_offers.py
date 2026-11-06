"""
seed_offers.py — load the offer catalogue into Snowflake once.

    python seed_offers.py          # insert anything missing, leave edits alone
    python seed_offers.py --force  # overwrite every seeded offer back to source

Mechanics below are transcribed from the documented sources, NOT invented:

  Bill Buster      Marketing Offers Strategy (21-Jul-2026), offer #1:
                   tiered Rs100 off >=999 / Rs250 off >=2499 / Rs500 off >=3599
  Spin The Wheel   same catalogue, offer #7: spin URL -> 1 of 4 gated vouchers,
                   Rs50 off >=500 / Rs100 >=999 / Rs150 >=1499 / Rs200 >=1799
  Selfie           same catalogue, offer #5: post a selfie tagging V2 ->
                   Rs100 voucher on the next bill
  70% Off          Dormant Customer Segmentation + Tees @ Rs1 notes:
                   up to 70% off on C-article, auto-applied at the counter
  V2 VIP Club      live card SKUs: Silver Rs200/2%, Gold Rs500/5%,
                   Platinum Rs1000/11%, valid 1 year (launched 10-Sep-2026)
  Festival gift    Festival Master bill slabs: Rs1,599-2,498 -> Gift 1 + Rs159
                   co-pay; Rs2,499-3,598 -> Gift 2 + Rs399; Rs3,599+ -> Gift 3
                   + Rs499
  Birthday /       catalogue offers #8 and #9: 15% off one bill in your birthday
  Anniversary      month (7-day validity) / 12% off one bill on your first-bill
                   anniversary month

VALID_FROM / VALID_TO are deliberately left NULL (= always on). Only the V2 VIP
Club had a confirmed live run at the time of seeding; set real windows for the
rest in the admin panel so the app stops showing an offer the day it ends.
"""
import sys
import argparse

sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION\Customer App\app')
sys.path.insert(0, r'E:\ANAND PRAKASH\AUTOMATION')

import sf_admin  # noqa: E402

OFFERS = [
    {
        'offer_id': 'BILL-BUSTER', 'title': 'Bill Buster', 'icon': '💥',
        'subtitle': 'The more you shop, the more you save',
        'offer_type': 'BILL_BUSTER', 'badge': 'UP TO ₹500 OFF', 'priority': 10,
        'min_bill': 999,
        'description': 'A flat discount that grows with your bill — nothing to '
                       'collect, nothing to redeem. It comes off at the counter.',
        'mechanic': 'Shop for ₹999 or more and get ₹100 off.\n'
                    'Shop for ₹2,499 or more and get ₹250 off.\n'
                    'Shop for ₹3,599 or more and get ₹500 off.',
        'terms': 'One slab per bill, applied on the highest slab your bill '
                 'qualifies for. Applies to eligible merchandise at the billing '
                 'counter. Cannot be clubbed with another bill-value offer.',
    },
    {
        'offer_id': 'SPIN-THE-WHEEL', 'title': 'Spin The Wheel', 'icon': '🎡',
        'subtitle': 'One spin, one voucher — every spin wins',
        'offer_type': 'SPIN_WHEEL', 'badge': 'WIN UP TO ₹200', 'priority': 20,
        'description': 'We send you a spin link. Give it one spin and you win a '
                       'voucher to use on your next V2 bill. Nobody spins and loses.',
        'mechanic': 'Spin and win one of four vouchers:\n'
                    '₹50 off on a bill of ₹500 or more\n'
                    '₹100 off on a bill of ₹999 or more\n'
                    '₹150 off on a bill of ₹1,499 or more\n'
                    '₹200 off on a bill of ₹1,799 or more',
        'terms': 'One spin per registered mobile number. The voucher is tied to '
                 'your number — just give it at the counter. Each voucher has its '
                 'own minimum bill value, shown on the voucher.',
    },
    {
        'offer_id': 'SELFIE', 'title': 'Selfie Offer', 'icon': '🤳',
        'subtitle': 'Post a selfie, get ₹100 off your next bill',
        'offer_type': 'SELFIE', 'badge': '₹100 VOUCHER', 'priority': 30,
        'description': 'Loved your V2 haul? Post a selfie, tag V2 Retail, and '
                       'we will put ₹100 on your next bill.',
        'mechanic': 'Post a selfie on social media tagging V2 Retail. '
                    'Show it at any V2 store to claim a ₹100 voucher, '
                    'redeemable on your next purchase.',
        'terms': 'One voucher per registered mobile number. The post must tag '
                 'V2 Retail and be publicly visible when you claim.',
    },
    {
        'offer_id': 'UPTO-70-OFF', 'title': 'Up to 70% Off', 'icon': '🏷️',
        'subtitle': 'End-of-season prices across the floor',
        'offer_type': 'DISCOUNT', 'badge': 'UP TO 70% OFF', 'priority': 15,
        'description': 'Season-end and clearance merchandise at up to 70% off. '
                       'The discount is already on the price tag — walk in and shop.',
        'mechanic': 'Up to 70% off on participating clearance merchandise, '
                    'applied automatically at the billing counter. No coupon '
                    'and no minimum bill.',
        'terms': 'Applies to participating articles only, while stocks last. '
                 'The discount varies by article; "up to 70%" is the maximum.',
    },
    {
        'offer_id': 'VIP-CLUB', 'title': 'V2 VIP Club', 'icon': '✦',
        'subtitle': 'Buy a card once, save on every bill for a year',
        'offer_type': 'MEMBERSHIP', 'badge': 'UP TO 11% OFF', 'priority': 5,
        'description': 'Our membership programme. Pick a card at any V2 store '
                       'and your discount applies on every eligible purchase for '
                       'the next 12 months.',
        'mechanic': 'Silver — ₹200 for the year, 2% off every eligible bill.\n'
                    'Gold — ₹500 for the year, 5% off every eligible bill.\n'
                    'Platinum — ₹1,000 for the year, 11% off every eligible bill.',
        'terms': 'Valid for one year from the date you buy the card. The discount '
                 'applies to eligible merchandise against your registered mobile '
                 'number. Ask any V2 store to issue or renew a card.',
    },
    {
        'offer_id': 'FESTIVAL-GIFT', 'title': 'Festival Gift', 'icon': '🎁',
        'subtitle': 'Shop above the slab, take a gift home',
        'offer_type': 'FESTIVAL', 'badge': 'GIFT WITH BILL', 'priority': 25,
        'min_bill': 1599,
        'description': 'During festival weeks, bills above ₹1,599 unlock a gift '
                       'from the V2 gift counter for a small co-pay.',
        'mechanic': 'Bill of ₹1,599–₹2,498 — Gift 1 for ₹159.\n'
                    'Bill of ₹2,499–₹3,598 — Gift 2 for ₹399.\n'
                    'Bill of ₹3,599 and above — Gift 3 for ₹499.',
        'terms': 'Available during festival periods at participating stores, '
                 'while stocks last. Gift options vary by store and festival. '
                 'One gift per qualifying bill.',
    },
    {
        'offer_id': 'BIRTHDAY', 'title': 'Birthday Offer', 'icon': '🎂',
        'subtitle': '15% off, our treat, in your birthday month',
        'offer_type': 'OTHER', 'badge': '15% OFF', 'priority': 40,
        'segment': 'ALL',
        'description': 'Tell us your date of birth under My details and we will '
                       'send you 15% off one bill during your birthday month.',
        'mechanic': '15% off a single bill, valid for 7 days within your '
                    'birthday month. Add your date of birth under My details '
                    'to receive it.',
        'terms': 'One bill per member per year. Applies to eligible merchandise.',
    },
    {
        'offer_id': 'ANNIVERSARY', 'title': 'Anniversary Offer', 'icon': '💐',
        'subtitle': '12% off to mark your anniversary with V2',
        'offer_type': 'OTHER', 'badge': '12% OFF', 'priority': 45,
        'description': 'A thank-you for shopping with us — 12% off one bill in '
                       'your anniversary month.',
        'mechanic': '12% off a single bill during your anniversary month. '
                    'Add your anniversary date under My details to receive it.',
        'terms': 'One bill per customer per year. Applies to eligible merchandise.',
    },
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true',
                    help='overwrite offers that already exist')
    args = ap.parse_args()

    sf_admin.ensure_tables()
    existing = {o['offer_id'] for o in sf_admin.list_offers()}

    added = skipped = updated = 0
    for o in OFFERS:
        if o['offer_id'] in existing and not args.force:
            print(f"  skip    {o['offer_id']:<16} (already in Snowflake)")
            skipped += 1
            continue
        sf_admin.save_offer({**o, 'is_active': True, 'scope': 'ALL',
                             'segment': o.get('segment', 'ALL')}, by='seed')
        if o['offer_id'] in existing:
            print(f"  update  {o['offer_id']:<16} {o['title']}")
            updated += 1
        else:
            print(f"  add     {o['offer_id']:<16} {o['title']}")
            added += 1

    print(f'\n{added} added, {updated} updated, {skipped} left alone.')
    print(f'Catalogue now holds {len(sf_admin.list_offers())} offers in '
          f'{sf_admin.T_OFFER}.')
    print('\nValidity windows are open-ended — set real start/end dates in the '
          'admin panel so an offer stops showing the day it ends.')


if __name__ == '__main__':
    main()
