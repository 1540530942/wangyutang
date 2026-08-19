# DashScope Compatible Model Probe - 2026-08-19

This note records the live probe results for DashScope OpenAI-compatible chat
models using the `common_api_manager/.env` configuration.

## Configuration

The probe used the existing DashScope compatible-mode endpoint:

```text
DASHSCOPE_COMPATIBLE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
```

The API key was read from `DASHSCOPE_LLM_API_KEY` / `DASHSCOPE_API_KEY`. The key
value was not printed or committed.

## Probe Method

Each model was called through:

```text
POST /chat/completions
```

with a minimal prompt asking for `pong`.

## Results

| Model | Result | Notes |
|---|---|---|
| `qwen3.6-flash` | Partially works | Returned `pong` once, but repeated probes also hit `RemoteDisconnected: Remote end closed connection without response`. Treat as available but unstable through the current route. |
| `qwen3.7-flash` | Works | Returned `pong`; response included `reasoning_content`. |
| `Moonshot-Kimi-K2-Instruct` | Works | Returned `pong` through DashScope compatible mode. Model name is case-sensitive. |
| `moonshot-kimi-k2-instruct` | Fails | Returned 404 `model_not_found`. |
| `kimi-k2.6` | Partially works | One probe returned successfully, later probes hit `RemoteDisconnected`. |
| `kimi-k2.5` | Fails in current probe | Hit `RemoteDisconnected` through the current route. |
| `kimi-k2-thinking` | Fails in current probe | Hit `RemoteDisconnected` through the current route. |
| `kimi-k2.7-code` | Fails in current probe | Hit `RemoteDisconnected` through the current route. |

## Recommendation

For `common_api_manager` compatible-mode text calls:

- Prefer `qwen3.7-flash` when a Qwen Flash model is needed.
- Use `Moonshot-Kimi-K2-Instruct` for Kimi via DashScope compatible mode.
- Keep `qwen3.6-flash` and `kimi-k2.6` behind retry/fallback logic if used.
- Do not lowercase `Moonshot-Kimi-K2-Instruct`; DashScope treated the lowercase
  variant as a different, unavailable model.

## Implementation Note

`online_backup/test_qwen.py` currently uses DashScope native SDK calls
(`dashscope.Generation.call`). To test or use the models above from that script,
add an OpenAI-compatible `/chat/completions` branch instead of routing them
through the native SDK path.
