# Agent evals

LLM agent tool-use evaluations (promptfoo).

```bash
cd evals
npm install
echo "OPENROUTER_API_KEY=your-key" > .env   # MCP server must run on localhost:8000

npm run eval:single && npm run report:single   # single-turn suite
npm run eval:multi && npm run report:multi     # multi-step suite

npm run eval:single:repeat                     # single-turn, 5 repeats
npm run eval:multi:repeat                      # multi-step, 3 repeats

# one model / one scenario
npx promptfoo eval -c multi-step/promptfooconfig.yaml --no-cache \
  --filter-providers haiku --filter-pattern '^M3' -o results/multi-step.json

npx promptfoo view                             # results in the browser
```
