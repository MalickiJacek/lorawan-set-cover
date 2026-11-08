# Optymalizacja rozmieszczenia anten LoRaWAN - Instrukcja v3.1

> Instrukcja do skryptu QGIS `qgis/lorawan_set_cover_qgis.py`. Opis wersji bez QGIS (GeoPandas, CLI) znajduje się w głównym [README](../README.md). Przykładowe dane w `data/sample/` są w pełni syntetyczne.

## Spis treści

1. [Opis problemu](#opis-problemu)
2. [Założenia](#założenia)
3. [Wymagania](#wymagania)
4. [Dane wejściowe](#dane-wejściowe)
5. [Parametry konfiguracyjne](#parametry-konfiguracyjne)
6. [Przebieg analizy](#przebieg-analizy)
7. [Wyniki](#wyniki)
8. [Możliwe modyfikacje](#możliwe-modyfikacje)

---

## Opis problemu

### Problem pokrycia zbiorów (Set Cover Problem)

Celem analizy jest **optymalizacja rozmieszczenia anten LoRaWAN** w taki sposób, aby pokryć zasięgiem wszystkie budynki przy użyciu **minimalnej liczby anten**.

Jest to klasyczny problem optymalizacji kombinatorycznej znany jako **Set Cover Problem (SCP)**:

- **Zbiór uniwersalny U** = wszystkie budynki do pokrycia
- **Rodzina podzbiorów S** = każda potencjalna antena pokrywa pewien podzbiór budynków
- **Cel**: znaleźć minimalną liczbę anten (podzbiorów), które łącznie pokryją wszystkie budynki

### Metoda rozwiązania — v3.0: Integer Linear Programming (ILP)

W wersji v3.0 zastąpiono heurystykę zachłanną (greedy) **dokładnym solverem ILP** opartym na bibliotece **PuLP** z solverem **CBC** (COIN-BC). Od v3.1 skrypt automatycznie użyje **HiGHS**, jeśli CBC jest niedostępny.

#### Dlaczego ILP jest lepsze od greedy?

| Cecha                        | Greedy (v1/v2)                 | ILP (v3)                      |
| ---------------------------- | ------------------------------ | ----------------------------- |
| Jakość rozwiązania           | Przybliżenie (ln(n) × optimum) | **Dokładne globalne optimum** |
| Gwarancja wyniku             | Nie                            | **Tak**                       |
| Czas obliczeń (< 500 anten)  | < 1 s                          | < 5 s                         |
| Czas obliczeń (> 2000 anten) | < 1 s                          | kilkadziesiąt sekund          |

Na syntetycznych danych testowych ILP potrzebuje o **12–29% mniej anten** niż algorytm zachłanny (w zależności od zasięgu, patrz `docs/img/ilp_vs_greedy.png`). ILP zawsze znajdzie **minimalne możliwe rozwiązanie**.

### Kontekst praktyczny

W planowaniu sieci LoRaWAN uwzględniono następujące parametry:

- Anteny mają ograniczony zasięg (przyjmowany jako stały dla każdej anteny lub zmienny w zależności od wysokości montażu, gęstości zabudowy)
- Część budynków jest już pokryta przez istniejącą infrastrukturę (kilka już zamontowanych anten)
- Chcemy dobrać minimalna liczbę nowych anten z puli potencjalnych lokalizacji

---

## Założenia przyjęte do rozwiązania zadania

### Geometryczne

- Zasięg anteny modelowany jest jako **okrąg** (bufor) o określonym promieniu
- Budynek jest "pokryty" jeśli jego **centroid** leży w zasięgu anteny
- Dane wejściowe są w **metrycznym układzie współrzędnych** (EPSG:2177 → Układ 2000)

### Algorytmiczne

- Wszystkie anteny mają **jednakowy koszt** (równoważne znaczenie w funkcji celu)
- **Tryb klasyczny**: ILP gwarantuje **dokładne minimum** liczby anten pokrywających wszystkie budynki
- **Tryb z budżetem**: ILP gwarantuje **dokładne maksimum** sumy lokali (LM+LU) przy zadanej liczbie anten

### Dane

- Każdy obiekt posiada identyfikator ID_SWDE, który jest unikalny w obrębie danego typu obiektu
- Anteny mogą mieć atrybut **WYSOKOSC** montażu do wyznaczania zmiennych zasięgów
- Budynki do pokrycia → wszystkie budynki, w których znajdują się lokale mieszkalne lub użytkowe objęte zdalnym odczytem
- Budynki mogą posiadać atrybuty **LM** (ilość lokali mieszkalnych) i **LU** (ilość lokali użytkowych) — wymagane w trybie wagowania
- Anteny potencjalne → budynki co najmniej piętrowe, na których dachach możliwy jest montaż anteny

---

## Wymagania

### Oprogramowanie

- **QGIS 3.42+** z konsolą Python
- Python 3.x (dostarczony z QGIS)

### Biblioteki Python

Instalacja w OSGeo4W Shell:

```bash
pip install "pulp>=2.7,<4" pandas
```

> PuLP 4.0 zmienił API i nie zawiera już solvera CBC — skrypt wymaga wersji 2.7–3.x.

> **Uwaga v3.0**: Biblioteka `SetCoverPy` nie jest już wymagana. Zamiast niej używana jest `pulp` z wbudowanym solverem CBC. Jeśli masz zainstalowaną `SetCoverPy` z v1/v2 — możesz ją pozostawić lub odinstalować; nie wpływa na działanie v3.

---

## Dane wejściowe

Pliki wskazuje się w oknie dialogowym, więc nazwy są dowolne (dane przykładowe: `data/sample/Buildings_To_Cover.shp`, `Existing_Antennas.shp`, `Candidate_Antennas.shp`):

| Plik                      | Opis                                  | Geometria | Wymagane kolumny        |
| ------------------------- | ------------------------------------- | --------- | ----------------------- |
| `Budynki_do_pokrycia.shp` | Budynki wymagające pokrycia zasięgiem | Poligon   | ID_SWDE, LM\*\*, LU\*\* |
| `Anteny_istniejace.shp`   | Lokalizacje istniejących anten        | Poligon   | ID_SWDE                 |
| `Anteny_potencjalne.shp`  | Potencjalne lokalizacje nowych anten  | Poligon   | ID_SWDE, WYSOKOSC\*     |

\*Kolumna WYSOKOSC wymagana tylko przy użyciu wzoru na zasięg
\*\*Kolumny LM i LU wymagane tylko w trybie wagowania

---

## Parametry konfiguracyjne

### Tryby określania zasięgu (bufora)

#### 1. Stała wartość

```
500
```

Wszystkie anteny mają ten sam zasięg (np. 500 metrów).

#### 2. Wzór z kolumną wysokości

```
400+10*WYSOKOSC
```

Zasięg obliczany indywidualnie dla każdej anteny na podstawie wysokości budynku.

### Przykładowe wzory

| Wzór              | Opis                                 |
| ----------------- | ------------------------------------ |
| `500`             | Stały zasięg 500 m                   |
| `300+15*WYSOKOSC` | 300 m + 15 m za każdy metr wysokości |
| `WYSOKOSC*50`     | Zasięg proporcjonalny do wysokości   |

---

### Tryb wagowania i optymalizacji (nowość v3.0)

Checkbox **"Włącz tryb wagowania (LM+LU)"** aktywuje pola wagowania i umożliwia przełączenie między dwoma trybami ILP.

#### Tryb 1: Klasyczny Set Cover (domyślny, limit anten = 0)

- ILP **minimalizuje liczbę anten** koniecznych do pokrycia WSZYSTKICH budynków
- Gwarantuje dokładne globalne optimum
- Wagi LM+LU używane są wyłącznie do statystyk i detekcji anten mało istotnych

#### Tryb 2: Maksymalizacja pokrycia z budżetem (limit anten > 0)

- Wymaga włączonego trybu wagowania
- ILP **maksymalizuje sumę lokali (LM+LU)** pokrytych budynków przy zadanym limicie anten
- Przydatny gdy: pełne pokrycie nie jest możliwe lub gdy chcemy wybrać N najlepszych anten
- Priorytetyzuje anteny pokrywające budynki z dużą liczbą lokali

#### Parametry trybu wagowania

| Parametr                     | Domyślnie | Opis                                                  |
| ---------------------------- | --------- | ----------------------------------------------------- |
| Włącz tryb wagowania (LM+LU) | Wyłączone | Checkbox aktywujący wagowanie                         |
| Kolumna LM                   | `LM`      | Nazwa kolumny z liczbą lokali mieszkalnych            |
| Kolumna LU                   | `LU`      | Nazwa kolumny z liczbą lokali użytkowych              |
| Próg "mało istotnych" anten  | `30`      | Antena z sumą LM+LU ≤ próg w zasięgu → mało istotna   |
| Limit anten — budżet         | `0`       | 0 = tryb klasyczny; >0 = tryb maksymalizacji pokrycia |

---

### Parametry solvera ILP (nowość v3.0)

| Parametr                | Domyślnie | Opis                                          |
| ----------------------- | --------- | --------------------------------------------- |
| Limit czasu solvera (s) | `300`     | Maksymalny czas pracy solvera CBC w sekundach |

Przy przekroczeniu limitu czasu solver zwraca **najlepsze znalezione wykonalne rozwiązanie** — może ono nie być optymalne, ale nadal jest poprawnym rozwiązaniem. Dla typowych instancji (< 500 anten) optymalność jest osiągana w kilka sekund.

---

## Przebieg analizy

### Diagram przepływu

```
┌─────────────────────────────────────────────────────────────┐
│                    DANE WEJŚCIOWE                           │
│  Budynki_do_pokrycia.shp  Anteny_istniejace.shp             │
│  Anteny_potencjalne.shp                                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 1: Centroidy                           │
│  Konwersja poligonów na punkty (środki ciężkości)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 2: Bufor istniejących anten            │
│  Utworzenie stref zasięgu wokół istniejących anten          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 3: Wyłączenie pokrytych budynków       │
│  Usunięcie budynków już w zasięgu istniejących anten        │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 4: Bufor potencjalnych anten           │
│  Utworzenie stref zasięgu dla wszystkich potencjalnych anten │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 5: Złączenie przestrzenne              │
│  Przypisanie budynków do anten (które anteny pokrywają      │
│  które budynki)                                             │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 6: Eksport do CSV                      │
│  Macierz_GIS.csv: pary (Budynek_ID, Antena_ID)              │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│              KROK 6b: Budowa wag [opcjonalnie]              │
│  Słownik budynek → LM+LU (jeśli tryb wagowania włączony)   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│           KROK 7: Algorytm Set Cover — ILP (PuLP/CBC)      │
│                                                             │
│  Tryb A (limit=0):  min Σ xⱼ  s.t. każdy budynek pokryty  │
│  Tryb B (limit>0):  max Σ wᵢyᵢ  s.t. Σ xⱼ ≤ limit         │
│                                                             │
│  → Dokładne globalne optimum (nie przybliżenie)            │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 8: Detekcja nadmiarowych anten         │
│  Anteny z >=75% pokryciem przez inne wybrane anteny         │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 9: Detekcja anten o niskim znaczeniu   │
│  <= 5 budynków lub <= N lokali w zasięgu                    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 10: Wizualizacja                       │
│  Dodanie warstw do projektu QGIS z odpowiednią symbolizacją │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 KROK 11: Podsumowanie                       │
│  Statystyki LM/LU, scenariusz bez anten mało istotnych     │
└─────────────────────────────────────────────────────────────┘
```

### Szczegółowy opis kroków

#### Krok 1–6: Przygotowanie danych przestrzennych

Kroki 1–6 są identyczne z v2.0:

- **Krok 1**: Centroidy — konwersja poligonów na punkty
- **Krok 2**: Bufor istniejących anten — strefy zasięgu
- **Krok 3**: Wyłączenie budynków już pokrytych przez istniejące anteny
- **Krok 4**: Bufor potencjalnych anten
- **Krok 5**: Złączenie przestrzenne (join by location) — pary budynek–antena
- **Krok 6**: Eksport par do pliku `Macierz_GIS.csv`

#### Krok 6b: Budowa wag (opcjonalnie)

- Wykonywany tylko gdy włączony tryb wagowania
- Dla każdego budynku obliczana jest waga = LM + LU (minimum 1)
- Wagi służą bezpośrednio jako parametry w modelu ILP (bez powielania wierszy jak w v2)

#### Krok 7: Algorytm Set Cover — ILP _(zmieniony w v3)_

W zależności od ustawień uruchamiany jest jeden z dwóch modeli ILP:

**Tryb A — Klasyczny Set Cover** (`limit anten = 0`):

1. Wczytanie par budynek–antena z CSV
2. Budowa modelu ILP: zmienne `xⱼ ∈ {0,1}` dla każdej anteny
3. Funkcja celu: `min Σ xⱼ`
4. Ograniczenia: dla każdego budynku `i` — `Σⱼ∈S(i) xⱼ ≥ 1`
5. Rozwiązanie solverem CBC
6. Weryfikacja pokrycia i zwrócenie listy wybranych anten

**Tryb B — Maksymalizacja pokrycia z budżetem** (`limit anten > 0`):

1. Wczytanie par budynek–antena z CSV
2. Budowa modelu ILP: zmienne `xⱼ, yᵢ ∈ {0,1}` dla anten i budynków
3. Funkcja celu: `max Σᵢ wᵢ · yᵢ` (suma lokali pokrytych budynków)
4. Ograniczenia: budżet `Σ xⱼ ≤ max_antennas` + powiązania budynek–antena
5. Rozwiązanie solverem CBC
6. Weryfikacja pokrycia i zwrócenie listy wybranych anten

#### Krok 8: Detekcja nadmiarowych anten

- Dla każdej wybranej anteny sprawdzane jest, jaki procent jej bufora pokrywa się z buforami pozostałych wybranych anten
- Anteny z pokryciem >= 75% oznaczane są jako **nadmiarowe** (potencjalnie do odrzucenia)
- Uwaga: w trybie ILP nadmiarowość może być rzadsza niż przy greedy — solver wybiera anteny efektywniej

#### Krok 9: Detekcja anten o niskim znaczeniu

- **Tryb klasyczny** (bez wagowania): antena mało istotna gdy ≤ 5 budynków w zasięgu
- **Tryb wagowania**: antena mało istotna gdy suma LM+LU ≤ próg (domyślnie 30)
- Anteny już oznaczone jako nadmiarowe są pomijane

#### Krok 10b: Eksport wybranych anten do CSV _(nowość v3)_

Generuje plik `wybrane_anteny.csv` ze szczegółowymi statystykami dla każdej wybranej anteny:

| Kolumna                     | Opis                                                                                                                  |
| --------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `Antena_ID`                 | Identyfikator anteny (ID_SWDE)                                                                                        |
| `Kategoria`                 | `niezbedna` / `potencjalnie nadmiarowa` / `malo istotna`                                                              |
| `Budynki`                   | Łączna liczba budynków w zasięgu tej anteny                                                                           |
| `LM`                        | Łączna liczba lokali mieszkalnych w zasięgu                                                                           |
| `LU`                        | Łączna liczba lokali użytkowych w zasięgu                                                                             |
| `Pokrycie_unikalne_budynki` | Budynki pokryte **wyłącznie** przez tę antenę (nie przez żadną inną wybraną)                                          |
| `Pokrycie_unikalne_LM`      | LM w budynkach unikalnie pokrytych                                                                                    |
| `Pokrycie_unikalne_LU`      | LU w budynkach unikalnie pokrytych                                                                                    |
| `Adres`                     | Adres budynku anteny (kolumna opcjonalna — pojawia się tylko gdy warstwa anten potencjalnych zawiera kolumnę `Adres`) |
| `Nakladanie_pct`            | % powierzchni bufora pokryty przez bufory innych wybranych anten                                                      |

> **Wskazówka interpretacyjna**: antena z `Pokrycie_unikalne_budynki = 0` mimo kategorii `niezbedna` oznacza, że jej budynki są pokryte przez inne anteny — można rozważyć jej usunięcie. Anteny z wysokim `Nakladanie_pct` i niskim `Pokrycie_unikalne_budynki` są kandydatami do optymalizacji.

#### Krok 11: Wizualizacja

| Kolor                 | Warstwa                                                     |
| --------------------- | ----------------------------------------------------------- |
| Niebieski             | Zasięg istniejących anten                                   |
| Zielony (jasny)       | Zasięg wybranych anten niezbędnych                          |
| Pomarańczowy          | Zasięg anten nadmiarowych (≥75% pokrycia przez inne anteny) |
| Fioletowy             | Zasięg anten o niskim znaczeniu (≤5 budynków / ≤N lokali)   |
| Czerwony (punkty)     | Centroidy budynków do pokrycia                              |
| Zielony (punkty)      | Centroidy wybranych anten niezbędnych                       |
| Pomarańczowy (punkty) | Centroidy anten nadmiarowych                                |
| Czarny (punkty)       | Budynki poza zasięgiem JAKIEJKOLWIEK potencjalnej anteny    |

#### Krok 11: Podsumowanie

Podsumowanie zawiera:

- Liczbę wszystkich budynków i ile pokrywają już istniejące anteny
- Wyniki ILP: liczbę wybranych anten z podziałem na kategorie
- **Scenariusz pełny** — wszystkie wybrane anteny
- **Scenariusz bez anten o niskim znaczeniu** — wybrane minus mało istotne

W trybie wagowania podsumowanie zawiera dodatkowo:

- Sumy LM i LU dla budynków pokrytych/niepokrytych
- Procent pokrycia lokali

---

## Wyniki

### Pliki wyjściowe

| Plik                                     | Opis                                                                                                                                                                             |
| ---------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `wybrane_anteny.csv`                     | Szczegółowe dane wybranych anten (ID, kategoria, pokrycie, unikalne pokrycie, nakładanie, opcjonalnie adres); separator `;`, kodowanie UTF-8 BOM — otwierać bezpośrednio w Excel |
| `Macierz_GIS.csv`                        | Macierz pokrycia (budynek–antena)                                                                                                                                                |
| `centroidy_*.shp`                        | Warstwy centroidów                                                                                                                                                               |
| `bufor_*.shp`                            | Warstwy buforów                                                                                                                                                                  |
| `budynki_niepokryte.shp`                 | Budynki wymagające nowych anten                                                                                                                                                  |
| `budynki_poza_zasiegiem.shp`             | Budynki poza zasięgiem potencjalnych anten                                                                                                                                       |
| `budynki_poza_bez_maloznaczacych.shp`    | Budynki poza zasięgiem (scenariusz bez mało istotnych)                                                                                                                           |
| `bufor_bez_maloznaczacych_dissolved.shp` | Połączony bufor anten (bez mało istotnych)                                                                                                                                       |

### Interpretacja wyników

#### Tryb klasyczny (limit anten = 0)

```
[LoRaWAN] Status solvera: Optimal | Wybrano anten: X
[LoRaWAN] Znaleziono dokładne globalne optimum.
[LoRaWAN] Pokrycie: B/B budynków (100.0%)
[LoRaWAN]
[LoRaWAN] Wybrano X nowych anten do maksymalnego pokrycia budynków, w tym:
[LoRaWAN]   - Y anten niezbędnych,
[LoRaWAN]   - Z anten potencjalnie nadmiarowych (>=75% pokrycia z zasięgiem innych anten),
[LoRaWAN]   - W anten o niskim znaczeniu (<= 5 budynków w zasięgu),
```

#### Tryb z budżetem i wagowaniem LM+LU

```
[LoRaWAN] Status solvera: Optimal | Suma pokrytych wag (LM+LU): 1234
[LoRaWAN] Znaleziono dokładne globalne optimum.
[LoRaWAN] Wybrano M anten (limit: max_antennas)
[LoRaWAN] Pokryto B2/B budynków
[LoRaWAN] Pokryto 1234/1500 lokali (82.3%)
[LoRaWAN]
[LoRaWAN] Wybrano M nowych anten (limit budżetu: max_antennas), w tym:
[LoRaWAN]   - Y anten niezbędnych,
[LoRaWAN]   ...
```

#### Informacja o timeout

```
[LoRaWAN] Status solvera: Not Solved | Wybrano anten: X
[LoRaWAN] UWAGA: Przekroczono limit czasu — używam najlepszego znalezionego rozwiązania.
```

Rozwiązanie jest poprawne (wykonalne), ale może mieć więcej anten niż optimum. Zwiększ limit czasu solvera lub zmniejsz problem (mniejszy bufor → mniej par w CSV).

---

## Uruchomienie skryptu

### W konsoli Python QGIS

1. Otwórz QGIS
2. Otwórz konsolę Python: `Wtyczki > Konsola Python`
3. Zainstaluj potrzebne biblioteki (jeśli pierwszy raz, w OSGeo4W Shell): `pip install "pulp>=2.7,<4" pandas`
4. Kliknij ikonę "Pokaż edytor"
5. Wczytaj plik `qgis/lorawan_set_cover_qgis.py`
6. Kliknij "Uruchom skrypt" (zielony trójkąt)
7. Wypełnij formularz konfiguracyjny:
   - Wskaż pliki wejściowe i folder wyjściowy
   - Ustaw parametry bufora
   - (Opcjonalnie) Włącz tryb wagowania i skonfiguruj LM/LU
   - (Opcjonalnie) Ustaw limit anten dla trybu z budżetem
   - Ustaw limit czasu solvera
8. Kliknij "Uruchom analizę"

---

## Rozwiązywanie problemów

| Problem                           | Rozwiązanie                                                                                             |
| --------------------------------- | ------------------------------------------------------------------------------------------------------- |
| `ModuleNotFoundError: pulp`       | Zainstaluj bibliotekę: `pip install "pulp>=2.7,<4"` w OSGeo4W Shell                                   |
| `Brak dostępnego solvera ILP`     | Zainstaluj PuLP < 4 (zawiera CBC) albo `pip install highspy`                                            |
| `ModuleNotFoundError: SetCoverPy` | Biblioteka SetCoverPy nie jest wymagana w v3 — ignoruj ten błąd jeśli pojawia się z innego skryptu      |
| Solver działa bardzo długo        | Zwiększ limit czasu LUB zmniejsz zasięg bufora (mniej par w CSV)                                        |
| Status solvera: `Not Solved`      | Solver przekroczył limit czasu — wynik jest użyteczny, ale może nie być optymalny. Zwiększ limit czasu. |
| Status solvera: `Infeasible`      | Sprzeczne ograniczenia — nie powinno się zdarzyć; sprawdź czy CSV nie jest pusty                        |
| Brak wyników Set Cover            | Sprawdź czy są budynki poza zasięgiem istniejących anten                                                |
| Błąd CRS                          | Upewnij się, że dane są w układzie metrycznym                                                           |
| Pusty plik CSV                    | Sprawdź nazwy kolumn ID w plikach wejściowych                                                           |
| Brak kolumn LM/LU                 | Wymagane tylko w trybie wagowania — sprawdź nazwy kolumn w dialogu                                      |

---

## Możliwe modyfikacje

### 1. Różne koszty anten

ILP w v3 naturalnie obsługuje różne koszty anten. Modyfikacja w funkcji `run_set_cover_ilp()`:

```python
# Zamiast kosztów jednostkowych:
prob += pulp.lpSum(x)

# Użyj kosztów z atrybutów:
costs = df.groupby("Antena_ID")["Koszt"].first()
prob += pulp.lpSum(costs[antenna_ids[i]] * x[i] for i in range(len(antenna_ids)))
```

### 2. Minimalna wymagana liczba anten na obszar

Można dodać ograniczenie geograficzne, np. każda dzielnica musi mieć co najmniej jedną antenę:

```python
# Dla każdego obszaru (zone_id) co najmniej jedna antena z tego obszaru
for zone_id, zone_antenna_indices in zone_antennas.items():
    prob += pulp.lpSum(x[i] for i in zone_antenna_indices) >= 1
```

### 3. Maksymalizacja pokrycia budynków bez wagowania

Tryb z budżetem (`max_antennas > 0`) bez włączonego wagowania używa wag = 1 dla wszystkich budynków, co oznacza **maksymalizację liczby pokrytych budynków** przy ograniczonej liczbie anten.

### 4. Uwzględnienie przeszkód terenowych

- Import modelu terenu (DEM)
- Analiza widoczności (viewshed) zamiast prostego bufora
- Wymaga rozszerzenia o algorytmy propagacji sygnału LoRa

### 5. Iteracyjna analiza wrażliwości

```python
# Uruchomienie dla różnych promieni bufora i porównanie wyników
for radius in [300, 400, 500, 600]:
    config['buffer_pot_value'] = str(radius)
    selected = run_analysis(config)
    print(f"Promień {radius} m → {len(selected)} anten")
```

---

## Autorzy i licencja

Autor: Jacek Malicki. Licencja: MIT (patrz plik `LICENSE`).

Projekt wykorzystuje bibliotekę PuLP udostępnianą na licencji MIT, z wbudowanym solwerem CBC (COIN-BC) udostępnianym na licencji Eclipse Public License.
Licencje dotyczą wyłącznie tych bibliotek i pozostają własnością ich autorów.
