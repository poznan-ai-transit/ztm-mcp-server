stworzyć .env w tym folderze i dodać do niego klucz:
OPENROUTER_API_KEY=klucz

Następnie:
```bash
cd src/tests

npx promptfoo eval --no-cache --env-file -o results.json
```
