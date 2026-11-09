# FV sync NL↔DE

Dagelijkse, uitsluitend lezende controle van de WooCommerce-shops foravida.nl en foravida.de. Python 3.10+; de enige direct gebruikte externe library is `requests`. Mail, CSV, HTML, berekeningen en tests gebruiken de standaardbibliotheek. De workflow gebruikt Python 3.12.

## Installatie en starten

1. Zet de bestanden inclusief `.github/workflows/fv-sync.yml` op de standaardbranch van de GitHub-repository.
2. Maak per shop een WooCommerce REST API-sleutel met **Read** als machtiging, via WooCommerce → Instellingen → Geavanceerd → REST API. Koppel de sleutel aan een gebruiker die producten, privéproducten, instellingen en belastingtarieven mag **lezen**. De REST API v3 moet op HTTPS bereikbaar zijn.
3. Voeg onderstaande repository secrets toe via GitHub → Settings → Secrets and variables → Actions → New repository secret.
4. Schakel GitHub Actions in. Start de eerste controle via Actions → FV sync NL-DE → Run workflow. Controleer de ontvangen e-mail en de log.

| Secret | Inhoud |
| --- | --- |
| `NL_URL` | HTTPS-basis-URL van de Nederlandse WordPress-installatie, zonder `/wp-admin` |
| `NL_KEY` | Nederlandse consumer key, alleen lezen |
| `NL_SECRET` | Nederlandse consumer secret |
| `DE_URL` | HTTPS-basis-URL van de Duitse WordPress-installatie |
| `DE_KEY` | Duitse consumer key, alleen lezen |
| `DE_SECRET` | Duitse consumer secret |
| `SMTP_HOST` | SMTP-server |
| `SMTP_PORT` | `465` voor impliciete TLS, anders een STARTTLS-poort zoals `587` |
| `SMTP_USER` | SMTP-gebruikersnaam; tevens afzenderadres, dus een geldig toegestaan e-mailadres |
| `SMTP_PASS` | SMTP-wachtwoord of app-wachtwoord |
| `MAIL_TO` | Ontvanger; meerdere adressen kommagescheiden |

Alle toegang en afleveradressen komen uit deze secrets. De workflow injecteert ze als omgevingsvariabelen; het script bevat geen productie-URL's, sleutels of mailconfiguratie. Er wordt geen `.env` ingelezen. Gebruik geen secrets in commandoregels, screenshots of commits.

De cron is exact `0 5 * * *`: dagelijks 05:00 **UTC**, dus 06:00 Nederlandse wintertijd en 07:00 zomertijd. GitHub kan geplande runs vertragen. De planning werkt vanaf de standaardbranch; controleer bij langdurige inactiviteit of geplande workflows nog ingeschakeld zijn. Handmatig starten kan altijd met `workflow_dispatch`. De workflow voert eerst de tests uit.

Lokaal testen, zonder shop- of mailtoegang:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m tests.example_report
```

Linux/macOS: gebruik `.venv/bin/python`. Lokaal echt uitvoeren kan met `python fv_sync.py` nadat dezelfde elf omgevingsvariabelen via een veilige secretvoorziening zijn gezet. GitHub Secrets zijn buiten Actions niet automatisch beschikbaar. Er is geen automatische configuratie van je GitHub-account vanuit dit script.

## Werking

Alle WooCommerce-verzoeken zijn `GET /wp-json/wc/v3/...`. Er bestaan geen schrijf-, wijzig-, batch- of verwijderaanroepen. Authenticatie gebruikt HTTP Basic over geverifieerde HTTPS, zonder sleutels in URL's. Redirects worden geweigerd om onbedoeld doorgeven van toegang te voorkomen. Logregels bevatten endpoint, shoplabel en HTTP-status, nooit authenticatie of responsebody. SMTP gebruikt geverifieerde TLS; onversleuteld verzenden is uitgesloten.

Het script leest:

- `settings/tax/woocommerce_prices_include_tax`;
- `taxes?class=standard`, inclusief paginering;
- alle producten met `status=any&context=edit`, inclusief draft en private;
- alle variaties van elk variabel product met `status=any&context=edit`, eveneens gepagineerd.

Elke collectie heeft maximaal 100 records per pagina. De client start maximaal twee verzoeken per seconde, met maximaal vier pogingen bij netwerkfouten, HTTP 429 en HTTP 5xx, exponentiële wachttijd en `Retry-After` (seconden of HTTP-datum, maximaal 120 seconden). Time-outs zijn 10 seconden voor verbinden en 60 seconden voor lezen. De workflow stopt na 120 minuten. Een succesvolle uitlezing is geen transactionele momentopname: voer tijdens de controle bij voorkeur geen grote import uit.

SKU-koppeling is exact en hoofdlettergevoelig. Zowel hoofdproducten als variaties doen mee. Productnamen worden alleen getoond; vertaalde namen worden niet vergeleken. Variaties krijgen de oudernaam plus attributen als naam. Bij een variatie verwijst de bewerklink naar het ouderproduct en het variatiepaneel; het variatie-ID staat in de brondata. De link aan de ontbrekende kant blijft leeg.

Per SKU worden bestaan (`exists`), `status`, `regular_price`, `sale_price`, `stock_status`, `date_on_sale_from`, `date_on_sale_to` en `variation_count` gecontroleerd. Het aantal variaties is het werkelijk uitgelezen aantal per ouderproduct; bij eenvoudige producten en variaties is dit nul. Een dubbele SKU geeft `duplicate_sku`; een willekeurig product kiezen zou onbetrouwbaar zijn. Producten en variaties zonder SKU krijgen elk een regel in **zonder SKU**, met ID en bewerklink. Ze worden niet op naam gekoppeld.

## Prijzen en btw

Het standaardtarief komt daadwerkelijk uit de API: normaal NL 21% en DE 19%, zonder hardcoded terugval. Bij `woocommerce_prices_include_tax=no` geldt voor belastbare producten: `bruto = ingevoerde prijs × (1 + tarief / 100)`. Bij `yes` blijft de ingevoerde prijs staan. Niet-belastbare producten (`tax_status=none` of `shipping`) krijgen geen product-btw toegevoegd. Voor variaties wordt belastingklasse `parent` opgelost naar de ouder.

Bedragen worden met `Decimal` op eurocenten afgerond (`ROUND_HALF_UP`); pas bij een verschil **groter dan €0,01** volgt een melding. Lege prijs en nul zijn verschillend. Zowel gewone prijs als actieprijs wordt gecontroleerd, ook buiten de actieperiode. De vergelijking veronderstelt dat beide shops europrijzen leveren.

Het script accepteert alleen één eenduidig landelijk standaardtarief per land. Lokale, samengestelde of meerdere standaardtarieven geven een expliciete fout in plaats van een gok. Een product met een afwijkende belastingklasse en ingevulde prijs geeft `data_error`; hiervoor is aanvullende belastinglogica nodig. De huidige opdracht betreft het standaardtarief. Sale-datums worden waar beschikbaar via de corresponderende `_gmt`-velden als UTC vergeleken; anders via het lokale ISO-veld. Gelijke lokale tijden veronderstellen dan gelijke shoptijdzones.

**Toegang tot instellingen:** een Read-sleutel geeft niet automatisch de WordPress-gebruiker de bevoegdheid om instellingen en tarieven te bekijken. Als die GET-endpoints HTTP 401/403 geven, verschijnt dit in de mail; prijzen worden dan niet met aangenomen btw vergeleken. Geef de sleutel geen schrijfrechten. Controleer de leesbevoegdheid van de gekoppelde gebruiker of laat een beheerder de REST-toegang tot deze leesgegevens regelen.

## Bewuste uitzonderingen

Pas `exceptions.csv` aan; UTF-8 en komma als scheidingsteken, met exact deze header:

```csv
SKU,veld,reden
VOORBEELD-SKU,regular_price,Bewuste lokale prijsafspraak
VOORBEELD-SKU,date_on_sale_to,Duitse actie loopt langer
```

Toegestane velden: `exists`, `status`, `regular_price`, `sale_price`, `stock_status`, `date_on_sale_from`, `date_on_sale_to`, `variation_count`. Gebruik één regel per combinatie SKU/veld, een niet-lege reden en de exacte SKU. Zet tekst met een komma tussen dubbele aanhalingstekens. Er zijn geen wildcards. Een uitzondering geldt alleen voor dat veld, ook bij variaties. Een fout, dubbele regel of onbekend veld meldt het script; het verbergt dan geen verschillen met het ongeldige bestand.

Onderdrukte verschillen blijven zichtbaar in de sectie **uitzonderingen** en in de CSV met hun reden, maar tellen niet mee in het onderwerp. Een uitzondering zonder actueel verschil levert geen rapportregel op. Technische fouten, ontbrekende SKU's en dubbele SKU's kunnen niet worden onderdrukt.

## Rapport en fouten

Onderwerp: `FV sync NL↔DE – [datum] – [n] afwijkingen`, met de datum in Europe/Amsterdam. `n` telt afwijkingsregels, geen unieke SKU's; afzonderlijke meldingen zonder SKU en technische fouten tellen mee.

De HTML-mail begint met een tabel, gesorteerd op prijs, ontbrekend product, status/voorraad, actieperiode en aantal variaties. Daarna volgen producten zonder SKU en bewuste uitzonderingen. De tabel toont SKU, productnaam, veld, waarden NL/DE en beide beschikbare wp-admin-links. Een CSV-bijlage bevat **alle rapportregels**, inclusief uitzonderingen en fouten; UTF-8 met BOM voor Excel. Mogelijke spreadsheetformules worden als tekst beveiligd. Een volledig identieke catalogus levert een header-only CSV op. De mail heeft ook een platte tekstversie.

Bij nul niet-uitgezonderde afwijkingen bevat de mail precies de regel **alles gelijk**; eventuele bewuste verschillen staan dan alleen in de bijlage. Bij een API-probleem bevat de mail endpoint en HTTP-status. Bij DNS/TLS/time-out bestaat geen HTTP-status en staat er expliciet `geen HTTP-respons`. Een onvolledige shop wordt niet als lege catalogus vergeleken. API-fouten staan bovenaan. Bij ontbrekende SMTP-configuratie of een SMTP-storing kan geen mail uitgaan: de Actions-run wordt rood en de log vermeldt een afgeschermde fout.

Exitcodes: `0` = controle en mail gelukt (ook met catalogusverschillen); `1` = rapport verstuurd met API- of gegevensfout; `2` = mail mislukt. Er worden geen rapporten met shopdata naar openbare Actions-artifacts geüpload.

## Tests en voorbeeld

`tests/fixtures/nl.json` en `tests/fixtures/de.json` zijn twee vaste, volledig fictieve datasets, met nep-URL's. De tests bewijzen alle gevraagde typen afwijkingen, inclusief variaties, bruto-omrekening, tolerantie, beide richtingen van ontbrekende producten, private/draft, lege actieprijs versus nul, ontbrekende/dubbele SKU, uitzonderingen, volgorde, e-mailbijlage en bewerklinks. Gesimuleerde HTTP-antwoorden controleren paginering, alleen GET, rate limiting, retries en API-foutmail zonder onterechte ontbrekende producten. Er worden tijdens tests geen echte shops of SMTP-servers benaderd.

`python -m tests.example_report` maakt `outputs/voorbeeldrapport.html`, `.csv` en `.eml` uit precies die datasets. Het voorbeeld bevat 15 niet-uitgezonderde afwijkingsregels plus één bewuste uitzondering. Open de HTML in een browser of de EML in een mailprogramma.

API-referenties: [producten](https://developer.woocommerce.com/docs/apis/rest-api/v3/products/), [variaties](https://developer.woocommerce.com/docs/apis/rest-api/v3/product-variations/), [instellingen](https://developer.woocommerce.com/docs/apis/rest-api/v3/settings/), [btw-tarieven](https://developer.woocommerce.com/docs/apis/rest-api/v3/taxes/).
