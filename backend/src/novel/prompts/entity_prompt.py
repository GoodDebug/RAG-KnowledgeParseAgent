# -*- coding: utf-8 -*-
"""
entity 抽取专属 Prompt + JSON Schema（子任务 03）。

设计：
  - Pydantic 模型 `EntityOutput`/`EntityItem` 是**输出契约**——llm_runner 校验 LLM 返回的 JSON，
    结构不合法直接判失败（触发缩窗重试），而不是把脏数据放行入库；
  - `build_prompt(scene_text, few_shot=True)` 组装"公共前置 + 任务 + 枚举 + 约束 + 文本"，
    默认追加 3-shot 跨题材极简示例（2正1错，覆盖玄幻修仙/科幻超能）；
  - 实体类型枚举与 002 `entity.entity_type` DDL 完全一致（human/item/skill/spirit/task/faction/rule）。
"""
from pydantic import BaseModel, Field, field_validator

from novel.prompts.base import BASE_SYSTEM_PROMPT

# 实体类型枚举（与 002 entity.entity_type DDL 一致，勿改）
ENTITY_TYPES = ("human", "item", "skill", "spirit", "task", "faction", "rule")

_ENTITY_TYPE_HINT = " / ".join(ENTITY_TYPES)

# 体裁动态提示（003 P0-4，entity 试点）：
#   build_prompt(genre=...) 非 None 时追加；与 entity_prompt 既有静态体裁别名规则
#   （历史/科幻/武侠玄幻，见下方"别名处理规则"）**去重**，只补静态未覆盖的差异项。
#   genre 运行期来源（book 题材字段）登记 09/上传链路，本期不接线（避免死参数）。
_GENRE_HINTS = {
    "历史": "历史题材补充：官职/封号是身份属性，不单独成实体。（字号/谥号等别名规则已在上方静态规则中，不重复）",
    "现实": "现实题材：亲属、职业、机构是常见实体；普通日常物品/情绪/事件严格禁止抽取；人物以全名为规范名。",
    "科幻": "科幻题材补充：科技设备/组织/能力按语义归类，不硬塞玄幻枚举。（代号/型号等别名规则已在上方静态规则中）",
    "玄幻": "玄幻题材补充：能力/法器/境界按 7 类枚举归类。（称号/绰号等别名规则已在上方静态规则中）",
    "悬疑": "悬疑题材：注意侧面提及/传闻人物与真实出场的区分（base 铁律 6）；时间线索、不在场证明等事件边界从严。",
}

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见抽取错误（代词/地点/杂物/情绪/事件/编造/重复/类型误判）。
_ENTITY_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
二愣子姓韩名立，是村里一个普通的农家小孩。三叔工作的酒楼属于一个叫"七玄门"的江湖门派，前不久三叔正式成为七玄门的外门弟子，能推举适龄孩童去参加七玄门招收内门弟子的考验。
【正确输出】
{"entities": [
  {"name": "韩立", "aliases": ["二愣子"], "type": "human", "description": "农家小孩"},
  {"name": "三叔", "aliases": [], "type": "human", "description": "七玄门外门弟子"},
  {"name": "七玄门", "aliases": [], "type": "faction", "description": "外门内门分立的江湖门派"},
  {"name": "七玄门招收内门弟子的考验", "aliases": [], "type": "task", "description": "招收内门弟子考核"}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
韩萧是二十四号试验样本，被注入瓦尔基里溶液，那是萌芽组织的基因药剂，用来强化大脑。"快点去通知海拉女士，试验体活过来了！"实验室主管海拉带着研究员林维贤前来查看。
【正确输出】
{"entities": [
  {"name": "韩萧", "aliases": ["二十四号试验样本"], "type": "human", "description": "被注射药剂的试验体"},
  {"name": "海拉", "aliases": [], "type": "human", "description": "实验室主管"},
  {"name": "林维贤", "aliases": [], "type": "human", "description": "实验室研究员"},
  {"name": "萌芽组织", "aliases": [], "type": "faction", "description": "研发基因药剂的组织"},
  {"name": "瓦尔基里溶液", "aliases": [], "type": "item", "description": "强化大脑的基因药剂"}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
韩萧被押进一间空的小黑屋，大门随即锁死。屋里黑黢黢的，只剩他一人，他疼得龇牙咧嘴，心里满是焦虑，角落里还堆着一条破旧的棉被。
【常见错误 → 正确做法（以下内容禁止抽成实体）】
- 代词"他" → 不单列，归并到已明确的"韩萧"
- 地点"小黑屋""角落" → 不抽（地点由地点模块处理；location 不在 7 类枚举内）
- 普通杂物"破旧的棉被" → 不抽 item
- 抽象情绪"焦虑" → 不抽 rule
- 普通事件/行为"被押进、锁门" → 不抽 task（由时间线模块处理）
- 原文没出现的"瓦尔基里溶液" → 不编造、不凭原著脑补
- 同一实体"韩萧" → 只输出 1 条，禁止重复
- 类型误判 → 按语义选对类型（"瓦尔基里溶液"属 item 而非 skill；"七玄门"属 faction 而非 rule）
【正确输出】
{"entities": [{"name": "韩萧", "aliases": [], "type": "human", "description": "被押进小黑屋的人"}]}
"""


class EntityItem(BaseModel):
    """单条实体：规范名 + 别名[] + 类型 + 一句话描述。

    强约束：name 非空（min_length=1）、type 必须在枚举内（field_validator）——
    LLM 输出若违反，llm_runner 直接判失败触发缩窗重试，不放脏数据入库。
    """

    name: str = Field(min_length=1, description="实体规范名（全名/标准名，非空）")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；可选，空则跳过锚定校验）")
    aliases: list[str] = Field(default_factory=list, description="别名列表：全名/昵称/称号（如 五条悟/悟大人/六眼神子/现代最强咒术师）")
    type: str = Field(description=f"实体类型，选自枚举：{_ENTITY_TYPE_HINT}")
    description: str = Field(default="", description="一句话描述（原文依据摘要）")

    @field_validator("type")
    @classmethod
    def _type_must_be_in_enum(cls, v: str) -> str:
        if v not in ENTITY_TYPES:
            raise ValueError(f"非法实体类型 {v!r}，可选 {_ENTITY_TYPE_HINT}")
        return v


class EntityOutput(BaseModel):
    """entity 抽取的整体输出契约：一个 entities 列表。"""

    entities: list[EntityItem] = Field(default_factory=list)


def build_prompt(scene_text: str, *, few_shot: bool = True, genre: str | None = None) -> dict:
    """组装 entity 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（llm_runner 传入；缩窗重试时传入的是截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :param genre: 体裁（历史/现实/科幻/玄幻/悬疑，003 P0-4）；非 None 时追加 `_GENRE_HINTS[genre]`
        题材专属提示段（与静态规则去重）；默认 None 与现状逐字一致。
    :return: {"system_prompt", "prompt"} 供 BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取全部实体。\n",
        "实体类型枚举：",
        f"{_ENTITY_TYPE_HINT}\n",
        "输出 JSON 结构：",
        '{"entities": [{"name": 规范名, "aliases": [别名1, 别名2, ...], "type": 类型, "description": 一句话描述}]}\n',
        "要求：\n",
        "  1. 同一实体出现多个称呼时，规范名用最常用全名，其余放 aliases；\n",
        "  2. 原文没有的类型/描述置 null 或空串，不要编造；\n",
        "  3. 只输出 JSON，不要任何额外文本。\n\n",
        "抽取规则补充：\n",
        "实体类型语义说明（跨题材通用）：\n",
        "  1. human：人物，包括现实人物、历史人物、虚构角色、有人格的AI/灵体\n",
        "  2.  item：物品，包括普通器物、武器、科技设备、道具、文件、特殊物品\n",
        "  3. skill：能力/技艺，包括法术、武功、职业技能、科技能力、特殊天赋\n",
        "  4. spirit：非人类智慧体，包括鬼怪、灵体、神兽、无实体意识体；普通动物/现实生物不归类为此\n",
        "  5. task：任务/目标，包括剧情任务、使命、委托、行动目标；普通日常行为不归类为此\n",
        "  6. faction：势力/群体，包括组织、门派、家族、军队、社会团体、派系\n",
        "  7. rule：规则/设定，包括世界观规则、社会规则、自然规律、能力法则、制度；普通个人观点不归类为此\n\n",
        "【禁止抽取】以下内容不作为实体输出：\n",
        "1. 所有地点、场所（统一由地点模块处理）\n",
        "2. 所有剧情事件、时间节点（统一由时间线模块处理）\n",
        "3. 普通日常物品（如桌子、水杯、普通衣物）、纯数字、纯形容词\n",
        "4. 抽象情绪、感受、概念（如悲伤、正义、自由）\n",
        "5. 代词（他、她、它、他们）不生成独立实体，归并到文本中已明确的实体\n",
        "6. 未具名的泛指群体（村民们、士兵们、众人）不拆成多个个体实体\n\n",
        "别名处理规则：\n",
        "- 历史题材：字号、谥号、官职、封号、尊称全部归入别名，规范名使用正式全名\n",
        "- 科幻题材：代号、型号、别称归入别名，规范名使用官方正式名称\n",
        "- 武侠/玄幻题材：称号、绰号、外号归入别名\n",
        "- 同一实体多个称呼，规范名选取最通用、最正式的称呼\n\n",
    ]
    if genre in _GENRE_HINTS:
        parts.append(_GENRE_HINTS[genre] + "\n\n")
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_ENTITY_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
