# FV sync NL↔DE

Dagelijkse, uitsluitend lezende controle van de WooCommerce-shops foravida.nl en foravida.de. Python 3.10+; de enige direct gebruikte externe library is `requests`. Mail, CSV, HTML, berekeningen en tests gebruiken de standaardbibliotheek. De workflow gebruikt Python 3.12.

## Installatie en starten

1. Zet de bestanden inclusief `.github/workflows/fv-sync.yml` op de standaardbranch van de GitHub-repository.
2. Maak per shop een WooCommerce REST API-sleutel met **Read** als machtiging, via WooCommerce → Instellingen → Geavanceerd → REST API. Koppel de sleutel aan een gebruiker die producten en privéproducten mag **lezen**. Instellingen lezen is alleen nodig als je `PRICES_INCLUDE_TAX` niet expliciet instelt; toegang tot belastingtarieven is optioneel. De REST API v3 moet op HTTPS bereikbaar zijn.
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
| `NL_PRICES_INCLUDE_TAX` | Nederlandse prijsinvoer: exact `yes` of `no`; heeft voorrang op de instellingen-API |
| `DE_PRICES_INCLUDE_TAX` | Duitse prijsinvoer: exact `yes` of `no`; heeft voorrang op de instellingen-API |
| `NL_VAT_RATE` | Percentage voor Nederlandse netto→bruto-omrekening; standaard `21` als leeg/niet ingesteld |
| `DE_VAT_RATE` | Percentage voor Duitse netto→bruto-omrekening; standaard `19` als leeg/niet ingesteld |
| `SMTP_HOST` | SMTP-server |
| `SMTP_PORT` | `465` voor impliciete TLS, anders een STARTTLS-poort zoals `587` |
| `SMTP_USER` | SMTP-gebruikersnaam; tevens afzenderadres, dus een geldig toegestaan e-mailadres |
| `SMTP_PASS` | SMTP-wachtwoord of app-wachtwoord |
| `MAIL_TO` | Ontvanger; meerdere adressen kommagescheiden |

Alle toegang en afleveradressen komen uit deze secrets. De workflow injecteert ze als omgevingsvariabelen; API-basis-URL's, sleutels en mailconfiguratie worden niet vastgelegd in de code. Alleen de publiek herkenbare contact-URL in de verplichte User-Agent staat vast. Er wordt geen `.env` ingelezen. Gebruik geen secrets in commandoregels, screenshots of commits.

De zes shop-secrets zijn verplicht. Stel bij voorkeur beide `PRICES_INCLUDE_TAX`-waarden expliciet in op basis van WooCommerce: `yes` betekent dat ingevoerde prijzen inclusief btw zijn, `no` dat ze exclusief btw zijn. Een ingestelde waarde heeft voorrang; het instellingen-endpoint wordt dan overgeslagen, zodat een 401/403 daar niet blokkeert. Alleen bij een lege/niet-ingestelde waarde leest het script de instelling uit de API. `NL_VAT_RATE` en `DE_VAT_RATE` zijn optioneel en worden via GitHub Secrets als omgevingsvariabelen doorgegeven. Gebruik een percentage zonder procentteken, met eventueel een decimale punt, bijvoorbeeld `21` of `7.5`. Ongeldige, negatieve of niet-eindige waarden geven een configuratiefout.

**Mail is optioneel.** Alleen als `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` én `MAIL_TO` allemaal niet-leeg zijn, probeert het script te mailen. Een ontbrekende of gedeeltelijke mailconfiguratie wordt gelogd als overgeslagen en is geen technische fout. Een volledig ingevulde maar ongeldige configuratie of een mislukte verzending is wel een technische fout.

De cron is exact `0 5 * * *`: dagelijks 05:00 **UTC**, dus 06:00 Nederlandse wintertijd en 07:00 zomertijd. GitHub kan geplande runs vertragen. De planning werkt vanaf de standaardbranch; controleer bij langdurige inactiviteit of geplande workflows nog ingeschakeld zijn. Handmatig starten kan altijd met `workflow_dispatch`. De workflow voert eerst de tests uit.

Lokaal testen, zonder shop- of mailtoegang:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m unittest discover -s tests -v
.\.venv\Scripts\python -m tests.example_report
```

Linux/macOS: gebruik `.venv/bin/python`. Lokaal echt uitvoeren kan met `python fv_sync.py` nadat de shop-omgevingsvariabelen en eventuele btw-invoerinstellingen via een veilige secretvoorziening zijn gezet. Mailvariabelen en afwijkende btw-percentages zijn optioneel. GitHub Secrets zijn buiten Actions niet automatisch beschikbaar. Er is geen automatische configuratie van je GitHub-account vanuit dit script.

## Werking

Alle WooCommerce-verzoeken zijn `GET /wp-json/wc/v3/...`. Er bestaan geen schrijf-, wijzig-, batch- of verwijderaanroepen. Authenticatie gebruikt HTTP Basic over geverifieerde HTTPS, zonder sleutels in URL's. Redirects worden geweigerd om onbedoeld doorgeven van toegang te voorkomen. Logregels bevatten endpoint, shoplabel en HTTP-status. Bij iedere niet-200-respons wordt ook een fragment van maximaal 300 tekens gelogd, inclusief bij iedere retry. Daardoor zijn Cloudflare-HTML en WooCommerce-JSON herkenbaar. Bekende sleutels en secrets, URL-/JSON-gecodeerde varianten en de Basic-authwaarde worden afgeschermd vóór het inkorten. Nieuwe regels worden als escapes weergegeven. Er worden geen authenticatieheaders gelogd. Bij netwerkfouten zonder respons bestaat geen body om te loggen. SMTP gebruikt geverifieerde TLS; onversleuteld verzenden is uitgesloten.

Elk HTTP-verzoek, inclusief paginering, btw-instellingen, tarieven, variaties en retries, gebruikt exact deze header in plaats van de standaard `python-requests`-identificatie:

```text
User-Agent: ForaVida-SyncCheck/1.0 (+https://www.foravida.nl)
```

Het script leest:

- `settings/tax/woocommerce_prices_include_tax`, alleen zonder expliciete omgevingswaarde;
- `taxes?class=standard`, inclusief paginering, uitsluitend voor een niet-blokkerende controle;
- alle producten met `status=any&context=edit`, inclusief draft en private;
- alle variaties van elk variabel product met `status=any&context=edit`, eveneens gepagineerd.

Elke collectie heeft maximaal 100 records per pagina. De client start maximaal twee verzoeken per seconde, met maximaal vier pogingen bij netwerkfouten, HTTP 429 en HTTP 5xx, exponentiële wachttijd en `Retry-After` (seconden of HTTP-datum, maximaal 120 seconden). Time-outs zijn 10 seconden voor verbinden en 60 seconden voor lezen. De workflow stopt na 120 minuten. Een succesvolle uitlezing is geen transactionele momentopname: voer tijdens de controle bij voorkeur geen grote import uit.

SKU-koppeling is exact en hoofdlettergevoelig. Zowel hoofdproducten als variaties doen mee. Productnamen worden alleen getoond; vertaalde namen worden niet vergeleken. Variaties krijgen de oudernaam plus attributen als naam. Bij een variatie verwijst de bewerklink naar het ouderproduct en het variatiepaneel; het variatie-ID staat in de brondata. De link aan de ontbrekende kant blijft leeg.

Per SKU worden bestaan (`exists`), `status`, `regular_price`, `sale_price`, `stock_status`, `date_on_sale_from`, `date_on_sale_to` en `variation_count` gecontroleerd. Het aantal variaties is het werkelijk uitgelezen aantal per ouderproduct; bij eenvoudige producten en variaties is dit nul. Een dubbele SKU geeft `duplicate_sku`; een willekeurig product kiezen zou onbetrouwbaar zijn. Producten en variaties zonder SKU krijgen elk een regel in **zonder SKU**, met ID en bewerklink. Ze worden niet op naam gekoppeld.

## Prijzen en btw

Het tarief voor de vergelijking komt uitsluitend uit `NL_VAT_RATE` (standaard **21**) en `DE_VAT_RATE` (standaard **19**). Lege waarden gebruiken ook deze standaardpercentages. Er wordt nooit een percentage uit `/taxes` voor de berekening gebruikt.

Staan `NL_PRICES_INCLUDE_TAX` en `DE_PRICES_INCLUDE_TAX` beide op **`yes`**, dan worden `regular_price` en `sale_price` rechtstreeks als brutobedragen vergeleken, zonder omrekening. Dit werkt ook voor producten met een afwijkende belastingklasse, omdat geen btw hoeft te worden berekend. Alleen bij een shop met prijsinvoer **`no`** worden belastbare bedragen omgerekend: `bruto = ingevoerde prijs × (1 + ingesteld percentage / 100)`. Bij een mix van yes/no wordt dus alleen de exclusieve kant omgerekend; bij no/no ieder met het eigen percentage. Niet-belastbare producten (`tax_status=none` of `shipping`) krijgen geen product-btw toegevoegd. Voor variaties wordt belastingklasse `parent` opgelost naar de ouder.

Bedragen worden met `Decimal` op eurocenten afgerond (`ROUND_HALF_UP`); pas bij een verschil **groter dan €0,01** volgt een melding. Lege prijs en nul zijn verschillend. Zowel gewone prijs als actieprijs wordt gecontroleerd, ook buiten de actieperiode. De vergelijking veronderstelt dat beide shops europrijzen leveren.

`/taxes` wordt alleen als controle uitgelezen. Geen, meerdere, lokale of samengestelde standaardtarieven geven een **waarschuwing in de log**; de vergelijking gaat door met de geconfigureerde percentages. Ook een onbereikbaar of niet-toegankelijk `/taxes`-endpoint of een ongeldige respons blokkeert niet. Deze waarschuwingen leveren geen `api_error`/`data_error` op, tellen niet mee en veroorzaken geen titel `CONTROLE MISLUKT` of exitcode 2.

Als voor een belastbaar product met een afwijkende belastingklasse een netto→bruto-omrekening nodig is, blijft `data_error` van toepassing: daarvoor is aanvullende belastinglogica nodig. Bij reeds inclusieve bedragen is de belastingklasse geen blokkade. Sale-datums worden waar beschikbaar via de corresponderende `_gmt`-velden als UTC vergeleken; anders via het lokale ISO-veld. Gelijke lokale tijden veronderstellen dan gelijke shoptijdzones.

**Toegang tot instellingen:** een Read-sleutel geeft niet automatisch de WordPress-gebruiker de bevoegdheid om instellingen en tarieven te bekijken. Met expliciete `PRICES_INCLUDE_TAX`-waarden is toegang tot het instellingen-endpoint niet nodig. Als zo'n waarde ontbreekt en het endpoint 401/403 teruggeeft, meldt het script welke omgevingsvariabele moet worden ingesteld. Andere fouten op dat benodigde instellingen-endpoint blijven technisch; `/taxes` is in alle gevallen niet-blokkerend. Geef de sleutel geen schrijfrechten.

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

Onderwerp en titel in de Job Summary: `FV sync NL↔DE – [datum] – [n] afwijkingen`, met de datum in Europe/Amsterdam. `n` telt catalogusafwijkingsregels, geen unieke SKU's; afzonderlijke meldingen zonder SKU tellen mee. **`api_error` en `data_error` tellen nooit als afwijking.**

Bij technische fouten, waaronder een shop die niet volledig kan worden uitgelezen, luidt de titel **`FV sync NL↔DE – [datum] – CONTROLE MISLUKT`**. Deze titel geldt ook als daarnaast catalogusverschillen zijn gevonden. Technische meldingen staan afzonderlijk onder **Technische fouten** in de HTML-mail, de platte tekstmail en de Job Summary. In de CSV krijgen ze de sectie `Technische fouten`; ze blijven dus beschikbaar in het artifact. Dezelfde afhandeling geldt voor onbetrouwbare prijsgegevens of een ongeldige configuratie (`data_error`).

Het rapport begint met een tabel, gesorteerd op prijs, ontbrekend product, status/voorraad, actieperiode en aantal variaties. Daarna volgen producten zonder SKU en bewuste uitzonderingen. De tabel toont SKU, productnaam, veld, waarden NL/DE en beide beschikbare wp-admin-links. De CSV bevat **alle rapportregels**, inclusief uitzonderingen en fouten; UTF-8 met BOM voor Excel. Mogelijke spreadsheetformules worden als tekst beveiligd. Een volledig identieke catalogus levert een header-only CSV op. De optionele e-mail bevat dezelfde tabel, een platte tekstversie en de CSV als bijlage.

Iedere run schrijft `outputs/fv-sync.csv` en `outputs/summary.md`, ook bij API- of SMTP-fouten. Met `--output-dir PAD` kies je lokaal een andere rapportmap. Binnen GitHub Actions wordt het rapport daarnaast altijd toegevoegd aan het bestand uit `$GITHUB_STEP_SUMMARY`, zodat het in de **Job Summary** staat. De workflow uploadt de CSV met `if: always()` als artifact **fv-sync-csv**, ook als het script met exitcode 1 of 2 eindigt. Bewaartermijn: 14 dagen. De Job Summary en het artifact volgen de toegangsrechten van de repository. Buiten Actions blijft de lokale summary beschikbaar. Een schrijffout is een technische fout; de andere rapportbestanden worden dan nog steeds geprobeerd. Bij een afgebroken job of infrastructuurfout voordat het script draait kan geen rapport worden gegarandeerd.

Alleen bij nul niet-uitgezonderde afwijkingen **én zonder technische fouten** bevat de rapportinhoud precies de regel **alles gelijk**; eventuele bewuste verschillen staan dan alleen in de CSV. Bij een API-probleem bevat het rapport endpoint en HTTP-status. Bij DNS/TLS/time-out bestaat geen HTTP-status en staat er expliciet `geen HTTP-respons`. Een onvolledige shop wordt niet als lege catalogus vergeleken. Technische fouten krijgen een aparte sectie na de tabel met eventuele catalogusverschillen. Een SMTP-storing wordt ook in Summary en CSV vermeld.

Exitcodes: **`0` = alles gelijk**, **`1` = afwijkingen gevonden**, **`2` = technische fout**. Technische fouten (benodigde API-data, ongeldige gegevens/configuratie, SMTP of schrijven van rapportbestanden) hebben voorrang op catalogusverschillen. Bewuste uitzonderingen tellen niet mee. Ontbrekende en dubbele SKU's tellen als catalogusafwijking. Zowel 1 als 2 maakt de Actions-stap rood; het CSV-artifact wordt daarna alsnog geüpload. Overgeslagen mail en waarschuwingen over `/taxes` veranderen de exitcode niet.

## Tests en voorbeeld

`tests/fixtures/nl.json` en `tests/fixtures/de.json` zijn twee vaste, volledig fictieve datasets, met nep-URL's. De tests bewijzen alle gevraagde typen afwijkingen, inclusief variaties, bruto-omrekening, tolerantie, beide richtingen van ontbrekende producten, private/draft, lege actieprijs versus nul, ontbrekende/dubbele SKU, uitzonderingen, volgorde, e-mailbijlage en bewerklinks. Gesimuleerde HTTP-antwoorden controleren paginering, alleen GET, rate limiting, retries en API-foutmail zonder onterechte ontbrekende producten. Aanvullende tests dekken expliciete btw-invoerinstellingen, API-uitlezing zonder expliciete waarde, vaste/configureerbare btw-percentages, alle vier yes/no-combinaties voor beide prijsvelden en doorgaan met vergelijken bij onduidelijke of onbereikbare `/taxes`-gegevens. Ook veilige HTML-/JSON-foutlogging, optionele mail, alle drie exitcodes en rapportbestanden bij technische fouten worden getest. Er worden tijdens tests geen echte shops of SMTP-servers benaderd.

De tests controleren ook de exacte User-Agent bij paginering en retries, de titel `CONTROLE MISLUKT` in mail en Job Summary, de aparte sectie voor beide typen technische fouten en de afwijkingenteller bij gemengde catalogus- en technische meldingen.

`python -m tests.example_report` maakt `outputs/voorbeeldrapport.html`, `.csv` en `.eml` uit precies die datasets. Het voorbeeld bevat 15 niet-uitgezonderde afwijkingsregels plus één bewuste uitzondering. Open de HTML in een browser of de EML in een mailprogramma.

API-referenties: [producten](https://developer.woocommerce.com/docs/apis/rest-api/v3/products/), [variaties](https://developer.woocommerce.com/docs/apis/rest-api/v3/product-variations/), [instellingen](https://developer.woocommerce.com/docs/apis/rest-api/v3/settings/), [btw-tarieven](https://developer.woocommerce.com/docs/apis/rest-api/v3/taxes/).
