// promptfoo provider: tool-calling loop (OpenRouter + MCP server), returns metadata.toolCalls.
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
    this.model = options.config.model;
    this.providerId = options.label || options.id;
    this.client = null;
    this.tools = null;
  }

  id() { return this.providerId; }

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

  async complete(messages) {
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
          body: JSON.stringify({ model: this.model, messages, tools: this.tools }),
          signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS),
        });
      } catch (e) {
        last = { error: { code: 504, message: `Request failed: ${e.message || e}` } };
        await sleep(1000 * 2 ** attempt);
        continue;
      }
      const data = await res.json().catch(() => ({ error: { code: res.status, message: 'non-JSON response' } }));
      if (!data.error && res.ok) return data;
      last = data;
      const code = Number(data.error?.code || res.status);
      if (code !== 429 && code < 500) break;
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

    for (let step = 0; step < MAX_STEPS; step++) {
      const data = await this.complete(messages);
      const msg = data?.choices?.[0]?.message;
      if (!msg) return { error: `OpenRouter: ${JSON.stringify(data?.error || data)}`, metadata: { toolCalls: calls } };
      if (!msg.tool_calls?.length) return { output: msg.content || '', metadata: { toolCalls: calls } };

      const toolMessages = [];
      const cleanCalls = [];
      for (const tc of msg.tool_calls) {
        let args = {};
        let result;
        try {
          args = JSON.parse(tc.function.arguments || '{}');
          result = await this.callTool(tc.function.name, args);
        } catch (e) {
          result = { error: `Invalid JSON arguments: ${e.message}` };
        }
        calls.push({ name: tc.function.name, args, result });
        cleanCalls.push({ ...tc, function: { ...tc.function, arguments: JSON.stringify(args) } });
        toolMessages.push({
          role: 'tool',
          tool_call_id: tc.id,
          content: typeof result === 'string' ? result : JSON.stringify(result),
        });
      }
      messages.push({ ...msg, tool_calls: cleanCalls }, ...toolMessages);
    }

    return { error: `MAX_STEPS (${MAX_STEPS}) exceeded`, metadata: { toolCalls: calls } };
  }

  async cleanup() {
    try { await this.client?.close(); } catch { /* ignore */ }
  }
}

module.exports = MultiStepProvider;
