# Tasks — add-daily-decision-page(A1 先行,A1 合并后才开 A2)

## A1 — 推荐工件契约 v2(独立 PR)

- [x] 1.1 `recommend()` 组装 `run_meta`(generated_at Asia/Shanghai ISO、model_path、
      model_pkl_sha256、解析后 fit_start/fit_end_for_inference、provider_uri、
      bundle_tag[无 identity 时 null]、instruments、topk),挂
      `DailyRecommendationResult.run_meta`(必填,无默认值)
- [x] 1.2 `write_outputs` 序列化 `meta` 块 + `artifact_schema_version: 2`;CSV 不变
- [x] 1.3 迁移调用方/构造器:`scripts/daily_recommend.py:266` +
      `tests/logic/inference/test_daily_recommend.py`(同 commit,AGENTS.md 契约规则)
- [x] 1.4 测试:meta 块字段断言(解析窗口/模型 sha 正确)、无 identity → bundle_tag
      null、v1 旧工件可读且可判别
- [ ] 1.5 本机验证:`pytest tests/logic -q` 绿;`python -c "import src.inference.
      daily_recommend"` 过;推 PR → codex 修净 P0–P2 → CI 6 legs 绿 → 通知合并

## A2 — 每日决策页(A1 合并后)

- [ ] 2.1 `web/operator_ui/decision_journal.py`:append_decision(幂等 nonce 扫描 +
      整行单次 write+flush binary)+ read_journal(supersede 语义 + 坏行跳过计数);
      `QUANT_DECISION_JOURNAL_DIR` 解析(默认 D:/stock/operator_journal)
- [ ] 2.2 威胁单测 ×5(tests/logic,全部本机可跑):rerun 同 nonce 不重复行 / 截断行
      容错+计数 / 字节级无 \r 行尾 }\n / trade_date 取自工件+decided_at 带 +08:00 /
      src/ 零引用源码扫描
- [ ] 2.3 `pages/_daily_decision_helpers.py` 纯函数:工件发现(日期列表/最新)、
      meta 交叉核对(sha 比对/缺字段清单)、成本参照列(30bps 常量注明出处)
- [ ] 2.4 `pages/daily_decision.py` 薄渲染:横幅(缺字段 WARN 不降级/错配 WARN/
      v1 WARN)+ 候选表(只读透传)+ 决策表单(显式按钮 + session nonce)
- [ ] 2.5 `app.py` 导航"运行"组 +1 行;`docs/operations-env-vars.md` 登记
      `QUANT_DECISION_JOURNAL_DIR`
- [ ] 2.6 顺手项(唯一):`web/README.md` 真实页面清单 + 日志边界声明
- [ ] 2.7 页面源码契约测试(repo 惯例):WARN-不-降级路径存在、无 config_run/jobs
      导入、无训练/作业触发调用
- [ ] 2.8 本机 streamlit 渲染验证 + 两张截图(正常页 / 篡改 meta 缺字段后的 WARN 页)
      存 PR;`pytest tests/logic -q` 绿;ruff + mypy(CI 口径)干净
- [ ] 2.9 推 PR(描述含对抗自审结论 + 截图)→ codex 修净 P0–P2 → CI 6 legs 绿 →
      通知合并;progress.md 更新

## 验收(对开工单 §5)

- [ ] 3.1 本 tasks 全勾;威胁五条各有对应单测且全绿;`openspec validate
      add-daily-decision-page --strict` 通过;A1/A2 均按上述流程合并
