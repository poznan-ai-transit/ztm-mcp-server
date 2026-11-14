Create a `.env` file in this directory and add the following key:
OPENROUTER_API_KEY=your-key

Then run:
```bash
cd src/tests
npm install
npx promptfoo eval --no-cache -o results.json
```

The configuration runs the same scenarios against three OpenRouter models.
Scenarios are grouped by category in `scenarios/`.

To measure stability, run several repetitions:
```bash
npx promptfoo eval --no-cache --repeat 5 -o results.json
node failures.js results.json
```

The text report first shows the pass rate for each model and category, followed
by details of failed cases. Results can also be viewed in the UI:
```bash
npx promptfoo view
```
