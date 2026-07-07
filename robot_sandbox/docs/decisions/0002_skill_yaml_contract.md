# 0002 Skill YAML contract

## 背景

Robot skills need a shared contract for tool schema generation, validation, safety preconditions, and dispatch routing.

## 候选方案

- Derive skills only from the action service catalog.
- Keep a local YAML registry as the primary contract with catalog fallback.

## 最终决策

Use `skills/registry.yaml` as the primary skill contract and allow catalog fallback when needed.

## 决策依据

The YAML registry can encode tool type, route, aliases, duration limits, risk level, and preconditions in a stable package-local format.

## 影响

Skill loading lives under `skills/registry.py`; catalog compatibility lives under `skills/catalog_loader.py`.

## 状态

Accepted.
