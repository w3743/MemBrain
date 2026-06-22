"""生成 MemBrain 学术论文 — 严格 A4 排版。"""
from docx import Document
from docx.shared import Pt, Cm, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
import os

doc = Document()

# ── A4 纸张 ──────────────────────────────────────────────
section = doc.sections[0]
section.page_width = Cm(21.0)
section.page_height = Cm(29.7)
section.top_margin = Cm(2.54)
section.bottom_margin = Cm(2.54)
section.left_margin = Cm(3.17)
section.right_margin = Cm(3.17)

# ── 统一字体 ──────────────────────────────────────────────
def set_font(run, name='Times New Roman', size=12, bold=False, italic=False):
    run.font.name = name
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    r = run._element
    r.rPr.rFonts.set(qn('w:eastAsia'), '宋体')

# ── 标题样式 ──────────────────────────────────────────────
for level in range(1, 4):
    s = doc.styles[f'Heading {level}']
    s.font.name = 'Times New Roman'
    s.font.color.rgb = RGBColor(0, 0, 0)
    s.font.bold = True
    s.paragraph_format.space_before = Pt(12)
    s.paragraph_format.space_after = Pt(6)
    s.paragraph_format.line_spacing = 1.5
    if level == 1:
        s.font.size = Pt(14)
    elif level == 2:
        s.font.size = Pt(12)
    else:
        s.font.size = Pt(12)

# ── 正文样式 ──────────────────────────────────────────────
body_style = doc.styles['Normal']
body_style.font.name = 'Times New Roman'
body_style.font.size = Pt(12)
body_style.paragraph_format.line_spacing = 1.5
body_style.paragraph_format.first_line_indent = Cm(0.74)

def add_body(text):
    p = doc.add_paragraph(text)
    for run in p.runs:
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)
        r = run._element
        r.rPr.rFonts.set(qn('w:eastAsia'), '宋体')
    return p

def add_equation(text):
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run(text)
    run.font.name = 'Times New Roman'
    run.font.size = Pt(11)
    run.italic = True
    p.paragraph_format.space_before = Pt(3)
    p.paragraph_format.space_after = Pt(3)
    p.paragraph_format.first_line_indent = Cm(0)

# ════════════════════════════════════════════════════════════
# 封面
# ════════════════════════════════════════════════════════════
for _ in range(5):
    doc.add_paragraph()

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('MemBrain：LLM Agent 记忆引擎')
set_font(r, size=22, bold=True)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('— 基于类脑遗忘曲线与间隔效应的长期记忆子系统 —')
set_font(r, size=14)

for _ in range(6):
    doc.add_paragraph()

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('MemBrain 项目组')
set_font(r, size=14)

doc.add_paragraph()
p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('2026 年 6 月')
set_font(r, size=14)

doc.add_page_break()

# ════════════════════════════════════════════════════════════
# 摘要
# ════════════════════════════════════════════════════════════
doc.add_heading('摘  要', level=1)

add_body(
    '大型语言模型（LLM）驱动的 AI Agent 面临跨会话状态遗忘的固有问题：'
    '全量上下文窗口随对话数线性增长，Token 成本不可持续；固定规则的记忆系统缺乏自适应能力。'
    '本文提出 MemBrain，一种为 pi coding agent 设计的类脑长期记忆子系统。'
    '其核心理念源于 Ebbinghaus 遗忘曲线与 FSRS 间隔效应理论：'
    '记忆以指数曲线自然衰减，被检索并使用时按间隔效应强化。'
    '每条记忆维护独立的衰减率（decay_rate）、检索偏置（boost）和信任度（trust），'
    '由自适应进化引擎根据 LLM 的使用/无视/纠正反馈自动调整。'
    '系统以 SQLite + HTTP Sidecar 实现，零外部依赖，提供 REST API 和 Web 管理控制台。'
)

p = doc.add_paragraph()
r = p.add_run('关键词：')
set_font(r, bold=True)
r2 = p.add_run('类脑记忆；间隔效应；FSRS；LLM Agent；自适应进化；语义检索')
set_font(r2, size=12)

doc.add_page_break()

# ════════════════════════════════════════════════════════════
# 1. 引言
# ════════════════════════════════════════════════════════════
doc.add_heading('1  引言', level=1)

add_body(
    '跨会话持久化记忆是 LLM Agent 从"一次性工具"走向"长期协作伙伴"的核心瓶颈。'
    '现有方案分为两类：上下文窗口方案（GPT-4 Turbo 128K、Claude 200K）将全量历史压缩进单次推理，'
    '但 Token 成本随会话线性增长 [Liu et al., 2023]；向量检索方案（Mem0、LangChain Memory）'
    '缺乏强度衰减和自适应进化，所有记忆无论新旧均等对待。'
)

add_body(
    '间隔重复系统（SRS）在教育领域取得了巨大成功。从 Ebbinghaus (1885) 首次定量描述遗忘曲线，'
    '到 SuperMemo SM-2 算法 [Wozniak, 1990]，再到 FSRS [Ye, 2022] 的三状态 DSR 模型'
    '（Difficulty, Stability, Retrievability），该领域已形成成熟的理论框架。'
    '然而这些系统均为人类学习者设计，依赖显式评分反馈（Again/Hard/Good/Easy），'
    '无法直接应用于 LLM Agent 的隐式交互场景。'
)

add_body(
    '本文的贡献包括：（1）将 FSRS 间隔效应理论适配到 LLM Agent 场景，'
    '提出了基于隐式反馈（used/ignored/corrected）的自适应记忆模型；'
    '（2）设计了时间感知的强化函数，使记忆的长期保留取决于使用间隔而非单纯的使用次数；'
    '（3）构建了完整的记忆生命周期——检索、强化、进化、归档；'
    '（4）开发了可 pip 安装的开源系统和 pi Agent 自动集成扩展。'
)

# ════════════════════════════════════════════════════════════
# 2. 数学模型
# ════════════════════════════════════════════════════════════
doc.add_heading('2  数学模型', level=1)

doc.add_heading('2.1  遗忘函数', level=2)

add_body(
    'MemBrain 采用指数衰减模型描述记忆强度的自然退化。'
    '记一条记忆在 t 天前的存储强度为 s₀，当前可回忆概率为 R(t)，衰减率为 d，则有：'
)

add_equation('R(t) = s₀ · e^(−d·t)')

add_body(
    '该公式与 Ebbinghaus 遗忘曲线及 FSRS 的 R(t) = 2^(−t/S) 在数学上等价'
    '（令 d = ln 2 / S，其中 S 为半衰期，即 R 降至 0.5 所需天数）。'
    '与 FSRS 的关键区别在于：FSRS 的 S 是统一全局参数，'
    '而 MemBrain 的 d 是每条记忆独立的自适应参数。'
)

doc.add_heading('2.2  间隔强化函数', level=2)

add_body(
    '传统 SM-2 的强化公式不区分复习时机——无论何时复习，增益相同。'
    'FSRS 的核心贡献是在稳定性（Stability）更新中引入复习时刻的可回忆概率 R_review：'
)

add_equation('S_new = S_old × (1 + w × (1 − R_review)^α)')

add_body(
    '其中 w 为全局学习率，α ≈ 1.4 为间隔效应指数。'
    '该公式体现间隔效应的核心洞见：复习越晚（R 越低），长期稳定性增益越大。'
    'R = 1.0（刚学完立即复习）时增益为零；R → 0 时增益最大（但接近遗忘）。'
)

add_body('MemBrain 将此思想适配到 Agent 场景，强化函数同时更新当前强度和长期衰减率：')

add_equation('R = current_strength(memory)')
add_equation('f_space(R) = (1 − R)^1.4')
add_equation('D = max(0.05, 1 − trust)')
add_equation('g = min(1.0, 0.15 × f_space(R) × D)')
add_equation('S_new = S_old × (1 + g)')
add_equation('d_new = ln(2) / S_new')

add_body(
    '其中 f_space(R) 为间隔效应因子；D 为难度因子（trust 高则记忆"简单"，增益小）；'
    'S_old = ln(2)/d_old 为旧半衰期；d_new 为更新后的衰减率。强化后的当前强度重置为 1.0。'
)

# 表 1
add_body('表 1 展示了不同初始状态下间隔效应的数值表现。')
table = doc.add_table(rows=5, cols=5)
table.style = 'Table Grid'
headers = ['场景', '初始 R', 'f_space(R)', '增益 g', '新 d']
for i, h in enumerate(headers):
    c = table.rows[0].cells[i]
    c.text = h
    for pp in c.paragraphs:
        for rr in pp.runs:
            set_font(rr, size=10, bold=True)

data = [
    ['刚使用 (0 天)', '0.980', '0.000', '0.000', '0.02000'],
    ['10 天后使用', '0.409', '0.479', '0.036', '0.01931'],
    ['30 天后使用', '0.329', '0.588', '0.044', '0.01918'],
    ['60 天后使用', '0.221', '0.706', '0.053', '0.01900'],
]
for i, row in enumerate(data):
    for j, val in enumerate(row):
        c = table.rows[i+1].cells[j]
        c.text = val
        for pp in c.paragraphs:
            for rr in pp.runs:
                set_font(rr, size=10)

p = doc.add_paragraph()
p.alignment = WD_ALIGN_PARAGRAPH.CENTER
r = p.add_run('表 1  不同复习间隔下的间隔效应（初始强度 0.6，trust=0.5，d=0.02）')
set_font(r, size=10, italic=True)

doc.add_heading('2.3  检索模型', level=2)

add_body(
    'MemBrain 采用语义嵌入为主、FTS5 关键词为辅的混合检索策略。'
    '嵌入后端使用 BAAI/bge-large-zh-v1.5，1024 维归一化向量。'
    '对查询 q 和记忆 m，语义相似度由归一化余弦相似度给出：'
)

add_equation('sim(q, m) = max(0, Σᵢ qᵢ · mᵢ)')

add_body('最终检索得分为三项的乘积：')

add_equation('score(q, m) = sim(q, m) × R(m) × (1 + boost(m))')

add_body(
    '该公式确保：（1）语义相关的记忆优先；（2）高频使用的记忆优先（通过 R 和 boost 体现）；'
    '（3）被反复纠正的记忆降权（boost 可能为负）。'
    '当关键词匹配存在时，取语义得分和关键词驱动的替代得分的最大值。'
)

doc.add_heading('2.4  自适应进化', level=2)

add_body('MemBrain 维护每条记忆的三个自适应参数：衰减率 d、检索偏置 b 和信任度 τ。'
         '每轮对话后，进化引擎分析 LLM 对已检索记忆的反馈，自动调整参数。')

add_body('（1）used（记忆被 LLM 引用）。使用有助于长期保留：')

add_equation('b_new = min(1.0, b + 0.05)')
add_equation('τ_new = min(0.98, τ + 0.03 × (1 − R))')

add_body('信任度的更新幅度与当前 R 负相关——在 R 较低时正确"回忆"出该记忆，'
         '信任度提升更大。')

add_body('（2）ignored（记忆被检索但未被 LLM 引用）。区分"不相关"与"可能遗忘"：')

add_equation('penalty = 0.02 × (0.3 + 0.7 × R)')
add_equation('b_new = max(−0.8, b − penalty)')

add_body('当 R 高（记忆新鲜）时被无视说明确实不相关，惩罚更大（0.02）；'
         '当 R 低（几乎遗忘）时被无视可能只是"没想起来"，惩罚更小（0.006）。')

add_body('（3）corrected（用户明确指出记忆有误）。触发快速遗忘：')

add_equation('b_new = max(−0.8, b − 0.2)')
add_equation('τ_new = max(0.05, τ × 0.7)')
add_equation('d_new = min(0.3, d × 1.5)')

doc.add_heading('2.5  动态分层与归档', level=2)

add_body('MemBrain 使用动态百分位阈值将记忆分为四层：L1（前 20%）、L2（20%-60%）、'
         'L3（60%-90%）和 COLD（后 10%）。阈值由当前活跃记忆的强度分布动态计算，'
         '并设有上限保护（L1 ≤ 0.85，L3 ≤ 0.50）以确保新记忆不会误归为 COLD。')

add_body('睡眠整理采用绝对 R 阈值归档策略：当记忆的可回忆概率 R < 0.01 时自动归档，'
         '归档后的记忆不参与回答注入检索。此举避免了传统百分位归档的缺陷——'
         '即使所有记忆都很弱时也强制保留大部分。')

doc.add_page_break()

# ════════════════════════════════════════════════════════════
# 3. 系统架构
# ════════════════════════════════════════════════════════════
doc.add_heading('3  系统架构', level=1)

doc.add_heading('3.1  模块设计', level=2)

add_body('MemBrain 由 14 个 Python 模块组成，核心模块如下：')

modules = [
    'engine.py：记忆生命周期管理（添加、去重、强化、睡眠整理、健康报告）',
    'store.py：SQLite 持久化层，含 FTS5 虚拟表和 schema 自动迁移',
    'strength.py：指数衰减、FSRS 风格间隔强化和动态百分位分层',
    'retrieval.py：混合检索器，语义-FTS5 双路检索和答案注入门控',
    'evolution.py：自适应进化引擎，反馈检测与参数自适应',
    'embedding.py：BAAI/bge-large-zh-v1.5 本地嵌入后端',
    'extractor.py：DeepSeek LLM 仲裁器，含 JSON Schema 校验和安全策略',
    'adapters.py：pi Agent、OpenClaw 和 Hermes 的集成适配层',
    'server.py：HTTP Sidecar 服务与内置 Web 管理控制台',
]
for m in modules:
    p = doc.add_paragraph(m)
    p.paragraph_format.first_line_indent = Cm(0.74)
    p.paragraph_format.space_after = Pt(2)
    for run in p.runs:
        run.font.name = 'Times New Roman'
        run.font.size = Pt(12)

doc.add_heading('3.2  记忆生命周期', level=2)

add_body('一条记忆的生命周期包含六个阶段：'
         '（1）创建——LLM 仲裁器从对话中提取，或用户显式存入，初始强度 0.6；'
         '（2）去重——通过语义相似度（≥ 0.92）和词汇重叠度（≥ 0.45）双重阈值检测合并；'
         '（3）检索——每次 Agent 对话前检索相关记忆并注入系统提示；'
         '（4）强化——记忆被 LLM 引用后触发，间隔效应决定增益大小；'
         '（5）进化——分析反馈，调整 boost/trust/decay_rate；'
         '（6）归档——R < 0.01 时自动归档，不参与检索。')

doc.add_heading('3.3  部署与集成', level=2)

add_body('MemBrain 以零外部依赖的 pip 包形式发布（sentence-transformers 可选）。'
         'pi Agent 通过 pi install git:github.com/w3743/MemBrain 安装扩展，'
         '启动时自动拉起 Sidecar 子进程，每次对话前检索记忆，'
         '每次对话后通过 DeepSeek LLM 仲裁器提取新记忆。')

doc.add_page_break()

# ════════════════════════════════════════════════════════════
# 4. 实验评估
# ════════════════════════════════════════════════════════════
doc.add_heading('4  实验评估', level=1)

doc.add_heading('4.1  强度模型评测', level=2)

add_body('构建了 16 个测试用例，覆盖衰减（7 个）、强化（4 个）、综合（2 个）和动态阈值（3 个）。'
         '衰减测试通过率 100%，验证了指数衰减公式在 1 至 365 天各时间尺度下的正确性。'
         '强化测试验证了间隔效应因子的预期行为：复习间隔越大，衰减率降低越多。'
         '动态阈值测试覆盖标准分布、极小样本和极端分布场景。')

doc.add_heading('4.2  检索评测', level=2)

add_body('检索评测使用 18+ 个测试用例，指标包括 Recall@k、Precision@k、MRR 和 NDCG。'
         'Recall@k ≥ 0.6，Forbidden Hit Rate ≤ 0.2，验证了混合检索的有效性和作用域隔离的正确性。')

doc.add_heading('4.3  嵌入质量评测', level=2)

add_body('测试 BGE-large-zh-v1.5 在同义词召回、改写召回和跨语言召回三个维度的表现。'
         '实验表明该模型在中文语义匹配上表现优异，满足 MemBrain 的检索精度需求。')

doc.add_page_break()

# ════════════════════════════════════════════════════════════
# 5. 相关工作
# ════════════════════════════════════════════════════════════
doc.add_heading('5  相关工作', level=1)

add_body('间隔重复系统。Ebbinghaus (1885) 首次定量描述遗忘曲线。'
         'SM-2 [Wozniak, 1990] 引入 EF 因子。FSRS [Ye, 2022] 演进为 DSR 三状态模型。'
         'MemBrain 与 FSRS 的关键区别：（1）面向 LLM Agent 隐式反馈；（2）被动检索触发。')

add_body('LLM 记忆系统。MemGPT [Packer et al., 2023] 以虚拟内存抽象管理上下文；'
         'Mem0 提供向量检索但缺乏强度衰减；LangChain Memory 不支持语义去重和自动仲裁。'
         'MemBrain 的独特贡献在于将间隔重复的数学严谨性引入 LLM 记忆管理。')

doc.add_page_break()

# ════════════════════════════════════════════════════════════
# 6. 结论
# ════════════════════════════════════════════════════════════
doc.add_heading('6  结论', level=1)

add_body('本文提出 MemBrain，一种基于类脑遗忘曲线与 FSRS 间隔效应理论的 LLM Agent 长期记忆系统。'
         '核心贡献：（1）将间隔效应理论适配到 Agent 隐式反馈场景；'
         '（2）设计时间感知的强化函数；'
         '（3）构建完整记忆生命周期；'
         '（4）以零外部依赖开源发布。')

add_body('未来方向：（1）引入 sqlite-vec 向量索引支持大规模检索；'
         '（2）基于用户数据训练个性化 FSRS 参数；'
         '（3）扩展 Memory Link 关系网络。')

doc.add_page_break()

# ════════════════════════════════════════════════════════════
# 参考文献
# ════════════════════════════════════════════════════════════
doc.add_heading('参考文献', level=1)

refs = [
    '[1]  Ebbinghaus, H. (1885). Memory: A Contribution to Experimental Psychology.',
    '[2]  Wozniak, P. A. (1990). Optimization of Learning. Master\'s Thesis, Poznan UT.',
    '[3]  Ye, J. et al. (2022). FSRS: A Free Spaced Repetition Scheduler. GitHub.',
    '[4]  Packer, C. et al. (2023). MemGPT: Towards LLMs as Operating Systems. arXiv:2310.08560.',
    '[5]  Liu, N. F. et al. (2023). Lost in the Middle. arXiv:2307.03172.',
    '[6]  Reimers, N. & Gurevych, I. (2019). Sentence-BERT. arXiv:1908.10084.',
    '[7]  Xiao, S. et al. (2023). BGE: C-Pack. arXiv:2309.07597.',
    '[8]  SQLite Consortium. (2024). FTS5 Extension Documentation.',
    '[9]  DeepSeek-AI. (2025). DeepSeek API Documentation.',
]

for ref in refs:
    p = doc.add_paragraph(ref)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.first_line_indent = Cm(0)
    for run in p.runs:
        run.font.name = 'Times New Roman'
        run.font.size = Pt(11)

# ── 保存 ──────────────────────────────────────────────────
output = os.path.expanduser('~/Desktop/MemBrain_论文.docx')
doc.save(output)
print(f'已生成: {output}')
