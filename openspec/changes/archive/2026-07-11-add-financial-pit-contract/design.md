# Gate-2 · Step-0 只读勘察 note（实现路径待 operator 确认）

> **性质:** Step-0 只读代码勘察,**未写任何实现**。确认 5 点 + 荐路径 + 3 个 operator 决策点,STOP。
> **前置:** Gate-0 charter 签、Gate-1 memo 过、4 契约默认签、`openspec validate --strict` 绿。

## 5 点确认(逐条给证据)

**① fetcher 能否拉 income/balancesheet/cashflow(带 PIT 列)+ 版本化落盘落点**
- `src/data/tushare/fetcher.py`:有 `ENDPOINTS` 注册表(L104)+ 干净的 per-endpoint `_fetch_*` 方法(单文件聚合 / stock_basic / index_weight / **per-ticker-per-year**)+ `atomic_write_parquet`。**加 income/balancesheet/cashflow = 加 3 个 `_fetch_*` + ENDPOINTS 三项**,架构支持。
- 财报是 **per-ts_code**(一次调用返回该股全部报告期);Gate-1 实证返回 `end_date/ann_date/f_ann_date/update_flag`(85 列)。
- **落点:** 沿用 data_pipeline ingest 约定 → `tushare_raw/{income,balancesheet,cashflow}/`。**⚠ 但现有 per-ticker-per-year 的 freshness/覆盖 skip 会去重/覆盖旧文件** —— 契约要求「留双 update_flag 行 + content hash + fetch batch + 变更不静默覆盖」是**新的版本保留 ingest 模式**,不能照抄现有覆盖式落盘。→ 新落盘方案(见决策 B)。

**② PIT 层结构 + FinancialPITDataView 隔离(照 D5)**
- `src/pit/query.py::PITDataProvider` **绑定 canonical runtime**(`init_qlib_canonical` + `D.features/D.calendar/D.instruments`)→ **不扩展它**。
- **D5 门样板 = `src/factor_mining/pit_adapter.py::FactorMiningDataView`**:factor_mining 唯一持有 PIT 引用的门、duck-typed(测试可塞 stub)、其余模块不直连 PIT。**FinancialPITDataView 照此形状**:单一 research-side 门、sole access path、duck-typed、与 canonical 隔离。
- `src/data/pit/index_membership.py` 提供 PIT 成分(见 ④)。

**③ available_from_trade_date 交易日历源(复用现有)**
- **复用 `src/data/trading_calendar.py`**:`StaticTradingCalendar`(纯内存、bisect、可测)/ `QlibTradingCalendar`(需 qlib init)/ `TradingCalendar` Protocol。
- **隔离要点:** 直接读 bundle 的 `calendars/day.txt` 构 `StaticTradingCalendar`(**不走 qlib.init**)→ research 视图拿「公告日后第一个交易日」而**不碰 canonical qlib 运行时**。需补一个小 `next_trading_day_after(date)`(对已排序日历 `bisect_right`)。避开 `QlibTradingCalendar`(它要 qlib init)。

**④ CSI300-ever PIT 成分(含退市名)复用**
- `instruments/csi300.txt`(PIT 成分 spans,709 run,含退出名的闭区间)+ `delisted_registry.parquet` = CSI300-ever 含退市宇宙。`PITDataProvider.get_universe` 已用 instruments+registry 做 PIT 过滤(含 post-delist mask)。财报视图宇宙**复用此路径**(spec 场景「delisted CSI300-ever via existing PIT universe」)。

**⑤ grep 确认不碰 canonical runtime / Alpha158 / daily_recommend**
- Canonical 特征图 = `feature_dataset_builder` / `_feature_dataset_cache` / `model_trainer` / `pipeline` / `daily_recommend` / `mined_factor_handler`(mined 因子→qlib handler 桥)。
- FinancialPITDataView **尚不存在 → 当前零引用(基线干净)**。设计保证其不进该图;**治理/import 测试**断言它不被上述任一模块 import。
- ingest 只往 fetcher(数据层)加 endpoint,**财报 raw 仅被 research 视图消费**,不喂 Alpha158/训练;无新 canonical-runtime qlib 调用点。

## 荐实现路径(照 tasks.md,拆 2 PR)

- **PR-1(ingest + 契约):** fetcher 加 3 个 `_fetch_*` + 版本保留落盘(双 update_flag / content hash / fetch batch / 变更记录非覆盖)+ 契约字段(`report_period` / `announcement_date` f_ann_date→ann_date 记 fallback / `available_from_trade_date` 公告后第一个交易日 / revision linkage)+ ingest & PIT-定日治理测试。
- **PR-2(view + 治理,stacks on PR-1):** `FinancialPITDataView`(as-of carry-forward 非 fillna、缺失 NA、金融股稳定名单排除 + `oper_cost` 缺失交叉、暴露 charter 列含 `adv_receipts`/`contract_liab` 原始)+ 治理测试(前视拒绝 / 下一交易日 / 原始披露优先 / 缺失 fail-loud 含 `rd_exp`=NA / 直读 raw 拒绝 / 覆盖门槛按 Gate-1 §4 n=627)+ 隔离测试(不被 canonical 图 import)。

## 3 个 operator 决策点(STOP 等确认)

1. **FinancialPITDataView 的家 + 隔离机制:** 建议 `src/data/pit/financial_pit.py`(视图)+ ingest 在 `src/data/tushare/`;靠 import/治理测试断言不进 canonical 图。是否接受?还是要一个独立 research 命名空间(更强物理隔离)?
2. **版本保留落盘方案:** 建议 `tushare_raw/{income,balancesheet,cashflow}/<ticker>.parquet`,行内带 `update_flag`+`content_hash`+`fetch_batch`,同 `(instrument, end_date, update_flag)` 唯一、变更追加新 batch 不覆盖。是否接受这个 store 形状?
3. **日历隔离:** 用 `StaticTradingCalendar`-from-`day.txt`(不碰 qlib init,隔离更干净)—— 确认走这条,而非 `QlibTradingCalendar`?

## STOP
等 operator 确认以上 3 决策 → 按 tasks.md 实现(PR-1 → @codex → CI → 手动合 → PR-2)。**本 note 不实现任何东西。**
