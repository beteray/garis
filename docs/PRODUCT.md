# GARIS — czym jest, dla kogo i co obiecuje

Dokument rozstrzygający. Jeśli kod robi coś innego niż tu napisano, to kod jest
do poprawy.

## Czym GARIS jest

Osobistym agentem, któremu mówisz **jaki chcesz rezultat**, a on dobiera metodę.
Działa w tle na Twoim komputerze, pamięta Cię między uruchomieniami, prowadzi
zadania trwające dniami i pyta tylko wtedy, gdy naprawdę musi.

## Czym GARIS nie jest

- **Nie jest czatem.** Rozmowa to droga do rezultatu, nie produkt sam w sobie.
- **Nie jest launcherem modeli.** Nie wybierasz modelu, tak jak nie wybierasz
  algorytmu sortowania w edytorze tekstu.
- **Nie jest narzędziem dla programistów.** Tryb developerski istnieje, ale jest
  trybem, nie domyślnym stanem.
- **Nie jest usługą chmurową.** Stan, pamięć i sejf zostają na maszynie.

## Dla kogo

Dla osoby, która ma komputer z Windows 11 i coś do zrobienia. Nie zakładamy
wiedzy o API, modelach, kluczach ani wierszu poleceń. Zakładamy, że użytkownik
potrafi opisać, czego chce.

Nie profilujemy po płci, wieku ani wyglądzie. Adaptacja opiera się **wyłącznie**
na tym, co użytkownik wybrał, oraz na zaobserwowanych wzorcach, które można
obejrzeć, poprawić i wyłączyć.

## Co użytkownik ma rozumieć

Cztery rzeczy, i ani jednej więcej:

1. Mówi, co ma być zrobione.
2. GARIS pracuje w tle i informuje rzadko.
3. Przy operacjach nieodwracalnych pyta o zgodę.
4. Wszystko, co GARIS pamięta, można obejrzeć i skasować.

## Co ma zostać ukryte

Nazwy dostawców · nazwy narzędzi · identyfikatory sejfu · ścieżki `vault://` ·
endpointy API · stany wewnętrzne · komendy CLI · nazwy modeli · limity tokenów ·
klucze bazy · szczegóły planu wykonania.

Wszystko powyższe jest dostępne w trybie developerskim. Nic z tego nie pojawia
się domyślnie.

## Co GARIS robi bez pytania

Czytanie plików i katalogów · analiza systemu · wyszukiwanie w sieci ·
przygotowywanie treści · tworzenie i modyfikowanie plików w obszarze roboczym ·
uruchamianie programów · instalacja z zaufanych źródeł · zmiany konfiguracji
odwracalne · zapis do pamięci · ponawianie po awarii · zmiana narzędzia lub
modelu, gdy pierwszy zawiódł.

## Co zawsze wymaga zgody

Pięć efektów, niezależnie od ustawień, bez możliwości wyłączenia:

**płatność · publikacja · wiadomość wysłana w Twoim imieniu · zmiana danych
logowania · trwałe usunięcie**

Do tego progi: duże pobranie, znaczący koszt, nieodwracalna zmiana systemowa,
podniesienie uprawnień.

Okno zgody pokazuje: dokładną akcję, cel, konto, odbiorcę, treść, cenę,
odwracalność i oczekiwany rezultat. Zgoda dotyczy **zadania**, nie kroku — raz
zatwierdzona instalacja nie pyta osiem razy.

## Gwarancje

1. Nic nie wykonuje się poza kontrolowaną ścieżką z audytem.
2. Osobowość nigdy nie zmienia reguł bezpieczeństwa ani faktów.
3. Poświadczenia nigdy nie trafiają do promptu, logu ani pamięci.
4. Zamknięcie okna nie zatrzymuje pracy.
5. GARIS nie zgłasza sukcesu, którego nie zweryfikował.
6. Milczenie jest domyślne.
7. Żadnej telemetrii bez wyraźnej zgody.

## Gdy GARIS nie jest pewny

Pyta **konkretnie**, nie ogólnie. Nie „potrzebuję więcej informacji", tylko
„Który katalog mam wyczyścić — Pobrane czy Dokumenty?". Pytanie zawiera opcje,
gdy je zna. Zadanie czeka w stanie z widoczną przyczyną i nie umiera.

Jeśli niepewność dotyczy rezultatu, a nie metody — GARIS wykonuje to, co da się
wykonać bezpiecznie, i mówi, czego nie zrobił.

## Gdy zadanie się nie uda

Najpierw próbuje sam: inne narzędzie, inny model, inny sposób. Bez informowania.

Gdy się nie da, mówi w jednym zdaniu **co** się nie udało i **co z tego wynika**.
Nie pokazuje stosu wywołań. Podaje jedną rekomendowaną akcję. Zawsze zostawia
możliwość ponowienia i podejrzenia szczegółów.

Zadanie częściowo wykonane raportuje się jako częściowe — nie jako sukces i nie
jako porażka.

---

# Model użytkownika

Jeden profil, edytowalny w całości, inspekcjonowalny w całości.

| Grupa | Pola |
|---|---|
| Tożsamość | imię, forma zwracania się, język |
| Komunikacja | styl, długość odpowiedzi, formalność, humor, poziom szczegółów technicznych |
| Dostępność | redukcja ruchu, kontrast, skala tekstu, czytnik ekranu |
| Rytm | godziny ciszy, tryb gry, tolerancja powiadomień, godziny pracy |
| Kontekst | zainteresowania, projekty, urządzenia, częste aplikacje |
| Prywatność | co może iść do chmury, co zostaje lokalnie, czy wolno się uczyć |
| Modele | preferencje dostawców, budżet, tryb tylko-lokalny |

**Źródło każdego pola jest zapisane**: wybrane przez użytkownika albo
zaobserwowane. Zaobserwowane da się odrzucić pojedynczo i wyłączyć globalnie.

Personalizacja wpływa na: język, ton, poziom szczegółów, gęstość interfejsu,
powiadomienia, briefingi, sugestie, sposób raportowania, głos, wygląd.

Personalizacja **nigdy** nie wpływa na: bramki zgody, poprawność faktów,
kompletność raportu, bezpieczeństwo.

---

# Model zgody

| Poziom | Kiedy | Jak wygląda |
|---|---|---|
| Brak | odczyt, analiza, wyszukiwanie | nic |
| Cicha notatka | zmiana odwracalna | wpis w dzienniku |
| Zgoda na zadanie | pierwszy efekt wymagający zgody w tym zadaniu | karta z pełnym kontekstem |
| Zgoda twarda | pięć efektów nienegocjowalnych | karta + jawne potwierdzenie |
| Odmowa | poza politykami | wyjaśnienie, bez pytania |

Zgoda jest trwała w bazie — może przyjść po restarcie albo z telefonu.

---

# Model pamięci i sejfu

Siedem rozdzielnych rzeczy, dziś częściowo pomieszanych:

| Co | Gdzie | Czas życia | Widoczne dla użytkownika |
|---|---|---|---|
| Profil | baza stanu | trwałe | tak, edytowalne |
| Pamięć długoterminowa | baza stanu, szyfrowana | trwałe / z wygaśnięciem | tak, pełna kontrola |
| Kontekst rozmowy | pamięć procesu | sesja | pośrednio |
| Kontekst zadania | baza stanu | życie zadania | w szczegółach zadania |
| Sekrety | **osobna baza, osobny klucz** | trwałe | tylko nazwa ludzka |
| Audyt | baza stanu | rotacja | w diagnostyce |
| Logi diagnostyczne | pliki | rotacja | na żądanie |

Każdy wpis pamięci: źródło, czas utworzenia, pewność, ostatnie użycie,
wygaśnięcie, **powód zapamiętania**, **klasyfikacja wrażliwości**, przypięcie.

Cztery pytania, na które GARIS musi umieć odpowiedzieć:
„Co o mnie pamiętasz?" · „Dlaczego to pamiętasz?" · „Zapomnij o tym." ·
„Nie ucz się z tej rozmowy."

Sekrety mają **nazwę ludzką** (`Klucz Gemini`) i nazwę techniczną
(`gemini_api_key`). Interfejs pokazuje pierwszą. Tryb developerski obie.
