"""Generate the deliverable example from the same two test datasets."""
from pathlib import Path

import fv_sync as sync
from tests.test_sync import fixture


def main():
    out = Path('outputs')
    out.mkdir(exist_ok=True)
    rows = sync.compare(fixture('nl'), fixture('de'), {('BEWUST', 'regular_price'): 'Lokale afspraak'})
    (out / 'voorbeeldrapport.html').write_text(sync.html_report(rows), encoding='utf-8')
    (out / 'voorbeeldrapport.csv').write_text(sync.csv_report(rows), encoding='utf-8-sig')
    msg = sync.message(rows, 'rapport@example.invalid', 'ontvanger@example.invalid', '2026-11-09')
    (out / 'voorbeeldrapport.eml').write_bytes(msg.as_bytes())


if __name__ == '__main__':
    main()
