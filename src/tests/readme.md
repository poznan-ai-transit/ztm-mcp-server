stworzyć .env w tym folderze i dodać do niego klucz:
OPENROUTER_API_KEY=klucz

Następnie:
```bash
cd src/tests
npm install
npx promptfoo eval --no-cache -o results.json
```

Konfiguracja uruchamia te same scenariusze na trzech modelach OpenRouter.
Scenariusze są podzielone według kategorii w `scenarios/`.

Do pomiaru stabilności wykonujemy kilka powtórzeń:
```bash
npx promptfoo eval --no-cache --repeat 5 -o results.json
node failures.js results.json
```

Raport tekstowy pokazuje na początku pass rate per model i kategorię, a następnie
szczegóły niezaliczonych przypadków. Wyniki można również przeglądać w UI:
```bash
npx promptfoo view
```
