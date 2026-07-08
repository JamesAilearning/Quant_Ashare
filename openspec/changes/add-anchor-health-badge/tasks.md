# Tasks — add-anchor-health-badge(PR-B)

- [x] 1.1 `web/operator_ui/anchor_health.py`:normalized_sha256(同锚测试算法,
      交叉引用)+ baseline_identity(git last-touch 注入可测,浅克隆降级)+
      ci_leg_status(gh 两跳:run list → view jobs 取锚腿;缺席/超时/解析失败
      → unknown+detail;job 名未命中回落整 run 结论并注明)
- [x] 1.2 `app.py` sidebar 徽章:emoji 状态点 + sha8/重签/evidence/CI 腿;
      `st.cache_data(ttl=600)`(untyped-decorator ignore 惯例);永不阻塞/崩溃
- [x] 1.3 tests/logic/test_anchor_health.py:sha CRLF/LF 等值 + 真实基线可算;
      git 注入成功/失败路径;gh 注入 success/job 回落/缺席/超时/坏 JSON →
      unknown;源码契约(模块零 streamlit;app.py 含 ttl 缓存与徽章)
- [x] 1.4 本机验证:目标测试 + tests/logic 全量绿;ruff + mypy --strict 干净;
      `openspec validate --strict` 过;streamlit 一次起停截图(徽章可见)
- [ ] 1.5 推 PR → codex 修净 P0–P2 → CI 6 legs 绿 → 通知合并(合并只能 James)
