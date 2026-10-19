[README.md](https://github.com/user-attachments/files/33673803/README.md)
# HOME HUNTER AI 4.0 — Trójmiasto

**Działający prototyp kontrolera ofert, nie scraper OLX ani gotowy agent wyszukujący nowe ogłoszenia.**

Kryteria: Gdańsk / Gdynia / Sopot, zakup inwestycyjny, min. 35 m², **750 000 zł łącznie z dodatkowymi kosztami**, preferowany właściciel albo prowizja 0 zł. Zakładamy model najmu całorocznego bez kredytu — kalkulator kredytowy do późniejszego wdrożenia.

## Co jest zaimplementowane

- CSV → trwała baza SQLite z historią zaimportowanych zmian cen.
- Automatyczna kontrola znanych linków, uwzględniająca HTTP 404/410, 403/429, przekierowania, niezgodny ID oraz widoczne parametry ogłoszenia.
- Rozpoznawanie prawdopodobnych duplikatów, bez automatycznego łączenia mieszkań z tego samego budynku.
- Kalkulator: pełny nakład, przychód przy 11 miesiącach najmu, ryczałt, koszty właściciela, rentowność i cena graniczna przy celu 4%.
- Raport Markdown podzielony na oferty z dopasowaną stroną, niezweryfikowane i uszkodzone linki.
- 19 testów regresyjnych; GitHub Actions do testów i powtarzania kontroli.

## Bardzo ważne ograniczenia

1. **Brak zbierania nowych ofert z portali.** Program monitoruje tylko linki, które dodasz do CSV (ręcznie lub z dozwolonego źródła ogłoszeń/alertów). Do zbierania nowych ogłoszeń potrzebujemy integracji zgodnej z regulaminem serwisu lub importu alertów.
2. `online_matched` oznacza jedynie, że treść publicznej strony wygląda na wskazane mieszkanie. **Nie potwierdza**, że sprzedający może je nadal sprzedać.
3. Blokada antybotowa, 403/429, brak możliwości pobrania treści, CAPTCHA lub roboty nie są traktowane jako usunięcie mieszkania.
4. Portale mają strony dynamiczne; wtedy status będzie `needs_manual_review`, nie automatyczne „aktywne”.
5. Rentowności są **wyłącznie modelowe**. W historycznej watchliście przychody i koszty są wcześniejszymi, niezweryfikowanymi założeniami. Nie tworzymy oceny 0–100 bez dokumentów i grup porównawczych.
6. Używaj programu tylko w granicach dozwolonego dostępu; bez obchodzenia CAPTCHA, uwierzytelniania ani blokad serwisów. Nie sprawdzaj masowo ogłoszeń przy braku zgody źródła.
7. Kod ma podstawowe walidacje adresów, ale nie jest publiczną usługą do pobierania arbitralnych URL-i — nie wystawiaj go jako otwartego API.

## Lokalny start (Python 3.12+, bez zewnętrznych bibliotek)

```bash
python -m unittest discover -s tests -v
python -m home_hunter --db data/home_hunter.sqlite3 init
python -m home_hunter --db data/home_hunter.sqlite3 import-csv config/watchlist.csv
python -m home_hunter --db data/home_hunter.sqlite3 dedupe
python -m home_hunter --db data/home_hunter.sqlite3 check --limit 40
python -m home_hunter --db data/home_hunter.sqlite3 report --out reports/latest.md
```

`check` wykonuje połączenia z portalami **tylko gdy reguły robots.txt zezwalają na pobranie**, ale do prawidłowej pracy lokalnej nie jest potrzebny przy `init`, `import-csv`, `dedupe` ani `report`.

W pliku `config/watchlist.csv` znajdziesz kilka ofert z poprzedniej rozmowy. **Są to wpisy historyczne: ich aktualność i prowizje nie zostały przez ten projekt zweryfikowane.** Możesz je usunąć lub zastąpić nowymi.

### Format CSV

Wymagane: `source`, `listing_id`, `url`, `city`, `street`, `price_pln`, `area_m2`, `rooms`.

Dodatkowe: `floor`, `district`, `house_no`, `seller_type` (`owner`, `agency`, `unknown`), `buyer_fee_status` (`confirmed_zero`, `amount`, `unknown`), `buyer_fee_pln`, `obligatory_extras_pln`, `preparation_pln`, `estimated_rent_pln`, `rent_comps_count`, `yearly_owner_cost_pln`, `photo_fingerprint`, `notes`.

**Nie ustawiaj `confirmed_zero` tylko dlatego, że ogłoszenie napisano „bez pośredników”.** Potwierdzenie prowizji powinno być wprost uzyskane od sprzedającego lub biura. Dane o kosztach i czynszu muszą mieć źródło; tutaj są wejściowymi szacunkami analityka.

## Uruchomienie GitHub Actions

1. Utwórz **NOWE prywatne repozytorium** `home-hunter-ai` (nie korzystaj z `pearl-hunter-sec`).
2. Przenieś zawartość ZIP do repozytorium; w GitHub wybierz **Actions → Home Hunter Monitor → Run workflow** na początek testowo.
3. Workflow sam odpali testy, zaimportuje obserwowane linki, spróbuje sprawdzić ich strony i zapisze historię do bazy SQLite w prywatnym repo. Wymaga uprawnienia **Actions: read and write permissions** (lub reguły pozwalającej `contents: write`) i możliwości push do gałęzi.
4. Włączenie cron uruchamia kontrole o `06:15` i `16:15 UTC` (czas w Polsce zmienia się z CET na CEST). GitHub scheduled workflows są best effort i mogą się opóźniać lub nie uruchomić w danym terminie.
5. Monitoring pozostanie ograniczony do wpisów `config/watchlist.csv`. Nowe oferty muszą najpierw zasilić watchlistę.

Dla prywatności repo powinno pozostać prywatne. Jeśli nie chcesz wersjonować bazy danych w repo, usuń krok automatycznego commitu i podepnij inne trwałe przechowywanie — bez tego historia zniknie pomiędzy uruchomieniami runnerów.

## Mapa następnych prac

- **v0.2** Import nowych ogłoszeń z dozwolonych alertów e-mail/RSS lub licencjonowanego API; źródło i czas pobrania.
- **v0.3** Trwałe zdjęcia/hashe i precyzyjniejsze deduplikowanie; testowanie fałszywych dopasowań.
- **v0.4** Warstwa cen transakcyjnych RCN oraz porównywalne umowy najmu; punktacja 0–100 tylko przy odpowiedniej jakości danych.
- **v0.5** Alerty o potwierdzonych zmianach (z kontrolą identyfikatorów zdarzeń), panel ofert i kredyt.

## Pochodzenie rozwiązań technicznych

Pomysły rozwinięto na podstawie materiałów Stack Overflow For Agents: techniki Jaccarda do podobnych treści, jawna obsługa przekierowań w web-automatyzacji, trwałe zdarzenia i idempotencja SQLite oraz konserwatywny etap filtrowania wyników AI. Zastosowanie do rynku nieruchomości jest adaptacją, a nie bezpośrednio potwierdzonym testem SOFA.
