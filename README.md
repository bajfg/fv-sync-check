# FV sync NL↔DE

Dagelijkse, uitsluitend lezende controle van de WooCommerce-shops foravida.nl en foravida.de. Python 3.10+; de enige direct gebruikte externe library is `requests`. Mail, CSV, HTML, berekeningen en tests gebruiken de standaardbibliotheek. De workflow gebruikt Python 3.12.

## Installatie en starten

1. Zet de bestanden inclusief `.github/workflows/fv-sync.yml` op de standaardbranch van de GitHub-repository.
2. Maak per shop een WooCommerce REST API-sleutel met **Read** als machtiging, via WooCommerce → Instellingen → Geavanceerd → REST API. Koppel de sleutel aan een gebruiker die producten, privéproducten, instellingen en belastingtarieven mag **lezen**. De REST API v3 moet op HTTPS bereikbaar zijn.
3. Voeg onderstaande repository secrets toe via GitHub → Settings → Secrets and variables → Actions → New repository secret.
4. Schakel GitHub Actions in. Start de eerste controle via Actions → FV sync NL-DE → Run workflow. Bekijk de Job Summary en download het artifact `fv-sync-csv`. Als mail is ingesteld, ontvang je ook een e-mail.

| Secret | Inhoud |
| --- | --- |
| `NL_URL` | HTTPS-basis-URL van de Nederlandse WordPress-installatie, zonder `/wp-admin` |
| `NL_KEY` | Nederlandse consumer key, alleen lezen |
| `NL_SECRET` | Nederlandse consumer secret |
| `DE_URL` | HTTPS-basis-URL van de Duitse WordPress-installatie |
| `DE_KEY` | Duitse consumer key, alleen lezen |
| `DE_SECRET` | Duitse consumer secret |
| `NL_PRICES_INCLUDE_TAX` | Terugval voor Nederlandse btw-instelling: exact `yes` of `no`; alleen gebruikt bij HTTP 401/403 op `settings/tax/woocommerce_prices_include_tax` |
| `DE_PRICES_INCLUDE_TAX` | Dezelfde terugval voor Duitsland: exact `yes` of `no` |
| `SMTP_HOST` | SMTP-server |
| `SMTP_PORT` | `465` voor impliciete TLS, anders een STARTTLS-poort zoals `587` |
| `SMTP_USER` | SMTP-gebruikersnaam; tevens afzenderadres, dus een geldig toegestaan e-mailadres |
| `SMTP_PASS` | SMTP-wachtwoord of app-wachtwoord |
| `MAIL_TO` | Ontvanger; meerdere adressen kommagescheiden |

Alle toegang en afleveradressen komen uit deze secrets. De workflow injecteert ze als omgevingsvariabelen; het script bevat geen productie-URL's, sleutels of mailconfiguratie. Er wordt geen `.env` ingelezen. Gebruik geen secrets in commandoregels, screenshots of commits.

De zes shop-secrets zijn verplicht. De twee btw-terugvalwaarden zijn alleen nodig wanneer de instellingen-API 401/403 teruggeeft. Controleer de waarde in WooCommerce: `yes` betekent dat ingevoerde prijzen inclusief btw zijn, `no` dat ze exclusief btw zijn. De API-waarde heeft voorrang als uitlezen lukt.

**Mail is optioneel.** Alleen als `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` én `MAIL_TO` allemaal niet-leeg zijn, probeert het script te mailen. Een ontbrekende of gedeeltelijke mailconfiguratie wordt gelogd als overgeslagen en is geen technische fout. Een volledig ingevulde maar ongeldige configuratie of een mislukte verzending is wel een technische fout.

De cron is exact `0 5 * * *`: dagelijks 05:00 **UTC**, dus 06:00 Nederlandse wintertijd en 07:00 zomertijd. GitHub kan geplande runs vertragen. De planning werkt vanaf de standaardbranch; controleer bij langdurige inactiviteit of geplande workflows nog ingeschakeld zijn. Handmatig starten kan altijd met `workflow_dispatch`. De workflow voert eerst de tests uit.

Lokaal testen, zonder shop- of mailtoegang:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m tests.example_report
```

Linux/macOS: gebruik `.venv/bin/python`. Lokaal echt uitvoeren kan met `python fv_sync.py` nadat de shop-omgevingsvariabelen en eventuele btw-terugvalwaarden via een veilige secretvoorziening zijn gezet. Mailvariabelen zijn optioneel. GitHub Secrets zijn buiten Actions niet automatisch beschikbaar. Er is geen automatische configuratie van je GitHub-account vanuit dit script.

## Werking

Alle WooCommerce-verzoeken zijn `GET /wp-json/wc/v3/...`. Er bestaan geen schrijf-, wijzig-, batch- of verwijderaanroepen. Authenticatie gebruikt HTTP Basic over geverifieerde HTTPS, zonder sleutels in URL's. Redirects worden geweigerd om onbedoeld doorgeven van toegang te voorkomen. Logregels bevatten endpoint, shoplabel en HTTP-status. Bij iedere niet-200-respons wordt ook een fragment van maximaal 300 tekens gelogd, inclusief bij iedere retry. Daardoor zijn Cloudflare-HTML en WooCommerce-JSON herkenbaar. Bekende sleutels en secrets, URL-/JSON-gecodeerde varianten en de Basic-authwaarde worden afgeschermd vóór het inkorten. Nieuwe regels worden als escapes weergegeven. Er worden geen authenticatieheaders gelogd. Bij netwerkfouten zonder respons bestaat geen body om te loggen. SMTP gebruikt geverifieerde TLS; onversleuteld verzenden is uitgesloten.

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

**Toegang tot instellingen:** een Read-sleutel geeft niet automatisch de WordPress-gebruiker de bevoegdheid om instellingen en tarieven te bekijken. Bij HTTP 401/403 op de btw-instelling gebruikt het script `NL_PRICES_INCLUDE_TAX` respectievelijk `DE_PRICES_INCLUDE_TAX` en gaat verder met tarieven en producten. Deze succesvolle terugval geeft een waarschuwing in de log en is geen technische fout of catalogusafwijking. Ontbreekt een geldige `yes/no`-waarde, dan volgt een expliciete technische melding. Andere HTTP-fouten en ongeldige API-inhoud activeren de terugval niet. Het standaardtarief moet nog steeds via `taxes` leesbaar zijn; voor dit endpoint is er geen terugval. Geef de sleutel geen schrijfrechten.

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

Het rapport begint met een tabel, gesorteerd op prijs, ontbrekend product, status/voorraad, actieperiode en aantal variaties. Daarna volgen producten zonder SKU en bewuste uitzonderingen. De tabel toont SKU, productnaam, veld, waarden NL/DE en beide beschikbare wp-admin-links. De CSV bevat **alle rapportregels**, inclusief uitzonderingen en fouten; UTF-8 met BOM voor Excel. Mogelijke spreadsheetformules worden als tekst beveiligd. Een volledig identieke catalogus levert een header-only CSV op. De optionele e-mail bevat dezelfde tabel, een platte tekstversie en de CSV als bijlage.

Iedere run schrijft `outputs/fv-sync.csv` en `outputs/summary.md`, ook bij API- of SMTP-fouten. Met `--output-dir PAD` kies je lokaal een andere rapportmap. Binnen GitHub Actions wordt het rapport daarnaast altijd toegevoegd aan het bestand uit `$GITHUB_STEP_SUMMARY`, zodat het in de **Job Summary** staat. De workflow uploadt de CSV met `if: always()` als artifact **fv-sync-csv**, ook als het script met exitcode 1 of 2 eindigt. Bewaartermijn: 14 dagen. De Job Summary en het artifact volgen de toegangsrechten van de repository. Buiten Actions blijft de lokale summary beschikbaar. Een schrijffout is een technische fout; de andere rapportbestanden worden dan nog steeds geprobeerd. Bij een afgebroken job of infrastructuurfout voordat het script draait kan geen rapport worden gegarandeerd.

Bij nul niet-uitgezonderde afwijkingen bevat de rapportinhoud precies de regel **alles gelijk**; eventuele bewuste verschillen staan dan alleen in de CSV. Bij een API-probleem bevat het rapport endpoint en HTTP-status. Bij DNS/TLS/time-out bestaat geen HTTP-status en staat er expliciet `geen HTTP-respons`. Een onvolledige shop wordt niet als lege catalogus vergeleken. API-fouten staan bovenaan. Een SMTP-storing wordt ook in Summary en CSV vermeld.

Exitcodes: **`0` = alles gelijk**, **`1` = afwijkingen gevonden**, **`2` = technische fout**. Technische fouten (API, ongeldige gegevens/configuratie, SMTP of schrijven van rapportbestanden) hebben voorrang op catalogusverschillen. Bewuste uitzonderingen tellen niet mee. Ontbrekende en dubbele SKU's tellen als catalogusafwijking. Zowel 1 als 2 maakt de Actions-stap rood; het CSV-artifact wordt daarna alsnog geüpload. Overgeslagen mail en een geslaagde btw-terugval veranderen de exitcode niet.

## Tests en voorbeeld

`tests/fixtures/nl.json` en `tests/fixtures/de.json` zijn twee vaste, volledig fictieve datasets, met nep-URL's. De tests bewijzen alle gevraagde typen afwijkingen, inclusief variaties, bruto-omrekening, tolerantie, beide richtingen van ontbrekende producten, private/draft, lege actieprijs versus nul, ontbrekende/dubbele SKU, uitzonderingen, volgorde, e-mailbijlage en bewerklinks. Gesimuleerde HTTP-antwoorden controleren paginering, alleen GET, rate limiting, retries en API-foutmail zonder onterechte ontbrekende producten. Aanvullende tests dekken btw-terugval bij 401/403 voor beide landen en yes/no, API-voorrang, ongeldige terugvalwaarden, veilige HTML-/JSON-foutlogging, optionele mail, alle drie exitcodes en rapportbestanden bij technische fouten. Er worden tijdens tests geen echte shops of SMTP-servers benaderd.

`python -m tests.example_report` maakt `outputs/voorbeeldrapport.html`, `.csv` en `.eml` uit precies die datasets. Het voorbeeld bevat 15 niet-uitgezonderde afwijkingsregels plus één bewuste uitzondering. Open de HTML in een browser of de EML in een mailprogramma.

API-referenties: [producten](https://developer.woocommerce.com/docs/apis/rest-api/v3/products/), [variaties](https://developer.woocommerce.com/docs/apis/rest-api/v3/product-variations/), [instellingen](https://developer.woocommerce.com/docs/apis/rest-api/v3/settings/), [btw-tarieven](https://developer.woocommerce.com/docs/apis/rest-api/v3/taxes/).
