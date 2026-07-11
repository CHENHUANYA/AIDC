# Alarm RAG Phase 0 版本收口計畫

更新日期：2026-07-11

## 目標

Phase 0 將目前已完成但尚未提交的 PostgreSQL 維運強化、工作流程一致性、前端修正與驗證文件，整理成可審查、可重跑、可安全提交的交付基準。本階段不擴充新的大型產品功能。

## 目前基準

| 項目 | 狀態 | Phase 0 動作 |
|---|---|---|
| PostgreSQL operations hardening | 已實作、待提交 | 依交付主題拆分提交 |
| Issue／Work Order 樂觀鎖 | 已實作、待提交 | 保留 API、前端與測試在同一提交 |
| Ruff | 通過 | 納入單一收口檢查器 |
| mypy | 19 個高風險來源通過 | 納入單一收口檢查器 |
| pytest | 2026-07-11 完整基準為 294 passed、26 subtests passed | 維持本機與 CI 完整通過 |
| Python runtime | 專案目標 3.11，本機驗證使用 3.12 | CI 同時驗證 3.11 與 3.12 |
| Production TLS／School API／正式 secrets rotation | 尚未有目標環境證據 | 保留為外部環境 gate，不在本機偽造完成 |

## 工作項目

### P0-1 可重複品質閘門

使用單一命令執行 Git diff hygiene、Ruff、mypy 與完整 pytest：

```powershell
python scripts/phase0_closeout_check.py
```

快速靜態檢查：

```powershell
python scripts/phase0_closeout_check.py --skip-pytest
```

正式提交後的 clean-tree gate：

```powershell
python scripts/phase0_closeout_check.py --require-clean
```

報告預設寫入 `tests_tmp/phase0/phase0_closeout_report.json`，此路徑受 Git ignore 保護。

### P0-2 CI runtime 基準

- Python 品質與測試 job 同時覆蓋 3.11、3.12。
- Container build 與 Compose contract 維持獨立 job，避免在 Python matrix 重複建置。
- Alembic SQL dry-run、file-secret overlay contract 必須維持通過。

### P0-3 文件重新基準化

| 文件 | Phase 0 定位 |
|---|---|
| `plans/NEXT_LOCAL_WORK_PLAN_2026-06-24.md` | 歷史本機工作計畫；多數可靠性項目已完成 |
| `plans/NEXT_PHASE_PRODUCTIZATION_AND_DEPLOYMENT_PLAN.md` | Pilot／Production 長期方向，保留作為 Phase 1 以後輸入 |
| `plans/POSTGRESQL_OPERATIONS_HARDENING_PLAN_2026-07-08.md` | 實作大致完成，待本批變更提交與外部演練 |
| `PR_DELIVERY_SUMMARY_2026-07-10.md` | 目前未提交工作樹的交付與驗證摘要 |
| 本文件 | 2026-07-11 起的版本收口執行來源 |

### P0-4 建議提交分組

提交前逐組 stage、檢查 `git diff --cached --check`，不要混入 runtime data 或 `tests_tmp`。

1. `ci: add PostgreSQL closeout quality gates`
   - CI、mypy scope、secret overlay checker、Phase 0 checker及其測試。
2. `fix: enforce optimistic locking for issue and work-order updates`
   - Issue、Work Order、三個角色頁面、相關 smoke／regression／contract tests。
3. `feat: harden PostgreSQL backup health and restore evidence`
   - Backup、health、restore、network boundary及其測試。
4. `docs: add PostgreSQL operations handoff runbooks`
   - Operations index、monitoring、rotation、restore、TLS boundary與驗收報告。
5. `chore: remove generated PDF page artifacts`
   - 僅包含 `pdf_pages/*.png` 刪除。

## Phase 0 出口條件

- `python scripts/phase0_closeout_check.py` 回傳 `RESULT=PASS`。
- CI 的 Python 3.11／3.12、container build、Compose jobs 全數通過。
- 工作樹依上述主題提交，且提交後 `--require-clean` 通過。
- README 與文件入口指向本計畫及目前交付摘要。
- 正式環境尚未驗證的 TLS、School API、WAL archive、secret rotation 明確保留為未完成，不以本機結果替代。

## Phase 0 後第一批工作

Phase 1 優先順序固定為：事件冪等鍵、列表分頁、Repository 單筆操作、System Settings／Documents 樂觀鎖、RAG 黃金評測集。Phase 0 未完成前不啟動大規模功能開發。
