// Multi-provider LLM client with fallback — mirrors app/llm.py.
// Order: NVIDIA NIM models (build.nvidia.com) first, then OpenRouter models.
// Each (provider, model) is tried in turn; brief 5xx/connection blips get one retry,
// then it falls through to the next model so a single bad provider/model/key never
// breaks the request. Used by /api/details (complete) and /api/search (tool loop).
import OpenAI from "openai";

const TIMEOUT_MS = (Number(process.env.LLM_TIMEOUT_SEC) || 60) * 1000;
const RETRY_STATUS = new Set([500, 502, 503, 504]);

const _clients = {};
function clientFor(provider) {
  if (!_clients[provider]) {
    _clients[provider] = provider === "nim"
      ? new OpenAI({
          apiKey: process.env.NIM_API_KEY,
          baseURL: process.env.NIM_BASE_URL || "https://integrate.api.nvidia.com/v1",
          timeout: TIMEOUT_MS, maxRetries: 0,
        })
      : new OpenAI({
          apiKey: process.env.OPENROUTER_API_KEY,
          baseURL: process.env.OPENROUTER_BASE_URL || "https://openrouter.ai/api/v1",
          timeout: TIMEOUT_MS, maxRetries: 0,
        });
  }
  return _clients[provider];
}

function csv(v) {
  return (v || "").split(",").map(s => s.trim()).filter(Boolean);
}

// Ordered [provider, model] attempts: NIM first (if keyed), then OpenRouter.
// The web service gets the OpenRouter list via OPENROUTER_MODEL (docker-compose maps
// it from ${OPENROUTER_MODELS}); fall back to OPENROUTER_MODELS just in case.
function modelChain() {
  const chain = [];
  const nimModels = csv(process.env.NIM_MODELS);
  if (process.env.NIM_API_KEY && nimModels.length) {
    for (const m of nimModels) chain.push(["nim", m]);
  }
  for (const m of csv(process.env.OPENROUTER_MODEL || process.env.OPENROUTER_MODELS)) {
    chain.push(["openrouter", m]);
  }
  return chain;
}

// Low-level: returns { resp, model }. Throws only when every model is exhausted.
export async function chatCreate(params) {
  const chain = modelChain();
  if (!chain.length) throw new Error("no LLM models configured");
  let lastErr;
  for (const [provider, model] of chain) {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        const resp = await clientFor(provider).chat.completions.create({ ...params, model });
        return { resp, model };
      } catch (e) {
        lastErr = e;
        const status = e?.status ?? e?.statusCode;
        if (RETRY_STATUS.has(status) && attempt === 0) {
          await new Promise(r => setTimeout(r, 1500));
          continue;
        }
        console.warn(`[llm] ${provider}/${model} failed (status=${status ?? e?.name}) — next model`);
        break;
      }
    }
  }
  throw lastErr || new Error("all models failed");
}

// Convenience: single system+user completion → { content, model }.
// temperature: lower = more deterministic. json: request response_format=json_object.
export async function chatComplete({ system, user, max_tokens = 800, temperature, json = false }) {
  const params = {
    max_tokens,
    messages: [
      { role: "system", content: system },
      { role: "user", content: user },
    ],
  };
  if (temperature != null) params.temperature = temperature;
  if (json) params.response_format = { type: "json_object" };
  const { resp, model } = await chatCreate(params);
  return { content: (resp.choices[0]?.message?.content || "").trim(), model };
}
