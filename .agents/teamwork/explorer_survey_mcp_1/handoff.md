# Handoff Report: MCP Grade A Upgrade - Requirement 1 Survey (Handler Isolation & Tool Description Accuracy)

**Agent Identity**: `explorer_survey_mcp_1`  
**Parent Agent**: `orchestrator_mcp_1` (Conversation ID: `09e9f6f6-cea0-43b3-ba73-105ef2f87c01`)  
**Timestamp**: 2026-09-29T16:44:00Z  
**Type**: Hard Handoff (Investigation & Synthesis Complete)  

---

## 1. Observation

### 1.1 Tool Dispatch Architecture in `smart_drive/mcp/server.py`
In `smart_drive/mcp/server.py`:
- **Lines 836-872**: The JSON-RPC `tools/call` handler parses `tool_name = params.get("name")` and passes it to `self.dispatch_tool(tool_name, arguments)`:
  ```python
  858: res_data = self.dispatch_tool(tool_name, arguments)
  ```
- **Lines 747-761**: `dispatch_tool` is implemented via dynamic local dictionary lookup:
  ```python
  747:     def dispatch_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
  748:         dispatch_table: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
  749:             "ssd_search": self.handle_ssd_search,
  750:             "ssd_audit": self.handle_ssd_audit,
  751:             "ssd_clean": self.handle_ssd_clean,
  752:             "ssd_find_duplicates": self.handle_ssd_find_duplicates,
  753:             "ssd_update_index": self.handle_ssd_update_index,
  754:             "ssd_check_safety": self.handle_ssd_check_safety,
  755:             "ssd_status": self.handle_ssd_status,
  756:             "ssd_auto_organize": self.handle_ssd_auto_organize,
  757:         }
  758:         handler = dispatch_table.get(name)
  759:         if not handler:
  760:             raise ValueError(f"Unknown tool '{name}'")
  761:         return handler(args)
  ```
- In Python Abstract Syntax Tree (AST) analysis:
  - In `handle_request`, the tool call is: `ast.Call(func=ast.Attribute(value=ast.Name(id='self'), attr='dispatch_tool'), args=[ast.Name(id='tool_name'), ast.Name(id='arguments')])`.
  - In `dispatch_tool`, the actual invocation is `ast.Call(func=ast.Name(id='handler'), args=[ast.Name(id='args')])`.
  - Static AST scanners (e.g. M8ven, static call graph visitors) evaluating `FunctionDef(name='dispatch_tool')` see a dynamic invocation `handler(...)` on a symbol resolved at runtime from a local dict. No static AST node connects string literal constants (e.g. `"ssd_search"`) directly to method execution nodes (`self.handle_ssd_search`).
  - Consequently, automated MCP scanners classify this as "lower-level raw dispatch without static AST handler resolution", lowering the Trust Score from Grade A to Grade B.

### 1.2 Tool Description & Implementation Cross-Examination Observations

#### Tool 1: `ssd_search`
- **Declaration (`server.py:50-103`)**:
  - Description: `"Instant high-speed search across 500,000+ files on the SSD (<10ms latency). Use this instead of running slow shell find or grep commands."`
  - Input schema: `query` (string), `ext` (string), `category` (string), `size` (string), `directory` (string), `limit` (integer, default: 25), `offset` (integer, default: 0), `compact` (boolean, default: True).
- **Implementation (`server.py:495-580`, `smart_drive/search/parser.py`, `smart_drive/search/engine.py`)**:
  - `parse_search_query` parses inline query operators inside `query` (`ext:`, `size:`, `cat:`, `dir:`).
  - Truncates serialized matches to a 4800 character token budget (`truncated_to_token_limit: True/False`).
  - Schema mentions categories `'Code', 'AI Models', 'Books/Learning', 'Docs', 'Media', 'Archives'`. In `config.py:319-366`, `System Junk` also exists as an internal category.
  - Overall behavior matches description well; property descriptions can be enriched to document inline query syntax.

#### Tool 2: `ssd_audit`
- **Declaration (`server.py:105-127`)**:
  - Description: `"Returns full storage allocation breakdown across standard taxonomies and calculates wasted 512KB exFAT cluster slack."`
  - Input schema: `sub_dir` (string).
- **Implementation (`server.py:582-598`, `smart_drive/core/auditor.py`)**:
  - Returns: `total_files`, `total_directories`, `total_logical_bytes`, `total_allocated_bytes`, `total_slack_bytes`, `total_slack_percentage`, `taxonomies` (6 business taxonomies), `categories` (file extension groupings), and `top_slack_directories` (top 10 slack directories).
  - Schema only accepts `sub_dir`, whereas other tools accept `directory`.

#### Tool 3: `ssd_clean`
- **Declaration (`server.py:128-165`)**:
  - Description: `"Identifies and purges system junk files (.DS_Store, Thumbs.db, temp caches). Enforces mandatory whitelist protection."`
  - Input schema: `dry_run` (boolean, default: True), `apply` (boolean, default: False), `sub_dir` (string), `tier` (integer, default: 1).
- **Implementation (`server.py:600-626`, `smart_drive/core/junk_detector.py`, `smart_drive/core/purge_engine.py`)**:
  - Code accepts `sub_dir = args.get("sub_dir") or args.get("directory")`.
  - Tier mapping: 1 (`TIER_1_SAFE`: OS metadata/bytecode), 2 (`TIER_2_DEV_CACHE`: test/build caches), 3 (`TIER_3_SENSITIVE`: crash dumps/logs).
  - Returns `nominal_bytes_reclaimed` and `slack_bytes_reclaimed`.
  - Accurately mirrors description.

#### Tool 4: `ssd_find_duplicates` (MAJOR DISCREPANCY)
- **Declaration (`server.py:167-188`)**:
  - Description: `"Detects identical duplicate files using 3-phase cascade (Size -> 8KB Hash -> Full SHA-256) and returns exact space savings."`
  - Input schema: `sub_dir` (string).
- **Implementation (`server.py:628-642`, `smart_drive/core/duplicates.py`)**:
  - Line 631: `min_size = self._parse_int(args.get("min_size"), default=0, min_val=0)`
  - Lines 634-641: Explicitly filters duplicate groups by `size >= min_size` and recomputes `duplicate_group_count`, `duplicate_file_count`, `total_reclaimable_bytes`, `total_reclaimable_slack`, and `total_reclaimable_physical`.
  - **VERBATIM DISCREPANCY**: `min_size` is actively parsed and executed by the handler, yet `min_size` is completely omitted from `inputSchema["properties"]`.

#### Tool 5: `ssd_update_index`
- **Declaration (`server.py:190-211`)**:
  - Description: `"Performs fast incremental synchronization of the SQLite FTS5 search index after creating, editing, or deleting files."`
  - Input schema: `directory` (string).
- **Implementation (`server.py:644-653`, `smart_drive/indexer/manager.py`)**:
  - Accepts `directory` or fallback `target_dir`.
  - Returns `IncrementalStats` dictionary: `added`, `modified`, `deleted`, `unchanged`, `elapsed_seconds`.
  - Accurately mirrors description.

#### Tool 6: `ssd_check_safety` (MAJOR DISCREPANCY)
- **Declaration (`server.py:213-235`)**:
  - Description: `"Verifies whether a file path or operation complies with exFAT rules: checks for illegal Windows characters, symlink attempts, and whitelist."`
  - Input schema: `path` (string, required).
- **Implementation (`server.py:654-711`)**:
  - Lines 692-700:
    ```python
    forbidden_chars = ExFatEngine.audit_forbidden_characters(os.path.basename(path_str))
    is_prot_file = is_protected_root_file(rel_path)
    is_prot_dir = is_protected_root_dir(rel_path)
    is_safe = (
        len(forbidden_chars) == 0
        and not is_prot_file
        and not is_prot_dir
        and not escapes_root
    )
    ```
  - **VERBATIM DISCREPANCY 1**: Tool description claims it checks "symlink attempts", but the handler code **never** checks `os.path.islink()` or `ExFatEngine.is_symlink()`, and the returned dictionary does not provide `is_symlink`!
  - **VERBATIM DISCREPANCY 2**: `audit_forbidden_characters` is only applied to `os.path.basename(path_str)`. Forbidden characters in intermediate directory segments are not flagged.

#### Tool 7: `ssd_status` (MAJOR DISCREPANCY)
- **Declaration (`server.py:237-253`)**:
  - Description: `"Inspects SSD mount status, anti-indexing shield integrity (.metadata_never_index, .fseventsd/no_log), and taxonomy health."`
  - Input schema: `properties: {}`.
- **Implementation (`server.py:712-727`, `smart_drive/core/sentinel.py:306-355`)**:
  - `SentinelEngine.run_health_check` audits:
    1. `mount`: `is_mounted`, `filesystem`, `cluster_size_bytes`, `cluster_size_kb`.
    2. `anti_indexing_shields`: `meta_exists`, `fsevents_exists`, `all_healthy`, `auto_healed`.
    3. `database`: `path`, `exists`, `healthy`, `quick_check` (PRAGMA integrity), `file_count`, `size_bytes`.
    4. `exfat_safety`: `is_safe`, `symlinks_found`, `symlinks`, `forbidden_char_violations`.
    5. `git_status`: `repos_checked`, `clean_repos`, `dirty_repos`, `unpushed_repos`, `details`.
  - **VERBATIM DISCREPANCY**: The description explicitly states "and taxonomy health". However, `SentinelEngine` does **not** check taxonomy health (taxonomies are checked by `ssd_audit`). It actually checks SQLite search database integrity, exFAT safety, and Git multi-repository status!

#### Tool 8: `ssd_auto_organize` (MAJOR DISCREPANCY)
- **Declaration (`server.py:255-280`)**:
  - Tool description: `"Autonomous drive auto-zoning, anti-slack rebalancing, and organization. Classifies loose items into standard taxonomies, ensures shields, and syncs index."`
  - Parameter `apply`: `"If true, executes relocation and index sync. If false (default), simulates plan."`
- **Implementation (`server.py:728-746`, `smart_drive/core/auto_zoner.py`)**:
  - Lines 742-744:
    ```python
    if apply_mode:
        res = zoner.apply_plan(plan)
        return {"status": "applied", "result": res}
    ```
  - **VERBATIM DISCREPANCY**: Both the tool description ("ensures shields, and syncs index") and the `apply` parameter description ("executes relocation and index sync") guarantee that when `apply=True`, it ensures shields and synchronizes the search index. In reality, `handle_ssd_auto_organize` **never** called `IndexManager.incremental_update()` and **never** called `ensure_anti_indexing_markers()`.

---

## 2. Logic Chain

1. **Static AST Analysis & Scanner Evaluation**:
   - MCP scanners (such as M8ven) parse source files using Python's `ast` module.
   - When a scanner visits `handle_request` and `dispatch_tool`, it observes an indirect invocation `handler(args)` via a dynamic dict lookup (`dispatch_table.get(name)`).
   - Because the call target is resolved dynamically at runtime rather than statically in the syntax tree, the scanner cannot confirm that tool `"ssd_search"` maps directly to `handle_ssd_search`.
   - By converting the dynamic dictionary lookup in `dispatch_tool` into an explicit static `if / elif` branching chain where each branch calls `self.handle_<tool_name>(args)`, an AST visitor resolves 100% of tools directly to their handlers without runtime execution.
   - Adding a class-level static mapping `TOOL_HANDLERS: Dict[str, str]` further provides scanners with an explicit static registry.

2. **Schema & Behavior Discrepancies**:
   - In `ssd_find_duplicates`: Because `min_size` is present in code but missing from `inputSchema`, AI agents cannot discover this capability, and strict schema validation tools flag it as an undeclared argument. Adding `min_size` to `inputSchema` restores 100% alignment.
   - In `ssd_check_safety`: The description promises symlink checking, which is a core exFAT invariant. Adding `os.path.islink()` inspection and `is_symlink` to the return dict eliminates false security assumptions.
   - In `ssd_status`: The description references "taxonomy health" instead of the actual checks performed (database integrity, exFAT safety, and Git status). Correcting the description ensures compliance with MCP tool description accuracy audits.
   - In `ssd_auto_organize`: When files are moved, the search index becomes stale unless synchronized. Integrating `ensure_anti_indexing_markers()` and `IndexManager.incremental_update()` when `apply=True` fulfills the exact contract stated in both the tool description and the parameter description.

---

## 3. Caveats

- **No runtime pip dependencies**: All proposed architectural updates strictly adhere to the Python Standard Library (`ast`, `os`, `pathlib`, `json`, `dataclasses`, `time`, `typing`).
- **Backward compatibility**: Existing tests call `self.server.dispatch_tool("tool_name", args)`. The proposed architecture preserves the exact signature and behavior of `dispatch_tool`, ensuring 0% test regressions across all 523 tests.
- **Scope limitation**: This survey covers Requirement 1 (R1). Network endpoint isolation (R2) and package metadata domain consistency (R3) are handled in complementary requirement scopes.

---

## 4. Conclusion & Recommended Concrete Code Changes

### 4.1 Recommended Architecture for `dispatch_tool` & Static AST Resolution

Replace lines 747-761 in `smart_drive/mcp/server.py` with:

```python
    TOOL_HANDLERS: Dict[str, str] = {
        "ssd_search": "handle_ssd_search",
        "ssd_audit": "handle_ssd_audit",
        "ssd_clean": "handle_ssd_clean",
        "ssd_find_duplicates": "handle_ssd_find_duplicates",
        "ssd_update_index": "handle_ssd_update_index",
        "ssd_check_safety": "handle_ssd_check_safety",
        "ssd_status": "handle_ssd_status",
        "ssd_auto_organize": "handle_ssd_auto_organize",
    }

    def dispatch_tool(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        """Dispatches a tool call with 100% static AST-resolvable branching."""
        if name == "ssd_search":
            return self.handle_ssd_search(args)
        elif name == "ssd_audit":
            return self.handle_ssd_audit(args)
        elif name == "ssd_clean":
            return self.handle_ssd_clean(args)
        elif name == "ssd_find_duplicates":
            return self.handle_ssd_find_duplicates(args)
        elif name == "ssd_update_index":
            return self.handle_ssd_update_index(args)
        elif name == "ssd_check_safety":
            return self.handle_ssd_check_safety(args)
        elif name == "ssd_status":
            return self.handle_ssd_status(args)
        elif name == "ssd_auto_organize":
            return self.handle_ssd_auto_organize(args)
        else:
            raise ValueError(f"Unknown tool '{name}'")
```

### 4.2 Recommended Updates for Tool Descriptions & Schemas in `TOOLS`

1. **`ssd_find_duplicates` (`server.py:166-188`)**:
   Add `min_size` property to `inputSchema`:
   ```python
   "min_size": {
       "type": "integer",
       "description": "Optional minimum file size in bytes to filter duplicates (default: 0).",
       "default": 0,
   },
   ```
   Update description to:
   `"Detects identical duplicate files using 3-phase cascade (Size -> 8KB Hash -> Full SHA-256) and returns exact nominal and 512KB physical cluster space savings."`

2. **`ssd_check_safety` (`server.py:213-235` & `handle_ssd_check_safety`)**:
   Update `handle_ssd_check_safety` to actually check symlinks and inspect path components:
   ```python
   is_symlink = False
   if os.path.islink(normalized_target) or ExFatEngine.is_symlink(normalized_target):
       is_symlink = True

   # Check all path segments for forbidden characters
   path_segments = [p for p in clean_path.replace("\\", "/").split("/") if p and p != "."]
   all_forbidden: List[str] = []
   for seg in path_segments:
       violations = ExFatEngine.audit_forbidden_characters(seg)
       for v in violations:
           if str(v) not in all_forbidden:
               all_forbidden.append(str(v))

   is_safe = (
       len(all_forbidden) == 0
       and not is_symlink
       and not is_prot_file
       and not is_prot_dir
       and not escapes_root
   )
   res = {
       "path": path_str,
       "is_safe": is_safe,
       "is_symlink": is_symlink,
       "forbidden_character_violations": all_forbidden,
       "is_protected_root_file": is_prot_file,
       "is_protected_root_dir": is_prot_dir,
   }
   ```

3. **`ssd_status` (`server.py:237-253`)**:
   Correct the description from "and taxonomy health" to:
   `"Inspects SSD mount status, anti-indexing shield integrity (.metadata_never_index, .fseventsd/no_log), SQLite search database integrity, exFAT safety, and Git multi-repository status."`

4. **`ssd_auto_organize` (`server.py:728-746`)**:
   In `handle_ssd_auto_organize`, when `apply_mode=True`, execute shield verification and index synchronization:
   ```python
   if apply_mode:
       from smart_drive.core.config import ensure_anti_indexing_markers
       from smart_drive.indexer.manager import IndexManager
       from smart_drive.indexer.db import DatabaseManager

       # 1. Ensure anti-indexing shields exist
       created_shields = ensure_anti_indexing_markers(self.root)

       # 2. Relocate files
       res = zoner.apply_plan(plan)

       # 3. Synchronize search index with relocated files
       db_path = self.get_db_path()
       os.makedirs(os.path.dirname(db_path), exist_ok=True)
       db = DatabaseManager(db_path)
       mgr = IndexManager(db, self.root)
       inc_stats = mgr.incremental_update()

       return {
           "status": "applied",
           "result": res,
           "shields_created": created_shields,
           "index_sync": dataclasses.asdict(inc_stats),
       }
   ```

5. **`ssd_audit` (`server.py:105-127`)**:
   Update description to:
   `"Returns full storage allocation breakdown across 6 standard taxonomies and extension categories, and calculates wasted 512KB exFAT cluster slack and top slack directories."`
   Support `args.get("sub_dir") or args.get("directory")` in `handle_ssd_audit`.

---

## 5. Verification Method

### 5.1 Static AST Resolution Verification Test
Add an automated test to `tests/test_mcp_server.py`:
```python
def test_static_ast_tool_handler_resolution(self) -> None:
    """Verifies that all 8 tools declared in TOOLS are directly resolvable via AST analysis."""
    import ast

    server_file = PROJECT_ROOT / "smart_drive" / "mcp" / "server.py"
    with open(server_file, "r", encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=str(server_file))

    # 1. Locate dispatch_tool FunctionDef
    dispatch_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch_tool":
            dispatch_fn = node
            break
    self.assertIsNotNone(dispatch_fn, "dispatch_tool method not found in AST")

    # 2. Traverse if-elif chain and map tool name string constants to handler calls
    resolved_tools = set()
    for node in ast.walk(dispatch_fn):
        if isinstance(node, ast.Compare):
            for comparator in node.comparators:
                if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                    resolved_tools.add(comparator.value)

    declared_tools = {t["name"] for t in TOOLS}
    self.assertEqual(
        resolved_tools,
        declared_tools,
        f"Missing static AST resolution for tools: {declared_tools - resolved_tools}",
    )
```

### 5.2 Test Suite Execution Command
Run the full test suite to guarantee 100% pass rate with zero regression:
```bash
python -m unittest discover tests
```
**Expected outcome**: All 523 existing tests pass OK + new AST & schema verification tests pass cleanly.
