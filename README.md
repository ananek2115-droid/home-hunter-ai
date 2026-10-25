[README.md](https://github.com/user-attachments/files/33673803/README.md)
# HOME HUNTER AI 4.0 — Trójmiasto

**Działający prototyp wykrywania kandydatów w RSS i kontroli znanych ofert. Nie potwierdza automatycznie możliwości zakupu ani rentowności; zablokowane strony pozostają do weryfikacji ręcznej.**

Kryteria: Gdańsk / Gdynia / Sopot, zakup inwestycyjny, min. 35 m², **750 000 zł łącznie z dodatkowymi kosztami**, **priorytet: sprzedaż od właściciela lub 0% prowizji kupującego**; agencje tylko jako udokumentowane wyjątki. Zakładamy model najmu całorocznego bez kredytu — kalkulator kredytowy do późniejszego wdrożenia.

## Co jest zaimplementowane

- CSV → trwała baza SQLite z historią zaimportowanych zmian cen.
- Automatyczna kontrola znanych linków, uwzględniająca HTTP 404/410, 403/429, przekierowania, niezgodny ID oraz widoczne parametry ogłoszenia.
- Rozpoznawanie prawdopodobnych duplikatów, bez automatycznego łączenia mieszkań z tego samego budynku.
- Kalkulator: pełny nakład, przychód przy 11 miesiącach najmu, ryczałt, koszty właściciela, rentowność i cena graniczna przy celu 4%.
- Raport Markdown podzielony na oferty z dopasowaną stroną, niezweryfikowane i uszkodzone linki.
- Testy regresyjne w GitHub Actions, w tym filtrowanie sprzedawców, przekierowania i duplikaty.

## Bardzo ważne ograniczenia

1. **Wykrywanie nowych kandydatów, nie gotowych okazji.** Program pobiera RSS z Domy.pl i Domiporta, a także obsługuje osobną komendę eksperymentalnego wyszukiwania Bing RSS. Nie przeszukuje prywatnych API OLX ani nie obchodzi blokad. Kandydat z RSS nie jest potwierdzoną, nadal dostępną ofertą.
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

1. Repozytorium `home-hunter-ai` już istnieje. Jest obecnie **publiczne**; przed przechowywaniem prywatnych notatek negocjacyjnych, dokumentów lub danych finansowych zmień widoczność na prywatną.
2. Przenieś zawartość ZIP do repozytorium; w GitHub wybierz **Actions → Home Hunter Monitor → Run workflow** na początek testowo.
3. Workflow sam odpali testy, zaimportuje obserwowane linki, spróbuje sprawdzić ich strony i zapisze historię do bazy SQLite w prywatnym repo. Wymaga uprawnienia **Actions: read and write permissions** (lub reguły pozwalającej `contents: write`) i możliwości push do gałęzi.
4. Włączenie cron uruchamia kontrole o `06:15` i `16:15 UTC` (czas w Polsce zmienia się z CET na CEST). GitHub scheduled workflows są best effort i mogą się opóźniać lub nie uruchomić w danym terminie.
5. Monitoring obejmuje historyczne wpisy `config/watchlist.csv` oraz odkrywanie kandydatów z RSS. Domyślna lokalizacja to wyłącznie Gdańsk, Gdynia i Sopot. Starsze dane innych miast pozostają w SQLite, lecz nie pojawiają się w bieżącym rankingu. Kandydaci RSS pozostają niepotwierdzeni, dopóki źródło nie przejdzie niezależnej kontroli.

Dla prywatności repo powinno pozostać prywatne. Jeśli nie chcesz wersjonować bazy danych w repo, usuń krok automatycznego commitu i podepnij inne trwałe przechowywanie — bez tego historia zniknie pomiędzy uruchomieniami runnerów.

## Mapa następnych prac

- **v0.2** [częściowo wykonane] Import kandydatów z RSS, z zapisem źródła i czasu obserwacji; pozostało rozbudowanie dozwolonych źródeł i wiarygodności weryfikacji.
- **v0.3** Trwałe zdjęcia/hashe i precyzyjniejsze deduplikowanie; testowanie fałszywych dopasowań.
- **v0.4** Warstwa cen transakcyjnych RCN oraz porównywalne umowy najmu; punktacja 0–100 tylko przy odpowiedniej jakości danych.
- **v0.5** Alerty o potwierdzonych zmianach (z kontrolą identyfikatorów zdarzeń), panel ofert i kredyt.

## Pochodzenie rozwiązań technicznych

Pomysły rozwinięto na podstawie materiałów Stack Overflow For Agents: techniki Jaccarda do podobnych treści, jawna obsługa przekierowań w web-automatyzacji, trwałe zdarzenia i idempotencja SQLite oraz konserwatywny etap filtrowania wyników AI. Zastosowanie do rynku nieruchomości jest adaptacją, a nie bezpośrednio potwierdzonym testem SOFA.

## Stan operacyjny i interpretacja testów — 25.10.2026

- Testy jednostkowe sprawdzają kod na danych kontrolowanych; zielony workflow **nie dowodzi**, że na zewnętrznych portalach udało się zweryfikować jakąkolwiek aktywną ofertę.
- Domyślny zakres produkcyjny: Gdańsk, Gdynia, Sopot. Toruń jest wspierany tylko jako historyczny format danych lub opcjonalne jawne ustawienie funkcji, lecz nie jest przeszukiwany przez harmonogram i nie występuje w głównym raporcie.
- `access_restricted` oznacza ograniczenia robota/serwisu, nie sprzedaż ani usunięcie lokalu. Gdy 0 ofert uzyska świeże dopasowanie strony, raport nie daje podstaw do rekomendowania zakupu.
- Workflow nie ukrywa już awarii wszystkich kanałów RSS za `continue-on-error`. Problem źródła powinien być widoczny jako nieudany krok zamiast fałszywego potwierdzenia działania radaru.
- Nadal brak zweryfikowanego, automatycznego modelu najmu dla nowych kandydatów; nie należy promować niskiej ceny za m² jako najwyższej stopy zwrotu.

## Priorytet sprzedaży bezpośredniej i 0% prowizji (v4.1)

1. Domyślnie preferuj ogłoszenia deklarujące sprzedaż prywatną **lub** 0% prowizji **dla kupującego**. W sekcji priorytetowej nie pokazuj typowych agencji z nieznaną prowizją.
2. Wyszukiwanie przez publiczny indeks RSS Bing zawiera najpierw zapytania o sprzedaż bezpośrednią, 'od właściciela' i 0% prowizji, a następnie zapytania ogólne. To źródło jest uzupełniające i może się nie powieść niezależnie od oficjalnego RSS.
3. Informację tekstową z ogłoszenia klasyfikujemy jako `owner_claim` lub `zero_fee_claim` tylko gdy wskazuje właściciela lub konkretnie kupującego. Ogólne hasła `bez prowizji` / `0% prowizji` bez wskazania strony mają osobny status `fee_ambiguous` i nie wchodzą do priorytetu. Żaden tekst nie oznacza zweryfikowanego 0 zł.
4. `agency_unknown`, `agency_fee` i `conflict` są wyłącznie rezerwą. Promocja wyjątku agencyjnego wymaga pisemnego potwierdzenia prowizji albo wiarygodnej przewagi cenowej po uwzględnieniu wszystkich opłat. Takiej przewagi model jeszcze nie weryfikuje.
5. `unknown` jest osobną listą wymagającą ustalenia sprzedającego. Niewiedza o prowizji nigdy nie oznacza prowizji 0 zł. Nie należy przypisywać portalu OLX ani Domy.pl do właściciela na podstawie domeny.
6. Kontrole nowo odkrytych mieszkań próbują w pierwszej kolejności wybierać deklarowane oferty prywatne/bez prowizji i różne lokalizacje; blokady `robots.txt` nadal pozostają nierozstrzygnięte.

