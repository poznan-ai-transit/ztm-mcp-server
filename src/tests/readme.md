stworzyć .env w tym folderze i dodać do niego klucz:
OPENROUTER_API_KEY=klucz

Następnie:
```bash
cd src/tests
npm install
npx promptfoo eval --no-cache -o results.json
```
