import copy
import base64
import json
import os
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch

import requests

import fv_sync as sync

FIXTURES = Path(__file__).parent / 'fixtures'


def fixture(label):
    data = json.loads((FIXTURES / (label + '.json')).read_text(encoding='utf-8'))
    data['rate'] = Decimal(data['rate'])
    return sync.Shop(**data)


def response(data, status=200, headers=None, text=None):
    return Mock(status_code=status, headers=headers or {}, json=Mock(return_value=data),
                text=json.dumps(data) if text is None else text)


class ComparisonTest(unittest.TestCase):
    def setUp(self):
        self.nl, self.de = fixture('nl'), fixture('de')
        self.rows = sync.compare(self.nl, self.de, {('BEWUST', 'regular_price'): 'Lokale afspraak'})

    def test_every_difference_from_two_datasets(self):
        actual = {(r['SKU'], r['veld']) for r in self.rows if r['sectie'] != 'uitzonderingen'}
        expected = {('PRIJS', 'regular_price'), ('ACTIEPRIJS', 'sale_price'),
                    ('ALLEEN-NL', 'exists'), ('ALLEEN-DE', 'exists'), ('STATUS', 'status'),
                    ('VOORRAAD', 'stock_status'), ('ACTIE-VAN', 'date_on_sale_from'),
                    ('ACTIE-TOT', 'date_on_sale_to'), ('VARIABEL', 'variation_count'),
                    ('VAR-ROOD', 'regular_price'), ('VAR-BLAUW', 'exists'),
                    ('', 'missing_sku'), ('LEEG-VS-NUL', 'sale_price'), ('DUBBEL', 'duplicate_sku')}
        self.assertEqual(actual, expected)
        self.assertEqual(sync.discrepancy_count(self.rows), 15)  # two separate products without SKU
        self.assertFalse(any(r['SKU'] in ('GELIJK', 'TOLERANTIE') for r in self.rows))
        self.assertEqual([sync.PRIORITY[r['veld']] for r in self.rows],
                         sorted(sync.PRIORITY[r['veld']] for r in self.rows))

    def test_exception_retained_in_csv_not_counted(self):
        exception = next(r for r in self.rows if r['SKU'] == 'BEWUST')
        self.assertEqual(exception['sectie'], 'uitzonderingen')
        self.assertIn('Lokale afspraak', sync.csv_report(self.rows))
        self.assertIn('zonder SKU', sync.html_report(self.rows))

    def test_gross_both_directions_and_tolerance(self):
        p = self.nl.products[0]
        self.assertEqual(sync.gross(p, 'regular_price', self.nl), Decimal('121.00'))
        self.de.includes_tax = False
        p = dict(p, regular_price='100')
        self.assertEqual(sync.gross(p, 'regular_price', self.de), Decimal('119.00'))
        self.assertEqual(sync.gross(dict(p, tax_status='none'), 'regular_price', self.de), Decimal('100.00'))
        self.nl.products = [dict(p, regular_price='100', sku='X')]
        self.de.products = [dict(p, regular_price='121.02', sku='X')]
        self.de.includes_tax = True
        self.assertEqual(len(sync.compare(self.nl, self.de)), 1)
        self.de.products[0]['regular_price'] = '121.01'
        self.assertEqual(sync.compare(self.nl, self.de), [])

    def test_private_status_and_dates_utc(self):
        a = dict(self.nl.products[0], date_on_sale_from='2026-11-10T00:00:00',
                 date_on_sale_from_gmt='2026-11-09T23:00:00')
        b = dict(a, status='private', regular_price='121', date_on_sale_from='2026-11-09T23:00:00')
        self.nl.products, self.de.products = [a], [b]
        self.assertEqual([r['veld'] for r in sync.compare(self.nl, self.de)], ['status'])

    def test_invalid_price_not_silently_equal(self):
        self.nl.products = [dict(self.nl.products[0], regular_price='NaN')]
        self.de.products = [self.de.products[0]]
        self.assertEqual(sync.compare(self.nl, self.de)[0]['veld'], 'data_error')

    def test_variation_admin_parent_link(self):
        variation = next(r for r in self.rows if r['SKU'] == 'VAR-ROOD')
        self.assertIn('post=9&action=edit#variable_product_options', variation['bewerken_NL'])
        self.assertIn('post=109&action=edit', variation['bewerken_DE'])

    def test_email_csv_html_and_zero(self):
        msg = sync.message(self.rows, 'sender@example.invalid', 'to@example.invalid', '2026-11-09')
        self.assertEqual(str(msg['Subject']), 'FV sync NL↔DE – 2026-11-09 – 15 afwijkingen')
        attachments = list(msg.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertIn('BEWUST', attachments[0].get_payload(decode=True).decode('utf-8-sig'))
        self.assertEqual(sync.message([], 'a@b.invalid', 'c@d.invalid').get_body(preferencelist=('plain',)).get_content().strip(), 'alles gelijk')
        self.assertIn('alles gelijk', sync.html_report([]))

    def test_html_and_csv_injection(self):
        rows = [sync.row('status', '=cmd()', '<script>alert(1)</script>', '+formula', 'draft')]
        self.assertNotIn('<script>', sync.html_report(rows))
        self.assertIn("'=cmd()", sync.csv_report(rows))

    def test_exception_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'exceptions.csv'
            path.write_text('SKU,veld,reden\nX,status,Afspraak\n', encoding='utf-8')
            self.assertEqual(sync.exceptions_from(path), {('X', 'status'): 'Afspraak'})
            path.write_text('SKU,veld,reden\nX,statuz,Typfout\n', encoding='utf-8')
            with self.assertRaises(sync.AuditError):
                sync.exceptions_from(path)


class ApiTest(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = sync.Client('NL', 'https://shop.example.invalid', 'fake-key', 'fake-secret', self.session)
        self.sleep = patch('fv_sync.time.sleep').start()
        self.addCleanup(patch.stopall)

    def test_pagination_and_get_only(self):
        self.session.get.side_effect = [response([{'id': 1}], headers={'X-WP-TotalPages': '2'}), response([{'id': 2}], headers={'X-WP-TotalPages': '2'})]
        self.assertEqual(self.client.pages('products'), [{'id': 1}, {'id': 2}])
        self.assertEqual(self.session.get.call_args.kwargs['params']['page'], 2)
        self.assertFalse(self.session.get.call_args.kwargs['allow_redirects'])
        self.assertGreaterEqual(self.sleep.call_count, 2)
        self.assertFalse(self.session.post.called)
        self.assertFalse(self.session.put.called)
        self.assertFalse(self.session.delete.called)

    def test_rate_limit_retry(self):
        self.session.get.side_effect = [response({}, 429, {'Retry-After': '5'}), response([])]
        self.assertEqual(self.client.pages('products'), [])
        self.sleep.assert_any_call(5.0)

    def test_http_and_transport_errors_sanitized(self):
        self.session.get.return_value = response({}, 403)
        with self.assertRaisesRegex(sync.AuditError, 'HTTP 403'):
            self.client.get('settings/tax/woocommerce_prices_include_tax')
        self.session.get.side_effect = requests.ConnectionError('fake-secret-sensitive')
        with self.assertRaises(sync.AuditError) as caught:
            self.client.get('products')
        self.assertIn('geen HTTP-respons', str(caught.exception))
        self.assertNotIn('fake-secret', str(caught.exception))

    def test_loads_settings_rates_and_all_variations(self):
        parent = dict(fixture('nl').products[8], tax_class='', tax_status='taxable')
        variation = dict(fixture('nl').products[9], tax_class='parent')
        fake = Mock(base='https://shop.example.invalid')
        fake.get.return_value = ({'value': 'no'}, {})
        fake.pages.side_effect = [[{'country': 'NL', 'rate': '21', 'class': 'standard'}], [parent], [variation]]
        shop = sync.load_shop(fake, 'NL')
        self.assertEqual(len(shop.products), 2)
        self.assertEqual(shop.products[-1]['variation_count'], 1)
        self.assertEqual(shop.products[0]['tax_class'], '')
        self.assertEqual(fake.pages.call_args.kwargs['status'], 'any')

    def test_ambiguous_tax_fails_closed(self):
        rates = [{'country': 'NL', 'rate': '21', 'class': 'standard'}]
        self.assertEqual(sync.standard_rate(rates, 'NL'), Decimal('21'))
        with self.assertRaises(sync.AuditError):
            sync.standard_rate(rates + copy.deepcopy(rates), 'NL')

    def test_incomplete_product_is_not_reported_as_equal(self):
        with self.assertRaisesRegex(sync.AuditError, 'HTTP 200, onvolledig'):
            sync.validate_product({'id': 1, 'sku': 'X'}, 'NL')

    def test_tax_fallback_401_403_for_both_countries(self):
        for country in ('NL', 'DE'):
            for status in (401, 403):
                for value in ('yes', 'no'):
                    with self.subTest(country=country, status=status, value=value), \
                         patch.dict(os.environ, {country + '_PRICES_INCLUDE_TAX': value}):
                        fake = Mock(base='https://shop.example.invalid')
                        fake.get.side_effect = sync.AuditError('settings forbidden', status_code=status)
                        fake.pages.side_effect = [[{'country': country, 'rate': '21' if country == 'NL' else '19'}], []]
                        shop = sync.load_shop(fake, country)
                        self.assertEqual(shop.includes_tax, value == 'yes')
                        self.assertEqual(fake.pages.call_count, 2)

    def test_tax_fallback_not_used_on_other_errors_or_api_success(self):
        fake = Mock(base='https://shop.example.invalid')
        with patch.dict(os.environ, {'NL_PRICES_INCLUDE_TAX': 'no'}):
            for status in (404, 429, 500, None):
                fake.get.side_effect = sync.AuditError('failed', status_code=status)
                with self.assertRaises(sync.AuditError):
                    sync.load_shop(fake, 'NL')
            fake.get.side_effect = None
            fake.get.return_value = ({'value': 'yes'}, {})
            fake.pages.side_effect = [[{'country': 'NL', 'rate': '21'}], []]
            self.assertTrue(sync.load_shop(fake, 'NL').includes_tax)

    def test_tax_fallback_requires_explicit_yes_no(self):
        for value in ('', 'true', 'YES'):
            with patch.dict(os.environ, {'NL_PRICES_INCLUDE_TAX': value}):
                fake = Mock()
                fake.get.side_effect = sync.AuditError('forbidden', status_code=403)
                with self.assertRaisesRegex(sync.AuditError, 'HTTP 403; NL_PRICES_INCLUDE_TAX moet yes/no'):
                    sync.load_shop(fake, 'NL')

    def test_http_body_html_json_and_each_retry_logged_safely(self):
        auth = base64.b64encode(b'fake-key:fake-secret').decode()
        body = '<html>Cloudflare fake-key fake-secret ' + auth + '\n</html>'
        self.session.get.side_effect = [response({}, 503, text=body),
                                        response({}, 403, text='{"code":"woocommerce_rest_cannot_view","key":"fake-key"}')]
        with self.assertLogs('fv_sync', level='WARNING') as captured, self.assertRaises(sync.AuditError):
            self.client.get('products')
        log = '\n'.join(captured.output)
        self.assertIn('<html>Cloudflare', log)
        self.assertIn('woocommerce_rest_cannot_view', log)
        self.assertEqual(len(captured.output), 2)
        for value in ('fake-key', 'fake-secret', auth):
            self.assertNotIn(value, log)
        self.assertEqual(sync.safe_response('X' * 400, 'fake-key', 'fake-secret'), 'X' * 300)
        self.assertNotIn('fake-secret', sync.safe_response('X' * 295 + 'fake-secret', 'fake-key', 'fake-secret'))

    def test_http_body_redacts_encoded_and_other_shop_secrets(self):
        from urllib.parse import quote
        with patch.dict(os.environ, {'DE_SECRET': 'secret+other/value'}):
            body = quote('secret+other/value', safe='') + ' ck_unknown123 cs_unknown456'
            result = sync.safe_response(body, 'fake-key', 'fake-secret')
        self.assertNotIn('other', result)
        self.assertNotIn('ck_', result)
        self.assertNotIn('cs_', result)

    def test_failed_shop_sends_http_alert_without_false_missing(self):
        sent = []
        env = {k: 'fake' for k in ['NL_KEY', 'NL_SECRET', 'DE_KEY', 'DE_SECRET', 'SMTP_PASS']}
        env.update(NL_URL='https://nl.example.invalid', DE_URL='https://de.example.invalid',
                   SMTP_HOST='smtp.example.invalid', SMTP_PORT='465', SMTP_USER='from@example.invalid', MAIL_TO='to@example.invalid')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, env | {'GITHUB_STEP_SUMMARY': str(Path(tmp) / 'job.md')}), \
             patch('sys.argv', ['fv_sync.py', '--output-dir', tmp]), \
             patch('fv_sync.load_shop', side_effect=[sync.AuditError('NL products: HTTP 503'), fixture('de')]), \
             patch('fv_sync.smtplib.SMTP_SSL') as smtp:
            smtp.return_value.__enter__.return_value.send_message.side_effect = sent.append
            self.assertEqual(sync.main(), 2)
            self.assertIn('HTTP 503', (Path(tmp) / 'job.md').read_text(encoding='utf-8'))
            self.assertIn('HTTP 503', (Path(tmp) / 'fv-sync.csv').read_text(encoding='utf-8-sig'))
        self.assertEqual(len(sent), 1)
        self.assertIn('HTTP 503', sent[0].get_body(preferencelist=('plain',)).get_content())
        self.assertNotIn('ontbreekt', sent[0].get_body(preferencelist=('plain',)).get_content())


class ReportingTest(unittest.TestCase):
    def run_main(self, shops, env=None, mail_error=None):
        credentials = {key: 'fake' for key in ('NL_KEY', 'NL_SECRET', 'DE_KEY', 'DE_SECRET')}
        credentials.update(NL_URL='https://nl.example.invalid', DE_URL='https://de.example.invalid')
        with tempfile.TemporaryDirectory() as tmp, \
             patch.dict(os.environ, credentials | (env or {}) | {'GITHUB_STEP_SUMMARY': str(Path(tmp) / 'job.md')}, clear=True), \
             patch('sys.argv', ['fv_sync.py', '--output-dir', tmp]), \
             patch('fv_sync.load_shop', side_effect=shops), \
             patch('fv_sync.smtplib.SMTP_SSL') as smtp, patch('fv_sync.smtplib.SMTP') as starttls:
            if mail_error:
                smtp.side_effect = mail_error
            result = sync.main()
            return (result, (Path(tmp) / 'job.md').read_text(encoding='utf-8'),
                    (Path(tmp) / 'fv-sync.csv').read_text(encoding='utf-8-sig'),
                    smtp.call_count + starttls.call_count)

    def test_no_mail_equal_exit_zero_and_summary_csv(self):
        a, b = fixture('nl'), fixture('de')
        a.products, b.products = a.products[:1], b.products[:1]
        code, summary, csv, calls = self.run_main([a, b])
        self.assertEqual(code, 0)
        self.assertIn('alles gelijk', summary)
        self.assertEqual(len(csv.splitlines()), 1)
        self.assertEqual(calls, 0)

    def test_differences_exit_one_without_or_with_partial_mail_config(self):
        for env in ({}, {'SMTP_HOST': 'smtp.example.invalid', 'SMTP_PORT': '465', 'SMTP_USER': 'a@b.invalid', 'SMTP_PASS': 'fake'}):
            code, summary, csv, calls = self.run_main([fixture('nl'), fixture('de')], env)
            self.assertEqual(code, 1)
            self.assertIn('PRIJS', summary)
            self.assertIn('PRIJS', csv)
            self.assertEqual(calls, 0)  # Missing MAIL_TO also disables mail

    def test_smtp_failure_still_writes_reports_and_exit_two(self):
        env = dict(SMTP_HOST='smtp.example.invalid', SMTP_PORT='465', SMTP_USER='a@b.invalid', SMTP_PASS='fake', MAIL_TO='c@d.invalid')
        code, summary, csv, calls = self.run_main([fixture('nl'), fixture('de')], env, OSError('private details'))
        self.assertEqual(code, 2)
        self.assertIn('Mail versturen mislukt', summary)
        self.assertIn('Mail versturen mislukt', csv)
        self.assertNotIn('private details', summary)
        self.assertEqual(calls, 1)

    def test_summary_attempted_when_csv_cannot_be_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / 'fv-sync.csv').mkdir()
            summary = directory / 'job.md'
            with patch.dict(os.environ, {'GITHUB_STEP_SUMMARY': str(summary)}):
                self.assertFalse(sync.save_report([], directory))
            self.assertIn('alles gelijk', summary.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
