# Subagent 调用 SOP

## 何时调用（调用原则）

唯一适用场景：**map模式**——将N个独立同构子任务分发给各自的subagent处理。

- 核心优势：独立上下文。避免处理文档A的长上下文污染处理文档B的质量
- 文件系统共享是优点：不同agent处理不同输入文件，产生不同输出文件
- 共享资源冲突：键鼠/浏览器主体不可共享（浏览器可分tab但需谨慎），subagent任务应限于文件处理
- 不满足map模式的任务 → 主agent顺序执行即可，别用subagent

**额外场景：SOP dry-run验证**——启动单个subagent执行目标SOP，通过output日志发现SOP缺陷（缺参数/选择器不准/步骤模糊），主agent据此patch优化SOP。单subagent不存在资源冲突。
- 测SOP质量：input指定SOP名（如"用ezgmail_sop查看最近3封未读邮件"），排除导航干扰，失败即SOP问题
- 测导航能力：input只写目标，验证subagent能自主从insight找到正确SOP。禁止内联SOP内容

### 测试驱动SOP优化方法

**场景**：发现SOP存在路径/参数/步骤问题，需验证修改效果

**方法**：
1. 创建test_path测试环境：`temp/test_path/`，写入input.txt描述测试目标
2. 启动subagent：`python agentmain.py --task test_path`（从代码根启动）
3. 观察output.txt和实际文件操作，验证SOP修改是否生效
4. 清理test_path，重复测试直到验证通过

**关键发现**（已验证）：
- **Insight优先级>SOP**：subagent优先读Insight索引，Insight中的警告/约束会覆盖SOP中的说明
- **subagent的cwd**：和主agent相同（都在temp/），subagent不知道自己是subagent
- **路径引用**：从temp/访问memory/用`../memory/`，访问autonomous_reports/用`./autonomous_reports/`（在temp/下）

**标准流程（map-reduce）**：
1. 主agent准备阶段：爬取/dump数据，存为多个独立输入文件
2. 分发：对每个文件启动一个subagent处理（主agent自己也可以处理其中一个）
3. 收集：等所有subagent完成，主agent读取各输出文件，汇总结果

## Task Mode 文件IO协议
- 目录：`temp/{task_name}/`（相对代码根GenericAgent/），主agent cwd在temp/时即 `./{task_name}/`
- 启动：`python agentmain.py --task {task_name} [--llm_no N]`（cwd=代码根）
- 流程：写 input.txt → 启动 → 轮询 output.txt → 读回复 → 写 reply.txt 继续 → 不写则5min自动退出
- input.txt原则：写目标+约束，可指定SOP名。禁写具体实现步骤——除非主agent已读过该SOP确认正确。凭印象猜的步骤会误导subagent
- reply后subagent的后续输出在 output1.txt, output2.txt ... 中（递增编号），主agent需轮询这些文件
- output.txt 每轮覆盖写，用 mtime/size 判断新轮次

## 后台调用要点
```python
task_dir = os.path.join(agent_root, 'temp', task_name)
proc = subprocess.Popen(
    [sys.executable, 'agentmain.py', '--task', task_name],
    cwd=agent_root, creationflags=0x08000000,
    stdout=open(os.path.join(task_dir, 'stdout.log'), 'w', encoding='utf-8'),
    stderr=open(os.path.join(task_dir, 'stderr.log'), 'w', encoding='utf-8'))
```
- 必须 Popen，禁止 subprocess.run（会阻塞）
- stdout.log/stderr.log 用于调试subagent卡死、LLM调用失败等问题
- `--llm_no` 默认=sonnet 4.5，`--llm_no 1`=opus 4.6
- 文件统一 UTF-8，subagent 无 reply 5min 自动退出无需清理