// Custom promptfoo provider: runs a tool-calling loop (OpenRouter chat API + real MCP server).
// The model gets ONE user question and decides the tool sequence itself.
// Returns metadata.toolCalls = [{ name, args, result }] used by the assertions in assertions.js.
//
// Provider config keys:
//   model           OpenRouter model id (required)
//   singleToolCall  true for models/providers that reject more than one tool call per
//                   assistant message (e.g. llama 3.1 8B on CoreWeave). Parallel calls are
//                   then split into separate assistant messages in the history.
//   temperature     optional sampling temperature
const { Client } = require('@modelcontextprotocol/sdk/client/index.js');
const { StreamableHTTPClientTransport } = require('@modelcontextprotocol/sdk/client/streamableHttp.js');

const MCP_URL = process.env.MCP_URL || 'http://localhost:8000/mcp';
const OPENROUTER_URL = 'https://openrouter.ai/api/v1/chat/completions';
const MAX_STEPS = 8;
const MAX_RETRIES = 3;
const REQUEST_TIMEOUT_MS = 90_000;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

class MultiStepProvider {
  constructor(options) {
    this.config = options.config || {};
    this.model = this.config.model;
    this.providerId = options.label || options.id || `multistep:${this.model}`;
    this.client = null;
    this.tools = null;
  }

  id() { return this.providerId; }

  // One connection to the MCP server per provider; tools are fetched once.
  async init() {
    if (this.client) return;
    const client = new Client({ name: 'promptfoo-multistep', version: '1.0.0' });
    await client.connect(new StreamableHTTPClientTransport(new URL(MCP_URL)));
    const { tools } = await client.listTools();
    this.tools = tools.map((t) => ({
      type: 'function',
      function: { name: t.name, description: t.description, parameters: t.inputSchema },
    }));
    this.client = client;
  }

  // Chat completion with retries on rate limits and transient upstream errors.
  // OpenRouter may return HTTP 200 with an `error` body, so both are checked.
  async complete(messages) {
    const body = { model: this.model, messages, tools: this.tools };
    if (this.config.singleToolCall) body.parallel_tool_calls = false;
    if (this.config.temperature !== undefined) body.temperature = this.config.temperature;

    let last;
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      let res;
      try {
        res = await fetch(OPENROUTER_URL, {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${process.env.OPENROUTER_API_KEY}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify(body),
          // Without a timeout one stuck request can hang the whole eval run.
          signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        });
      } catch (e) {
        // Timeouts and network errors are treated like transient upstream errors.
        last = { error: { code: 504, message: `Request failed: ${e.message || e}` } };
        await sleep(1000 * 2 ** attempt);
        continue;
      }
      const data = await res.json().catch(() => ({ error: { code: res.status, message: 'non-JSON response' } }));
      const code = Number(data.error?.code || res.status);
      const transient = code === 429 || code >= 500;
      if (!data.error && res.ok) return data;
      last = data;
      if (!transient) break;
      await sleep(1000 * 2 ** attempt);
    }
    return last;
  }

  async callTool(name, args) {
    try {
      const r = await this.client.callTool({ name, arguments: args });
      const text = (r.content || []).filter((c) => c.type === 'text').map((c) => c.text).join('\n');
      let result;
      try { result = JSON.parse(text); } catch { result = text; }
      return r.isError ? { error: result } : result;
    } catch (e) {
      return { error: String(e.message || e) };
    }
  }

  async callApi(prompt) {
    await this.init();
    const messages = JSON.parse(prompt);
    const calls = [];
    const meta = (extra = {}) => ({ toolCalls: calls, sequence: calls.map((c) => c.name).join(' > '), ...extra });

    for (let step = 0; step < MAX_STEPS; step++) {
      const data = await this.complete(messages);
      const msg = data?.choices?.[0]?.message;
      if (!msg) return { error: `OpenRouter: ${JSON.stringify(data?.error || data)}`, metadata: meta({ steps: step + 1 }) };

      // Done: the model returned text without making tool calls.
      if (!msg.tool_calls?.length) {
        return { output: msg.content || '', metadata: meta({ steps: step + 1 }) };
      }

      // If the provider still returned several calls, keep them all but split the history.
      const results = [];
      for (const tc of msg.tool_calls) {
        let args = {};
        let argsError;
        try { args = JSON.parse(tc.function.arguments || '{}'); } catch (e) { argsError = String(e.message); }

        // Malformed arguments are reported back to the model instead of crashing the history.
        const result = argsError
          ? { error: `Invalid JSON arguments: ${argsError}` }
          : await this.callTool(tc.function.name, args);

        calls.push({ name: tc.function.name, args, result, ...(argsError && { rawArgs: tc.function.arguments }) });
        // Re-serialise arguments so the history always holds valid JSON.
        const cleanCall = { ...tc, function: { ...tc.function, arguments: JSON.stringify(args) } };
        results.push({ call: cleanCall, content: typeof result === 'string' ? result : JSON.stringify(result) });
      }

      const toolMsg = (r) => ({ role: 'tool', tool_call_id: r.call.id, content: r.content });
      if (this.config.singleToolCall) {
        results.forEach((r, i) => {
          messages.push({ role: 'assistant', content: i === 0 ? msg.content || '' : '', tool_calls: [r.call] });
          messages.push(toolMsg(r));
        });
      } else {
        messages.push({ ...msg, tool_calls: results.map((r) => r.call) });
        results.forEach((r) => messages.push(toolMsg(r)));
      }
    }

    return { error: `MAX_STEPS (${MAX_STEPS}) exceeded`, metadata: meta({ steps: MAX_STEPS }) };
  }

  async cleanup() {
    try { await this.client?.close(); } catch { /* ignore */ }
  }
}

module.exports = MultiStepProvider;
