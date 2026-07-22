---
name: create-jianercore-plugin
description: Create, modify, test, and validate new-style JianerCore or JianerBot plugins using PluginMetadata, PluginManager, the built-in Alconna command layer, raw on_message dispatch, or setup hooks. Use whenever a user asks to scaffold, implement, migrate, repair, or review a JianerCore plugin, a jianer-bot plugin, a plugins/*.py module, or a jianerbot-plugin-* extension. Do not use for Codex plugins or unrelated plugin ecosystems.
---

# Create JianerCore Plugin

Create source-faithful, loadable JianerCore plugins and prove that their behavior works. Prefer the target project's installed or local JianerCore source over remembered APIs.

## Required Reference

Read [references/plugin-contract.md](references/plugin-contract.md) completely before creating or changing plugin code. It records discovery rules and runtime edge cases that are easy to miss.

For adapter-specific actions, non-message events, or a target checkout that differs from the reference, inspect that checkout's implementations before writing code. Never generalize OneBot behavior to Milky, Feishu, or Kritor without verification.

## Workflow

1. Inspect the target repository instructions, Git status, Python environment, existing `plugins/` layout, dependency files, and current plugin-loading call.
2. Resolve the requested trigger, response, state, dependencies, protocols, output path, and tests from local context. Ask one concise question only when a missing choice would materially change behavior.
3. Choose the smallest viable shape:
   - Default to a new-style, single-file Alconna command plugin.
   - Use raw `on_message(event, actions)` for message-wide or custom dispatch logic.
   - Use a synchronous `setup(client, manager)` hook for notice/request events and verify the selected event exists for every required adapter.
   - Use a directory plugin only when multiple files are necessary; its entry is `setup.py`, and helper imports require a real load test.
4. For a new baseline plugin, run `scripts/scaffold_plugin.py` from this skill. It copies the bundled templates and refuses to overwrite existing paths. If a plugin already exists, inspect and patch it instead.
5. Customize the generated handler and tests to implement the user's actual behavior. Keep metadata literal and top-level. Put Python packages in the target dependency file; put only Jianer plugin IDs in `requires`.
6. Validate syntax, metadata discovery, dependency loading, matching and non-matching dispatch, actual send arguments, and every requested group/private or protocol branch.
7. Follow the target repository's required checks and commit policy. Do not push, tag, publish, or touch unrelated changes unless explicitly authorized.

## Scaffold Command

Use the target project's Python interpreter. Treat `SKILL_DIR` below as the directory containing this `SKILL.md`:

```text
python <SKILL_DIR>/scripts/scaffold_plugin.py ping \
  --plugins-dir <PROJECT>/plugins \
  --mode alconna \
  --command ping \
  --reply pong \
  --description "Reply pong to ping" \
  --usage "Send ping" \
  --tests-dir <PROJECT>/tests
```

Accept a short name such as `ping` or a canonical ID such as `jianerbot-plugin-ping`. Use `--layout directory` only for an explicitly justified directory entry. Omit `--tests-dir` only when the target has no test suite, then create an equivalent isolated smoke test manually.

The scaffolder intentionally accepts only a no-whitespace command token. For `echo <text>`, multiple arguments, options, subcommands, or notice events, scaffold a baseline and then implement the handler using the patterns in the required reference.

## Validation

Run all applicable layers with the target interpreter:

```text
python -m py_compile <PLUGIN_ENTRY>
python <SKILL_DIR>/scripts/validate_plugin.py <PLUGIN_PATH>
python <SKILL_DIR>/scripts/validate_plugin.py <PLUGIN_PATH> --load
python -m pytest <FOCUSED_TEST_PATH> -q
```

When a plugin depends on sibling third-party plugins, pass their discovery root with `--plugins-dir <PROJECT>/plugins` during the load check. A successful import alone is insufficient: dispatch a matching event and assert the sent message and target, then dispatch a non-match and assert it remains unhandled.

If the target environment cannot import `jianer`, complete static validation and report the skipped dynamic check precisely. Do not claim runtime compatibility from static checks alone.

## Completion Contract

Report:

- exact created or modified paths;
- canonical plugin ID, layout, trigger mode, and dependencies;
- the host `Client.load_plugins(...)` path;
- syntax, load, dispatch, focused-test, and broader-test results;
- any adapter, event, or environment limitation still unverified.
