# Demo Recording Script

## Purpose

Record a short self-running MVP demo that shows the no-vendor flow end to end:

```text
Mock alarm -> alarm banner -> lookup/RAG source -> auto work order -> close order -> BI movement
```

Target length: 4 to 6 minutes.

## Setup Shot

Show these tabs before recording:

- `/alarm-app`
- `/dashboard`
- `/operations`
- Terminal in `alarm-rag/`

Start the API:

```bash
uvicorn main:app --host 0.0.0.0 --port 8100 --reload
```

Seed richer demo data when needed:

```bash
python scripts/seed_week2_data.py --base-url http://localhost:8100
```

## Recording Flow

1. Open `/alarm-app` and show the `警報監控` tab.
2. Trigger one alarm:

```bash
python scripts/replay_demo_alarms.py --base-url http://localhost:8100 --source n8n-mock --min-severity high --delay 0
```

3. Show the alarm banner after polling.
4. Click `立即查詢解決方案` and confirm alarm `3000` lookup result appears.
5. Point out source metadata: collection, code, page/title, source or document ID.
6. Open `維修工單` and show the auto-created work order.
7. Move the order to completed and enter a short resolution.
8. Open `BI 儀表板`, refresh, and show movement in at least four places:
   - 今日警報數
   - 查詢總數
   - 工單總數 or 完成率
   - 回饋 or來源統計
9. Run the Week 4 acceptance script:

```bash
python scripts/week4_acceptance.py --base-url http://localhost:8100 --manual 808d --alarm-code 3000
```

10. Show `docs/MVP_WEEK4_ACCEPTANCE_REPORT.md` with all rows passing.

## Screenshot Checklist

Capture these stills for slides or a proposal appendix:

- Alarm banner visible in `/alarm-app`.
- Lookup result with source metadata.
- Work order detail after completion.
- BI dashboard after refresh.
- Week 4 acceptance report.
- n8n workflow canvas or `mock_data/n8n_mock_workflow.json` import preview.

## Narration Beats

- "This demo does not require vendor data or a real PLC connection."
- "The current trigger is HTTP/n8n mock; future OPC-UA events replace only the trigger source."
- "Maintenance resolutions are written back into the knowledge base."
- "BI metrics update from the same operational events used by the operator flow."
