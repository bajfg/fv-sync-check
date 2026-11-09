"""Read-only WooCommerce audit. Only requests is an external dependency."""
import argparse
import base64
import csv
import html
import io
import json
import logging
import os
import re
import smtplib
import ssl
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from email.message import EmailMessage
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit, quote, quote_plus

import requests

LOG = logging.getLogger("fv_sync")
USER_AGENT = "ForaVida-SyncCheck/1.0 (+https://www.foravida.nl)"
TECHNICAL_FIELDS = {"api_error", "data_error"}
FIELDS = {"exists", "status", "regular_price", "sale_price", "date_on_sale_from",
          "date_on_sale_to", "stock_status", "variation_count"}
PRIORITY = {"regular_price": 0, "sale_price": 0, "exists": 1, "status": 2,
            "stock_status": 2, "date_on_sale_from": 3, "date_on_sale_to": 3,
            "variation_count": 4, "missing_sku": 5, "duplicate_sku": 5,
            "data_error": 5, "api_error": -1}
COLUMNS = ["sectie", "SKU", "productnaam", "veld", "waarde_NL", "waarde_DE",
           "bewerken_NL", "bewerken_DE", "reden"]


class AuditError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


def safe_response(text, key, secret):
    """Redact before truncating, including echoed URL/Basic-auth representations."""
    values = {key, secret, *(os.environ.get(name, "") for name in
              ("NL_KEY", "NL_SECRET", "DE_KEY", "DE_SECRET", "SMTP_PASS"))}
    values.add(base64.b64encode(f"{key}:{secret}".encode()).decode())
    variants = set()
    for value in values - {""}:
        variants.update((value, quote(value, safe=""), quote_plus(value),
                         json.dumps(value)[1:-1]))
    for value in sorted(variants, key=len, reverse=True):
        text = text.replace(value, "[AFGESCHERMD]")
    text = re.sub(r"\b(?:ck|cs)_[a-zA-Z0-9]+", "[AFGESCHERMD]", text)
    return text[:300]


def base_url(value):
    p = urlsplit(value)
    if p.scheme != "https" or not p.netloc or p.username or p.password or p.query or p.fragment:
        raise AuditError("Shop-URL moet HTTPS gebruiken, zonder credentials, query of fragment")
    result = value.rstrip("/")
    if result.endswith("/wp-json/wc/v3"):
        result = result[:-len("/wp-json/wc/v3")]
    return result


class Client:
    def __init__(self, label, url, key, secret, session=None):
        self.label, self.base = label, base_url(url)
        self.session = session or requests.Session()
        self.session.auth = (key, secret)
        self.key, self.secret = key, secret
        self.last_request = 0.0

    def get(self, endpoint, params=None):
        """Only GET; no redirects, no credentials in query strings or logs."""
        status = "geen HTTP-respons"
        status_code = None
        for attempt in range(4):
            time.sleep(max(0, 0.5 - (time.monotonic() - self.last_request)))
            self.last_request = time.monotonic()
            try:
                response = self.session.get(self.base + "/wp-json/wc/v3/" + endpoint,
                                            params=params, headers={"User-Agent": USER_AGENT},
                                            timeout=(10, 60), allow_redirects=False)
            except requests.RequestException:
                status_code = None
                status = "geen HTTP-respons (netwerk/TLS/timeout)"
                if attempt < 3:
                    time.sleep(2 ** attempt)
                    continue
                break
            status = "HTTP " + str(response.status_code)
            status_code = response.status_code
            LOG.info("%s GET %s %s", self.label, endpoint, status)
            if response.status_code != 200:
                LOG.warning("%s GET %s %s response=%r", self.label, endpoint, status,
                            safe_response(response.text, self.key, self.secret))
            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt < 3:
                    retry = response.headers.get("Retry-After", "")
                    try:
                        delay = float(retry)
                    except ValueError:
                        try:
                            delay = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                        except (ValueError, TypeError):
                            delay = 2 ** attempt
                    time.sleep(min(120, max(2 ** attempt, delay)))
                    continue
            if response.status_code != 200:
                break
            try:
                return response.json(), response.headers
            except ValueError:
                raise AuditError(f"{self.label} {endpoint}: HTTP 200, ongeldige JSON") from None
        raise AuditError(f"{self.label} {endpoint}: {status}", status_code=status_code)

    def pages(self, endpoint, **params):
        result = []
        for page in range(1, 100001):
            data, headers = self.get(endpoint, dict(params, per_page=100, page=page))
            if not isinstance(data, list):
                raise AuditError(f"{self.label} {endpoint}: HTTP 200, lijst verwacht")
            result.extend(data)
            total = headers.get("X-WP-TotalPages")
            if (total is not None and page >= int(total)) or (total is None and len(data) < 100):
                return result
        raise AuditError(f"{self.label}: pagineringslimiet overschreden")


@dataclass
class Shop:
    label: str
    base: str
    includes_tax: bool
    rate: Decimal
    products: list


def configured_vat_rate(country):
    name = country + "_VAT_RATE"
    raw = os.environ.get(name, "").strip() or {"NL": "21", "DE": "19"}[country]
    try:
        rate = Decimal(raw)
        if not rate.is_finite() or rate < 0:
            raise ValueError
    except (InvalidOperation, ValueError):
        raise AuditError(f"{name} moet een eindig, niet-negatief percentage zijn (decimale punt)") from None
    return rate


def check_tax_configuration(client, country):
    """Diagnostics only: API tax records never determine the comparison rate."""
    try:
        rates = client.pages("taxes", **{"class": "standard"})
        relevant = [r for r in rates if r.get("country") == country
                    and r.get("class", "standard") in ("", "standard")]
        generic = [r for r in relevant if not any(r.get(k) for k in
                   ("state", "postcode", "city", "postcodes", "cities", "compound"))]
        if len(generic) != 1 or len(relevant) != 1:
            LOG.warning("%s /taxes niet eenduidig; vergelijking gaat door met %s_VAT_RATE/default", country, country)
    except (AuditError, ValueError, KeyError, TypeError, AttributeError):
        LOG.warning("%s /taxes niet controleerbaar; vergelijking gaat door met %s_VAT_RATE/default", country, country)


def prices_include_tax(client, country):
    name = country + "_PRICES_INCLUDE_TAX"
    value = os.environ.get(name, "").strip()
    # Explicit configuration takes priority: yes/yes must always compare directly.
    if value:
        if value not in ("yes", "no"):
            raise AuditError(f"{name} moet yes/no zijn")
        LOG.info("%s: gebruik %s=%s", country, name, value)
        return value == "yes"
    try:
        setting, _ = client.get("settings/tax/woocommerce_prices_include_tax")
    except AuditError as error:
        if error.status_code in (401, 403):
            raise AuditError(f"{country} settings/tax: HTTP {error.status_code}; stel {name} in op yes/no") from None
        raise
    if not isinstance(setting, dict) or setting.get("value") not in ("yes", "no"):
        raise AuditError(f"{country}: woocommerce_prices_include_tax ontbreekt/is ongeldig")
    return setting["value"] == "yes"


def load_shop(client, country):
    included = prices_include_tax(client, country)
    rate = configured_vat_rate(country)
    check_tax_configuration(client, country)
    products = client.pages("products", status="any", context="edit", orderby="id", order="asc")
    all_products = []
    for product in products:
        validate_product(product, country)
        product = dict(product, parent_id=0, variation_count=0)
        if product.get("type") == "variable":
            variations = client.pages(f"products/{product['id']}/variations", status="any",
                                      context="edit", orderby="id", order="asc")
            product["variation_count"] = len(variations)
            for variation in variations:
                validate_product(variation, country, variation=True)
                v = dict(variation, parent_id=product["id"], variation_count=0)
                attrs = ", ".join(str(a.get("option", "")) for a in v.get("attributes", []))
                v["name"] = product["name"] + (" — " + attrs if attrs else " — variatie")
                if v.get("tax_class") == "parent":
                    v["tax_class"] = product.get("tax_class", "")
                if not v.get("tax_status"):
                    v["tax_status"] = product.get("tax_status", "taxable")
                all_products.append(v)
        all_products.append(product)
    return Shop(country, client.base, included, rate, all_products)


def validate_product(product, country, variation=False):
    fields = {"id", "sku", "status", "regular_price", "sale_price", "stock_status",
              "date_on_sale_from", "date_on_sale_to"}
    if not variation:
        fields |= {"name", "type"}
    if not isinstance(product, dict) or not fields <= product.keys():
        raise AuditError(f"{country} producten: HTTP 200, onvolledig productrecord")
    if not isinstance(product["id"], int) or not isinstance(product["sku"], str):
        raise AuditError(f"{country} producten: HTTP 200, ongeldig product-ID/SKU")


def gross(product, field, shop):
    raw = product.get(field)
    if raw in (None, ""):
        return None
    value = Decimal(str(raw))
    if not value.is_finite() or value < 0:
        raise ValueError("ongeldig prijsbedrag")
    if not shop.includes_tax and product.get("tax_status", "taxable") == "taxable":
        if product.get("tax_class", "") not in ("", "standard"):
            raise ValueError("afwijkende belastingklasse; bruto niet betrouwbaar bepaalbaar")
        value *= 1 + shop.rate / 100
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def sale_date(product, field):
    # UTC prevents false differences between shops' local time zones.
    gmt = product.get(field + "_gmt")
    raw = gmt or product.get(field)
    if not raw:
        return ""
    value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    if gmt:
        value = value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value.isoformat()
    return value.isoformat()  # fallback for imported/older API records


def edit_link(shop, product):
    if not product:
        return ""
    ident = product.get("parent_id") or product["id"]
    suffix = "#variable_product_options" if product.get("parent_id") else ""
    return f"{shop.base}/wp-admin/post.php?post={int(ident)}&action=edit{suffix}"


def exceptions_from(path):
    result = {}
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, strict=True)
        if reader.fieldnames != ["SKU", "veld", "reden"]:
            raise AuditError("exceptions.csv: verwachte kolommen SKU,veld,reden")
        for row in reader:
            if None in row or any(row.get(k) is None for k in ("SKU", "veld", "reden")):
                raise AuditError(f"exceptions.csv: onjuist aantal kolommen op regel {reader.line_num}")
            sku, field, reason = (row[k].strip() for k in ("SKU", "veld", "reden"))
            if not sku or field not in FIELDS or not reason or (sku, field) in result:
                raise AuditError(f"exceptions.csv: ongeldige of dubbele regel {reader.line_num}")
            result[sku, field] = reason
    return result


def row(field, sku="", name="", nl="", de="", nl_link="", de_link="", reason="", section="afwijkingen"):
    if field in TECHNICAL_FIELDS:
        section = "Technische fouten"
    return dict(zip(COLUMNS, [section, sku, name, field, str(nl), str(de), nl_link, de_link, reason]))


def compare(nl, de, exceptions=None):
    exceptions = exceptions or {}
    rows = []
    indexes = []
    for shop in (nl, de):
        index = defaultdict(list)
        for p in shop.products:
            sku = p.get("sku", "")
            if not sku or not sku.strip():
                rows.append(row("missing_sku", name=p.get("name", ""),
                                nl=f"ID {p['id']}" if shop is nl else "",
                                de=f"ID {p['id']}" if shop is de else "",
                                nl_link=edit_link(shop, p) if shop is nl else "",
                                de_link=edit_link(shop, p) if shop is de else "", section="zonder SKU"))
            else:
                index[sku].append(p)
        indexes.append(index)
    for sku in sorted(set(indexes[0]) | set(indexes[1])):
        left, right = indexes[0].get(sku, []), indexes[1].get(sku, [])
        a, b = (left or [None])[0], (right or [None])[0]
        name = (a or b).get("name", "")
        links = (edit_link(nl, a), edit_link(de, b))
        def add(field, x, y):
            reason = exceptions.get((sku, field), "")
            rows.append(row(field, sku, name, x, y, *links, reason=reason,
                            section="uitzonderingen" if reason else "afwijkingen"))
        if len(left) > 1 or len(right) > 1:
            add("duplicate_sku", ",".join(str(p['id']) for p in left),
                ",".join(str(p['id']) for p in right))
            continue
        if not a or not b:
            add("exists", "aanwezig" if a else "ontbreekt", "aanwezig" if b else "ontbreekt")
            continue
        for field in ("regular_price", "sale_price", "status", "stock_status",
                      "date_on_sale_from", "date_on_sale_to", "variation_count"):
            try:
                if field.endswith("price"):
                    x, y = gross(a, field, nl), gross(b, field, de)
                    differs = (x != y) if x is None or y is None else abs(x - y) > Decimal("0.01")
                elif field.startswith("date_on_sale"):
                    x, y = sale_date(a, field), sale_date(b, field)
                    differs = x != y
                else:
                    x, y = a.get(field, 0 if field == "variation_count" else ""), b.get(field, 0 if field == "variation_count" else "")
                    differs = x != y
                if differs:
                    add(field, "leeg" if x is None else x, "leeg" if y is None else y)
            except (ValueError, InvalidOperation) as error:
                add("data_error", f"{field}: {error}", f"{field}: controle nodig")
    return sorted(rows, key=lambda r: (PRIORITY[r["veld"]], r["SKU"], r["veld"]))


def discrepancy_count(rows):
    return sum(r["sectie"] != "uitzonderingen" and r["veld"] not in TECHNICAL_FIELDS for r in rows)


def has_technical_errors(rows):
    return any(r["veld"] in TECHNICAL_FIELDS for r in rows)


def report_title(rows, date=None):
    status = "CONTROLE MISLUKT" if has_technical_errors(rows) else f"{discrepancy_count(rows)} afwijkingen"
    return f"FV sync NL↔DE – {date or amsterdam_date()} – {status}"


def csv_report(rows):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=COLUMNS)
    writer.writeheader()
    for r in rows:
        # Prevent spreadsheet formula execution from product names/SKUs/values.
        writer.writerow({k: "'" + v if v.lstrip().startswith(("=", "+", "-", "@")) else v
                         for k, v in r.items()})
    return out.getvalue()


def html_report(rows):
    if not discrepancy_count(rows) and not has_technical_errors(rows):
        return "<html><body><p>alles gelijk</p></body></html>"
    def table(items):
        headers = ["SKU", "Productnaam", "Veld", "Waarde NL", "Waarde DE", "Bewerken NL", "Bewerken DE", "Reden"]
        result = "<table border='1' cellpadding='6' cellspacing='0'><tr>" + "".join("<th>" + h + "</th>" for h in headers) + "</tr>"
        for r in items:
            result += "<tr>"
            for k in COLUMNS[1:]:
                v = html.escape(r[k], quote=True)
                result += f'<td><a href="{v}">bewerken</a></td>' if k.startswith("bewerken") and v else f"<td>{v}</td>"
            result += "</tr>"
        return result + "</table>"
    differences = [r for r in rows if r["sectie"] == "afwijkingen" and r["veld"] not in TECHNICAL_FIELDS]
    body = table(differences) if differences else ""
    technical = [r for r in rows if r["veld"] in TECHNICAL_FIELDS]
    if technical:
        body += "<h2>Technische fouten</h2>" + table(technical)
    for section in ("zonder SKU", "uitzonderingen"):
        items = [r for r in rows if r["sectie"] == section]
        if items:
            body += "<h2>" + section + "</h2>" + table(items)
    return "<html><body>" + body + "</body></html>"


def amsterdam_date():
    # EU DST rule, no external tzdata dependency on Windows.
    now = datetime.now(timezone.utc)
    def last_sunday(month):
        day = datetime(now.year, month, 31, 1, tzinfo=timezone.utc)
        return day - timedelta(days=(day.weekday() + 1) % 7)
    return (now + timedelta(hours=2 if last_sunday(3) <= now < last_sunday(10) else 1)).date().isoformat()


def message(rows, sender, recipient, date=None):
    date = date or amsterdam_date()
    msg = EmailMessage()
    msg["Subject"] = report_title(rows, date)
    msg["From"], msg["To"] = sender, recipient
    if not discrepancy_count(rows) and not has_technical_errors(rows):
        text = "alles gelijk"
    else:
        sections = []
        for section in ("afwijkingen", "zonder SKU", "Technische fouten", "uitzonderingen"):
            items = [r for r in rows if (r["veld"] in TECHNICAL_FIELDS if section == "Technische fouten"
                     else r["sectie"] == section and r["veld"] not in TECHNICAL_FIELDS)]
            if items:
                sections.append(section + "\n" + "\n".join(
                    f"{r['SKU']} | {r['productnaam']} | {r['veld']} | NL: {r['waarde_NL']} | DE: {r['waarde_DE']} | {r['bewerken_NL']} | {r['bewerken_DE']}" for r in items))
        text = "\n\n".join(sections)
    msg.set_content(text)
    msg.add_alternative(html_report(rows), subtype="html")
    msg.add_attachment(("\ufeff" + csv_report(rows)).encode("utf-8"), maintype="text", subtype="csv",
                       filename=f"fv-sync-{date}.csv")
    return msg


def required(name):
    value = os.environ.get(name, "")
    if not value.strip():
        raise AuditError(f"Omgevingsvariabele {name} ontbreekt")
    return value


def save_report(rows, output_dir):
    """Attempt CSV, local summary and GitHub Summary independently, even on errors."""
    summary = f"## {report_title(rows)}\n\n"
    summary += html_report(rows).removeprefix("<html><body>").removesuffix("</body></html>") + "\n"
    directory = Path(output_dir)
    ok = True
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError:
        LOG.error("Rapportmap maken mislukt")
        ok = False
    for path, content, encoding, mode in (
        (directory / "fv-sync.csv", csv_report(rows), "utf-8-sig", "w"),
        (directory / "summary.md", summary, "utf-8", "w"),
        (os.environ.get("GITHUB_STEP_SUMMARY"), summary, "utf-8", "a"),
    ):
        if not path:
            continue
        try:
            with open(path, mode, encoding=encoding, newline="") as handle:
                handle.write(content)
        except OSError:
            LOG.error("Rapportbestand schrijven mislukt (%s)", "Job Summary" if mode == "a" else "lokaal")
            ok = False
    return ok


def send_mail(rows):
    names = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "MAIL_TO")
    if not all(os.environ.get(name, "").strip() for name in names):
        LOG.info("Mail overgeslagen: SMTP-configuratie/MAIL_TO niet volledig gevuld")
        return
    user, recipient = required("SMTP_USER"), required("MAIL_TO")
    msg = message(rows, user, recipient)
    host, port, password = required("SMTP_HOST"), int(required("SMTP_PORT")), required("SMTP_PASS")
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, context=context, timeout=60) as smtp:
            smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=60) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.ehlo()
            smtp.login(user, password)
            smtp.send_message(msg)
    LOG.info("Rapport verstuurd: %s", report_title(rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exceptions", default="exceptions.csv")
    parser.add_argument("--output-dir", default="outputs")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    rows, shops = [], {}
    try:
        exceptions = exceptions_from(args.exceptions)
    except (AuditError, OSError, TypeError, csv.Error, UnicodeError) as error:
        exceptions = {}
        rows.append(row("data_error", name="Uitzonderingenbestand", nl=str(error)))
    for country in ("NL", "DE"):
        try:
            client = Client(country, required(country + "_URL"), required(country + "_KEY"), required(country + "_SECRET"))
            shops[country] = load_shop(client, country)
            LOG.info("%s: %d records; prijzen inclusief btw=%s; tarief=%s%%", country,
                     len(shops[country].products), shops[country].includes_tax, shops[country].rate)
        except (AuditError, ValueError, KeyError, TypeError) as error:
            # HTTP bodies are logged only via safe_response; never log exception details here.
            LOG.error("%s uitlezen mislukt", country)
            rows.append(row("api_error", name="Shop niet volledig uitgelezen",
                            nl=str(error) if country == "NL" else "",
                            de=str(error) if country == "DE" else ""))
    if len(shops) == 2:
        rows.extend(compare(shops["NL"], shops["DE"], exceptions))
    rows.sort(key=lambda r: (PRIORITY[r["veld"]], r["SKU"], r["veld"]))
    try:
        send_mail(rows)
    except (AuditError, ValueError, OSError, smtplib.SMTPException):
        LOG.error("Mail versturen mislukt; controleer SMTP-secrets en provider (details afgeschermd)")
        rows.insert(0, row("data_error", name="Mail versturen mislukt", nl="Controleer SMTP-configuratie/provider"))
    saved = save_report(rows, args.output_dir)
    if not saved or has_technical_errors(rows):
        return 2
    return 1 if discrepancy_count(rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
