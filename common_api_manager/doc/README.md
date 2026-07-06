# common_api_manager docs

`common_api_manager` is the public workbench under:

```text
https://www.wangyutang.cn/common
```

It owns public pages and common API adapters. Feature areas are separated as
sub-workspaces so the URL, static files, backend modules, and tests stay aligned.

| Area | Public page | API namespace | Code ownership |
|---|---|---|---|
| Model Studio | `/common/model-studio` | `/common/api/model-studio/*` | `modules/model_studio_catalog/`, `static/model_studio*` |
| Robot Skills | `/common/robot-skills` | uses `/action/api/*` and `/camera/api/*` | `static/robot_skills*`; execution remains in `action_move` |

The old public names are intentionally not kept:

| Old | New |
|---|---|
| `/common/lab` | `/common/model-studio` |
| `/common/api/lab/*` | `/common/api/model-studio/*` |
| `/common/function_center` | `/common/robot-skills` |

Recommended extension rule:

1. Add a new page as `/common/<area-name>`.
2. Put static files in `static/<area_name>.html/css/js`.
3. Put backend APIs in `modules/<area_name>_*` or `modules/<area_name>/` when that area has server state.
4. Add focused tests under `tests/test_<area_name>*.py`.
5. Record the evolution and verification result in this `doc/` directory.

Related documents:

- [architecture.md](architecture.md)
- [model-studio.md](model-studio.md)
- [robot-skills.md](robot-skills.md)
- [function-center-migration.md](function-center-migration.md)
- [verification-records.md](verification-records.md)
