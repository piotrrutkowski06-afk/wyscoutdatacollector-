# Wyscout Data Collector

Prywatny panel Streamlit dla scoutingu FK Žalgiris Vilnius. W **Konfiguracja → Timeframe — sezon do eksportu** wybierz **previous season**, **current season** albo oba (domyślnie). Kolektor zapisuje etykietę rzeczywistego sezonu danej ligi i nigdy nie łączy rekordów zawodnika między sezonami. W menu `DISPLAY → Custom` używa `Model S, appka` dla zawodników z pola oraz `Model S, appka - GK` dla bramkarzy. Zapisany zestaw filtrów włącza się osobno.

## Co działa w pierwszej wersji

- **Import Excel**: wgraj oryginalne pliki XLSX. Uzupełnij ligę, rzeczywisty sezon, Timeframe i pozycję każdego pliku. Podaj opcjonalny licznik Wyscout. Wybierz **Sezon w pliku Excel**, a następnie pobierz skoroszyt z osobnym arkuszem każdej ligi i `EXPORT_LOG`. Import nie potwierdza filtrów źródłowych i nie trafia do historii eksportów automatycznych. Brak drużyny jest zgłaszany bez dopisywania danych. Powtórzone nagłówki (np. `Aerial duels per 90` w układzie GK) zostają w osobnych kolumnach.
- **Silnik automatyzacji**: logowanie, wybór rozgrywek, sezonu, pozycji i układu, przycisk eksportu, walidacja, wznowienie oraz odświeżanie. Integracja wymaga potwierdzonego profilu zalogowanego interfejsu oraz dostępu do konta. Zrzuty ekranu potwierdzają etykiety, nie selektory DOM, rzeczywisty format pliku ani dostępność filtrów. **Nie przeprowadzono testu na koncie Wyscout.**
- Pierwszy test przeglądarki ogranicz do jednej ligi, pozycji GK i CB oraz obu sezonów. Dopiero po porównaniu liczników i kolumn rozszerz zakres na pozostałe pozycje.

## Uruchamianie

**Replit:** otwórz podgląd Wyscout Data Collector. Skrypt `scripts/run-wyscout.sh` uruchamia Streamlit na przydzielonym porcie. W razie potrzeby w NixOS Playwright korzysta z Node zainstalowanego w środowisku, a Chromium z systemu.

**Streamlit Community Cloud:** umieść `packages.txt` obok `app.py` i `requirements.txt` w katalogu głównym repozytorium. Cloud instaluje z niego systemowy Chromium; sam wpis `playwright` w `requirements.txt` nie instaluje przeglądarki. Po przebudowie aplikacji wybierz **Diagnostyka → Sprawdź start Chromium**. Domyślnie kolektor wykrywa systemowy Chromium; usuń `WYSCOUT_CHROMIUM` z Secrets, jeśli wskazuje ścieżkę z innego komputera.

**Lokalnie** (Python 3.11+):

```sh
python -m pip install -r requirements.txt
python -m playwright install chromium
python -m streamlit run app.py
```

Na obsługiwanym Linuksie użyj `python -m playwright install --with-deps chromium` przy pierwszej konfiguracji systemowej. Możesz podać istniejący plik wykonywalny w `WYSCOUT_CHROMIUM`. Na własnym komputerze ustaw `WYSCOUT_HEADED=1`, by przy rozpoznanym MFA lub CAPTCHA mieć do 4 minut na dokończenie autoryzacji w oknie Chromium. W trybie headless zadanie kończy się komunikatem; aplikacja nie omija zabezpieczeń logowania.

## Dostęp do panelu i konta Wyscout

Na Streamlit Community Cloud otwórz **Manage app → Settings → Secrets**. Lokalnie użyj nieśledzonego pliku `.streamlit/secrets.toml`, a w Replit dodaj trzy poniższe klucze jako osobne sekrety:

```toml
WYSCOUT_PANEL_PASSWORD = "ODRĘBNE_LOSOWE_HASŁO_DO_PANELU"
WYSCOUT_USERNAME = "LOGIN_DO_WYSCOUT"
WYSCOUT_PASSWORD = "HASŁO_DO_WYSCOUT"
```

Wszystkie trzy wpisy muszą być na głównym poziomie TOML, poza sekcjami. Po aktualizacji usuń starą sekcję `[auth]` oraz `WYSCOUT_ALLOWED_EMAILS`, jeśli ich używałeś. Google Cloud nie jest potrzebny. Do panelu wybierz unikalne hasło o długości od 20 do 256 znaków, inne niż hasło do Wyscout. Możesz wygenerować je lokalnie poleceniem `python -c "import secrets; print(secrets.token_urlsafe(32))"`. Na Community Cloud zachowaj prywatną widoczność aplikacji. Nie umieszczaj sekretów w repozytorium ani w czacie.

Po zapisaniu sekretów otwórz aplikację i wpisz hasło panelu. Sesja wygasa po 8 godzinach; możesz też użyć **Wyloguj się**. Bez skonfigurowanego hasła dostępny jest tylko ręczny import XLSX, bez prywatnych zadań i historii.

`WYSCOUT_USERNAME` i `WYSCOUT_PASSWORD` są używane wyłącznie przez kolektor na stronie Wyscout. Po zalogowaniu do panelu użyj **Pobieranie → Test Wyscout Connection**, aby sprawdzić logowanie i otwarcie Advanced Search. Test wymaga działającego Chromium. Jeśli brak osobnego profilu i nie podano `WYSCOUT_ADAPTER_CONFIG`, test korzysta z dołączonego szablonu. Osobny przycisk **Pobierz listę rozgrywek** wymaga profilu z potwierdzoną pełną listą; udany test logowania jej nie zastępuje. Nie zapisujemy cookies ani sesji Wyscout. Na wygaśnięcie sesji podczas pobierania kolektor odpowiada najwyżej jedną próbą ponownego logowania. Używaj kolektora tylko w ramach uprawnień licencji Hudl.

Jeśli Wyscout wyświetli **Multiple access attempt**, kolektor domyślnie zatrzyma się z kodem `W9`. Wyloguj się z pozostałych sesji albo w **Pobieranie** zaznacz **Zezwól na Force login w następnej operacji** i ponów wybraną operację. **Force login zamyka wszystkie pozostałe sesje konta Wyscout.** Zgoda jest jednorazowa: nie przechodzi na kolejną operację ani na automatyczne ponowne logowanie. Kolektor kliknie przycisk najwyżej raz, wyłącznie na rozpoznanym ekranie Wyscout. Następnie musi jeszcze potwierdzić otwarcie Advanced Search.

Jeżeli test zwróci błąd, przekaż wyłącznie treść komunikatu z kodem `W1`–`W9`. Kod opisuje etap, na którym test się zatrzymał. Raport `SC-W6-2` pokazuje kategorię końcowego ekranu, stan dokumentu, które ze znanych elementów są widoczne oraz zagregowane błędy przeglądarki i sieci po kliknięciu Continue. Nie zawiera treści strony, adresów, zrzutów ani danych konta. HTTP 200 dokumentu nie potwierdza poprawności hasła ani załadowania całego interfejsu. Nie przesyłaj danych logowania, ciasteczek ani adresów przekierowań.

W **Pobieranie → Prywatny podgląd W6** uprawniony użytkownik może zaznaczyć opcję podglądu przed uruchomieniem testu. Obraz powstaje tylko przy `W6` na stronie `wyscout.hudl.com`, bez rozpoznanego formularza logowania. Pola formularzy są zasłonięte. Obraz trafia bezpośrednio do bieżącej sesji panelu: bez pliku na dysku i bez publicznego linku. **Nie udostępniaj obrazu** — może nadal zawierać dane konta. Do dalszej diagnostyki wystarczy nagłówek lub treść komunikatu bez danych osobowych. **Ukryj podgląd** usuwa obraz z panelu. Podgląd jest dostępny tylko w sprawdzonej wersji Streamlit, przypiętej w zależnościach.

## Profil interfejsu Wyscout

1. Skopiuj `config/wyscout.example.json` do `config/wyscout.json` lub wskaż istniejący profil przez `WYSCOUT_ADAPTER_CONFIG`. `null` oznacza **brak potwierdzonego selektora**, a nie dowolnie odgadniętą wartość.
2. Zweryfikuj w zwykłej sesji przeglądarki elementy logowania, przycisk `Advanced Search`, formularze, aktywne etykiety oraz dokładny licznik. Stany `results_ready` / `results_loading` muszą odróżniać nowy, gotowy wynik od wyniku poprzedniego zapytania. W `active_season` oczekiwany jest widoczny rok sezonu właściwy dla rozgrywek.
3. Potwierdź w mapie `positions` kontrolki pozycji, stan wyboru i podsumowanie **wyłącznie jednej** pozycji głównej. `reset_filters` musi przywracać stan bez filtrów, w tym filtrów poprzednich zapytań. Zamieszczone elementy boiska na zrzutach są oznaczone numerami; nie zakładamy mapowania na kody pozycji.
4. `competition_list_complete` można włączyć dopiero po sprawdzeniu, że selektor wskazuje wszystkie dostępne ligi (nie tylko widoczną stronę czy fragment wirtualizowanej listy). W przeciwnym razie wybierz ligę ręcznie w konfiguracji.
5. Podział przy ≥500 wynikach jest domyślnie wyłączony. Włącz go tylko, jeśli umowa i interfejs dopuszczają takie zapytania, granice dat są włączne, a przedziały pokrywają cały wynik. Jeśli brakuje kompletnych danych, zadanie nie zapisze ich jako kompletnych.
6. Powtórzenia sprawdzamy po zawodniku i klubie tylko w obrębie jednej ligi i rzeczywistego sezonu. `Player ID` i data urodzenia nie są wymagane. Źródłem klubu jest `Team within selected timeframe`; tylko dla `current season`, gdy nie ma tej kolumny, dopuszczamy `Team`. Świeży eksport GK z `previous season` potwierdził, że obie kolumny mogą wskazywać inne kluby; świeżego eksportu CB nie sprawdzono. W automatycznym eksporcie wymagamy klubu dla każdego wiersza i porównujemy liczbę wierszy z licznikiem Wyscout. Jeśli mimo filtra pozycji głównej klucz się powtarza, zachowujemy wszystkie wiersze i odnotowujemy to w `EXPORT_LOG`.

## Dane, wznowienie i hosting

- Import, konfiguracja, historia, postęp i diagnostyka są w `app.py` i `wyscout/import_panel.py`. `wyscout/wyscout_adapter.py` zawiera wszystkie interakcje z Wyscout. `excel.py`, `jobs.py`, `store.py` i `partition.py` odpowiadają za Excela, przebieg, SQLite i podział zapytań.
- Jedna instalacja z jednym katalogiem danych wykonuje najwyżej jedno zadanie na raz. Po restarcie wybierz **Resume Download**. Wznowienie potwierdza sezon i używa zweryfikowanych, nienaruszonych części. Kolektor nigdy nie uzupełnia braków zerami ani nie zastępuje poprawnej bazy niekompletną. `Refresh Database` podmienia tylko wybrane pary ligi i sezonu, a poprzednie wersje zostają w historii.
- Na hoście z nietrwałym systemem plików skieruj `WYSCOUT_DATA_DIR` na prywatny trwały wolumen z poprawną obsługą blokad plikowych. Alternatywnie zatrzymaj kolektor, wykonaj kopię całego tego katalogu na szyfrowany nośnik i przywróć go przed wznowieniem. Kopia musi zawierać SQLite, manifesty i pobrane pliki. Nie umieszczaj danych komercyjnych ani kopii w GitHub.
- **Prywatny GitHub → Streamlit Community Cloud:** wejście `app.py`, wersja Pythona 3.13 (zalecana; taka jest blokada zależności Replit), zależności `requirements.txt`. Import nie potrzebuje Chromium. Do automatyzacji Playwright dystrybucja musi mieć Chromium i jego biblioteki. Na Streamlit Community Cloud nie potwierdzono niezawodności długich zadań ani trwałości plików po restarcie; nie przeznaczaj tego wariantu na produkcyjne pobieranie bez weryfikacji. Nie wdrożono oddzielnego zdalnego worker API.

## Testy

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Testy raportów i SQLite używają jawnie syntetycznych plików. Nie świadczą o działaniu logowania lub eksportowania na koncie Wyscout.
