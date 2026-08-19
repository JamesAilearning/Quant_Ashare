# Tasks: 2026-08-19-run-catalog-cwd-pollution

## 实现

- [x] 默认索引路径锚在仓库根（不再按 CWD 解析）
- [x] 追加前判据：`output_dir` 必须落在仓库那棵 output 树内
- [x] 边界具名（`<repo>/output`），**不从索引文件位置反推**
- [x] 显式 `catalog_path` = 逃生口，不二次猜测
- [x] 判据两侧都 resolve：跨得过链接/联接/8.3 短名
- [x] 不满足时 warning + 跳过（与既有 `OSError` 契约一致），并说清原因
- [x] 维护脚本：默认只报数，`--prune` 才动手，移除行原样写旁车文件留证
- [x] 脚本与写入侧**共用同一个判据函数**（不再各写一份）
- [x] 脚本 `--tree` 显式指定边界并打印出来
- [x] 脚本防并发：改写前指纹比对，变了就拒绝动手；改写走原子替换
- [x] 脚本保留 `null` 这类合法但非记录的 JSON（以前会抛 AttributeError 中断）

## 验证（每条要实测数字）

- [x] 回溯核验：拟议判据套到现有 3560 行，逐类给出保留/拒绝数
- [x] 树外运行不再写入：真跑一次带临时 output_dir 的引擎，索引行数不变
- [x] 树内运行照常写入：不得误伤
- [x] CWD 无关：从不同目录调用，落点相同
- [x] `tests/logic/` + `tests/governance/` 全量 + mypy/ruff
- [x] `openspec validate --strict`
- [ ] codex CLEAN + CI 绿 → STOP 等 merge

## 不做

- [x] **不动操作人的真实索引**（3455 行清理交给脚本 + 操作人签收）

## 实测数字（原样）

```
体检（脚本对操作人真实索引，只读）
  总行数 3560 / 树内 105 (2.9%) / 树外 3455 (97.1%)
  树外按引擎 walk_forward 3102、pipeline 353；涉及 2283 个独立目录
  出现最多的四个各 294 次：	mp\wf_test_{irrelevant,_2,_3,explicit_disable}

第二轮（codex 四条 + Windows CI 修复）后跑全量
  4421 passed / 29 skipped / 1285 subtests（4:04）
  操作人真实索引 3560 → 3560   ← 一行没长（修复前同规模会加几十行）

判据改成两侧 resolve 之后，真实索引的分类**一行没变**：
  树内 105 (2.9%) / 树外 3455 (97.1%)   ← 与首版判据完全一致
```

首版 Windows CI 三个位全红（3.10/3.11/3.12），红的是**误拒树内运行**：
`InTreeRunsStillGetCatalogued` 写入 0 行而非 1 行。本机用目录联接复现出同一
形态（索引=解析拼写 / `output_dir`=别名拼写 → 写入 0 行），两侧都 resolve 后
复现用例转绿；该用例在 runner 上因 `TEMP` 是 8.3 短名而顺带就是短名回归。

变异验证（每条先断言变异确实落进文件，再看红）：

```
A 判据退回纯词法(不 resolve)        抓到   1 failed, 12 passed
B 去掉 isinstance(record, dict)     抓到   1 failed, 12 passed
C 去掉改写前的指纹比对              抓到   1 failed, 12 passed
D 旁车不再先写                      抓到   1 failed, 12 passed
```

（D 第一次跑时替换串没命中、测试全绿 —— 那是假信号，按行重做并断言文件确实
变了之后才拿到红。同一个假信号首版也吃过一次。）
