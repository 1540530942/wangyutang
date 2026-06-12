# 0001 ReAct default paradigm

## 背景

`audio_recognition` has evolved from ASR routing into a robot agent runtime that records decisions in envelopes and executes bounded tool calls.

## 候选方案

- Keep direct rule/planner routing as the main path.
- Use ReAct tool calling as the main path and keep rule planning as legacy compatibility.

## 最终决策

Use the ReAct harness as the default decision path.

## 决策依据

ReAct provides explicit observations, validation, safety checks, dispatch results, and replayable traces.

## 影响

New runtime code should live under `agent/`, `harness/`, `tools/`, `safety/`, `storage/`, and `core/`. Old rule planning lives under `legacy/`.

## 状态

Accepted.
