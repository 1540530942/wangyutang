# 0003 Single tool call protocol

## 背景

Robot control needs deterministic, auditable decisions. Multiple tool calls in one assistant turn make validation and safety ordering harder.

## 候选方案

- Allow the model to emit arbitrary multi-tool plans per turn.
- Require one ReAct turn to contain one tool call.

## 最终决策

One ReAct turn equals one canonical tool call.

## 决策依据

Single-call turns allow validation, safety checks, observations, dispatch, and tool results to be recorded step-by-step in `DecisionEnvelope`.

## 影响

Tool call adaptation and validation live under `tools/`; the ReAct loop lives under `harness/react_loop.py`.

## 状态

Accepted.
