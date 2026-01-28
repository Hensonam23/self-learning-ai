# Unused Files Analysis Report

**Date:** January 28, 2026  
**Repository:** self-learning-ai

## Executive Summary

This report identifies files in the repository that appear to be **no longer in active use**. These files were likely created during initial development but have since been replaced by newer implementations or abandoned.

---

## Findings

### 🔴 Definitely Unused/Deprecated Files (Safe to Remove)

#### 1. **`research_worker.py.bak2`**
- **Type:** Backup file
- **Last Modified:** January 27, 2026
- **Size:** Not analyzed (backup file)
- **Status:** ❌ **UNUSED - Backup file**
- **Reason:** 
  - Clear backup suffix (`.bak2`)
  - Not referenced anywhere in the codebase
  - Active version is `research_worker.py`
- **Recommendation:** **DELETE** - This is a backup file that should not be in version control

---

#### 2. **`memory_store.py`**
- **Type:** Python module (52 lines)
- **Last Modified:** January 27, 2026
- **Status:** ❌ **UNUSED - Replaced by newer implementation**
- **Reason:**
  - No imports from any other file in the codebase
  - Not referenced in any shell scripts or services
  - Functionality appears to be replaced by `storage/memory.py` which IS actively used
  - Search confirms: `grep -r "import.*memory_store"` returns no results
- **Active Replacement:** `storage/memory.py` (used by multiple tools)
- **Recommendation:** **DELETE** - Superseded by better implementation in `storage/` module

---

#### 3. **`server.py`**
- **Type:** Flask-based API server (121 lines)
- **Last Modified:** January 27, 2026
- **Status:** ❌ **UNUSED - Replaced by modern API**
- **Reason:**
  - Legacy Flask implementation
  - Not executed in any shell scripts or systemd services
  - Not imported by any active code
  - Replaced by `ms_api.py` (847 lines, FastAPI-based) and `ms_ui.py` (923 lines)
  - Only self-references found in codebase
- **Active Replacement:** `ms_api.py` + `ms_ui.py` (FastAPI stack)
- **Recommendation:** **DELETE** - Old Flask server superseded by modern FastAPI implementation

---

#### 4. **`voice_loop.py`**
- **Type:** Python module (191 lines)
- **Last Modified:** January 27, 2026
- **Status:** ❌ **UNUSED - Orphaned module**
- **Reason:**
  - No imports from any other file
  - Not referenced in shell scripts
  - Contains defensive imports suggesting experimental/abandoned code
  - Likely replaced by combination of `voice_session.py` + `conversation/wake.py`
- **Active Replacement:** `voice_session.py` (used by active voice system)
- **Recommendation:** **DELETE** - Appears to be abandoned voice loop implementation

---

### 🟡 Questionable/Utility Files (Consider Removing)

#### 5. **`import_tools.py`**
- **Type:** Utility module (91 lines)
- **Last Modified:** January 27, 2026
- **Status:** ⚠️ **UNUSED - Utility never integrated**
- **Reason:**
  - Not imported by any active production code
  - Contains utility functions for parsing and importing knowledge from files
  - Appears to be a manual data import tool that was never integrated
- **Recommendation:** **CONSIDER DELETING** - Unless used manually for data imports, can be removed

---

#### 6. **`diag_voice.py`**
- **Type:** Diagnostic/testing utility (~ lines)
- **Last Modified:** January 27, 2026
- **Status:** ⚠️ **UNUSED - Testing tool only**
- **Reason:**
  - Not imported by production code
  - Manual testing/diagnostic tool
  - Directly calls `voice_session.py` for testing
- **Recommendation:** **CONSIDER KEEPING** - Useful for manual debugging, but could be moved to `scripts/` or `tools/` directory

---

#### 7. **`learning_shim.py`**
- **Type:** Adapter module (43 lines)
- **Last Modified:** January 27, 2026
- **Status:** ⚠️ **PARTIALLY USED - Only referenced in comments**
- **Reason:**
  - Only referenced in `tools/planner.py` for the `learn-now` command
  - The import is conditional (`try/except` block)
  - Has defensive error handling suggesting experimental nature
  - However, it IS technically used by `planner.py` when running `learn-now` command
- **Recommendation:** **KEEP IF `planner.py learn-now` is used, otherwise DELETE**

---

## Summary Statistics

| Category | Count | Files |
|----------|-------|-------|
| **Backup Files** | 1 | `research_worker.py.bak2` |
| **Replaced Implementations** | 3 | `memory_store.py`, `server.py`, `voice_loop.py` |
| **Unused Utilities** | 1 | `import_tools.py` |
| **Testing/Diagnostic** | 1 | `diag_voice.py` |
| **Questionable** | 1 | `learning_shim.py` |
| **TOTAL** | 7 files | |

---

## Recommendations

### High Priority (Safe to Delete)
1. ✅ **DELETE `research_worker.py.bak2`** - Backup file
2. ✅ **DELETE `memory_store.py`** - Replaced by `storage/memory.py`
3. ✅ **DELETE `server.py`** - Replaced by `ms_api.py` and `ms_ui.py`
4. ✅ **DELETE `voice_loop.py`** - Replaced by `voice_session.py`

### Medium Priority (Review Before Deleting)
5. ⚠️ **REVIEW `import_tools.py`** - Delete if not used for manual data imports
6. ⚠️ **REVIEW `diag_voice.py`** - Keep if useful for debugging, or move to `scripts/`
7. ⚠️ **REVIEW `learning_shim.py`** - Check if `planner.py learn-now` is actually used

---

## Files That ARE Being Used (For Reference)

These files are **actively used** and should NOT be removed:
- ✅ `brain.py` - Core engine (4159 lines, main entry point)
- ✅ `answer_engine.py` - Used by brain and other components
- ✅ `memory_manager.py` - Core memory component
- ✅ `research_worker.py` - Used in watch scripts
- ✅ `ms_api.py` & `ms_ui.py` - Active FastAPI/UI implementation
- ✅ `main.py` - CLI entry point
- ✅ `evolve_ai.py`, `night_learner.py` - Active learning modules
- ✅ All `tools/` modules - Used in various automation tasks
- ✅ `storage/memory.py` - Core storage backend
- ✅ Audio/voice modules (`audio/`, `conversation/`, `voice_*.py`) - Active voice system

---

## Next Steps

1. **Back up the repository** before making any deletions
2. **Start with high-priority deletions** (backup files and clearly replaced implementations)
3. **Test the system** after each deletion to ensure nothing breaks
4. **Review medium-priority files** with the team to confirm they're not needed
5. **Update documentation** to reflect the current architecture

---

## Notes

- All files analyzed were last modified on **January 27, 2026**, suggesting a recent repository reorganization
- The transition from Flask (`server.py`) to FastAPI (`ms_api.py`, `ms_ui.py`) appears complete
- The storage layer was consolidated from individual files to the `storage/` module
- Voice interface was refactored from `voice_loop.py` to `voice_session.py` + `conversation/wake.py`

---

**Generated by:** GitHub Copilot Analysis  
**Contact:** Review this report with your team before making changes
