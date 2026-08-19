# -*- coding: utf-8 -*-
"""
entity_snapshot 抽取专属 Prompt + JSON Schema（子任务 04 → 二阶段 02 升级）。

职责：从章节场景抽"每实体本章状态"——status_desc（觉醒反转术式/肉身孱弱/一级战力）
+ attributes（**固定键结构**）+ 三态标注 + 增量提取（注入历史已入库快照摘要，最新可用、背景参考）。

二阶段 02（提取层·主观层与三态）升级：
  - attributes 固定键：physique / psychology{surface/inner/change} /
    action{key_behavior/key_line/gain_loss} / items[] / skills[] / doubts[] / conflicts[]
    （全可选 + `extra="allow"`，非人物实体容错）；
  - 主观层铁律：inner_emotion / doubts / conflicts 必须携带 source_fragment，无锚点不填；
  - three_state：fact（原文直证）/ inference（合理推断，主观层默认）/ review（不确定或弱锚点）——
    LLM 标注，聚合层（04）按字段类型 + 锚点确定性重算为最终值；
  - 增量提取：`build_prompt(scene_text, *, few_shot=True, prev_snapshot_context=None)`，
    非 None 时追加「历史已入库快照摘要」块（背景参考；本章原文明确描述的必须完整输出，防漏）。

设计（照 03 entity_prompt 模板）：
  - Pydantic `SnapshotOutput`/`SnapshotItem` 强校验（entity_name 非空、attributes 结构化）；
  - `result_field = "snapshots"`，llm_runner 校验后取该字段；
  - 实体按「名」引用（persist 期跨章 name→entity_id 解析，顶层 Spec §4.0）；
  - `build_prompt(scene_text, few_shot=True)` 默认追加 3-shot 跨题材极简示例（2正1错，覆盖玄幻修仙/科幻超能）。
"""
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from novel.prompts.base import BASE_SYSTEM_PROMPT

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - attributes 用固定键结构；主观层（inner/doubts/conflicts）带 source_fragment；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见快照抽取错误（含二阶段新增）。
_SNAPSHOT_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
七玄门是一个江湖门派，有外门和内门之分，五年一次的招收内门弟子测试下个月就要开始了。三叔是七玄门外门弟子，能推举适龄孩童去参加七玄门招收内门弟子的考验。一向老实巴交的韩父，听了"江湖""门派"之类的话心里犹豫不决，但当听到每月能有一两银子拿、还能成为体面人，终于拿定了主意，答应让韩立去参加考核。三叔见韩父应承，心里很高兴，说一个月后就来带韩立走。
【正确输出】
{"snapshots": [
  {"entity_name": "韩立", "status_desc": "被应允参加七玄门内门弟子考验，一个月后随三叔离家",
   "source_fragment": "答应让韩立去参加考核", "three_state": "fact",
   "attributes": {"action": {"key_behavior": "应允随三叔赴考", "key_line": "", "gain_loss": ""},
                  "items": [], "skills": [], "doubts": [], "conflicts": []}},
  {"entity_name": "韩父", "status_desc": "由犹豫不决转为答应韩立参加考核",
   "source_fragment": "心里犹豫不决，但当听到每月能有一两银子拿、还能成为体面人，终于拿定了主意",
   "three_state": "fact",
   "attributes": {"psychology": {"surface_emotion": "犹豫", "inner_emotion": "看重每月银两与体面（依据：听到银子与体面才决定）",
                                 "mental_change": "从犹豫转为答应"},
                  "items": [], "skills": [], "doubts": [], "conflicts": []}},
  {"entity_name": "三叔", "status_desc": "说动韩父应承，约定一个月后带韩立走",
   "source_fragment": "三叔见韩父应承，心里很高兴，说一个月后就来带韩立走", "three_state": "fact",
   "attributes": {"action": {"key_behavior": "说动韩父、约定带韩立走", "key_line": "", "gain_loss": ""},
                  "items": [], "skills": [], "doubts": [], "conflicts": []}},
  {"entity_name": "七玄门", "status_desc": "五年一次的招收内门弟子测试下个月开始",
   "source_fragment": "五年一次的招收内门弟子测试下个月就要开始了", "three_state": "fact",
   "attributes": {"items": [], "skills": [], "doubts": [], "conflicts": []}}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
韩萧豁然睁开双眼，他是被注入瓦尔基里溶液的二十四号试验样本，竟异变存活了下来。眼前跳出一行半虚幻的蓝光文字：[你已注射【瓦尔基里溶液】，耐力潜力+1，获得专长——高度专注，获得专长——低级精神韧性]。姓名：韩萧。种族：碳基人类。模板：npc。总等级：1。瓦尔基里溶液是一种用来强化大脑的基因药剂。
【正确输出】
{"snapshots": [
  {"entity_name": "韩萧", "status_desc": "被注射瓦尔基里溶液后异变存活，获得专长高度专注与低级精神韧性",
   "source_fragment": "竟异变存活了下来", "three_state": "fact",
   "attributes": {"physique": {"health_status": "存活", "power_level": "总等级 1", "body_special": "溶液异变"},
                  "action": {"key_behavior": "苏醒，获得专长", "key_line": "", "gain_loss": "耐力潜力+1"},
                  "items": [], "skills": ["高度专注", "低级精神韧性"], "doubts": [], "conflicts": []}},
  {"entity_name": "瓦尔基里溶液", "status_desc": "被注入试验体韩萧体内",
   "source_fragment": "被注入瓦尔基里溶液的二十四号试验样本", "three_state": "fact",
   "attributes": {"items": [], "skills": [], "doubts": [], "conflicts": [], "类型": "基因药剂", "作用": "强化大脑"}}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
韩立躺在土炕上，翻来覆去睡不着。他想着明天要进山拣干柴，还要帮妹妹多拣些红浆果。韩母在隔壁屋里唠唠叨叨，韩父抽着旱烟杆一声不吭。韩立今年十岁，是个普通的农家小孩。
【常见错误 → 正确做法（以下内容禁止照做）】
- entity_name 用代词"他" → 归并到"韩立"，不单列
- entity_name 编造原文没出现的实体（如"林维贤"）→ 只抽本段明确出现的实体
- 把固有不变特征写进 status_desc（"韩立是十岁农家小孩"）→ 固有特征放 attributes，status_desc 只写本章变化/状态
- 主观层无锚点硬猜（如韩母"内心担忧韩立前程"原文未提）→ inner/doubts 无依据必须置空，禁止脑补
- attributes 结构不符（自由乱键如 "{"战力": 很强}"）→ 按固定键输出；题材专属键可附加
- three_state 误标（把推断标 "fact"）→ 合理推断标 "inference"；不确定标 "review"
- 原文没提的属性硬猜（韩母"身份：韩立母亲"）→ attributes 空对象 {}，禁止猜测填充
- 背景出场、无本章状态变化的实体硬凑快照（韩母、韩父）→ 不生成快照
- 把事件/目标当快照（"帮妹妹拣红浆果"作为红浆果的快照）→ 事件由时间线模块处理
- 同一实体重复输出多条（"韩立"×2）→ 每实体每章只输出 1 条
- attributes 不是 JSON 对象（用字符串/列表）→ 必须是 {} 或 {"键": 值} 结构
【正确输出】
{"snapshots": [
  {"entity_name": "韩立", "status_desc": "辗转难眠，计划明早进山拣干柴、帮妹妹拣红浆果",
   "source_fragment": "翻来覆去睡不着。他想着明天要进山拣干柴", "three_state": "fact",
   "attributes": {"psychology": {"surface_emotion": "焦虑", "inner_emotion": "", "mental_change": ""},
                  "action": {"key_behavior": "计划明早进山拣柴", "key_line": "", "gain_loss": ""},
                  "items": [], "skills": [], "doubts": [], "conflicts": []}}
]}
"""


# ========== 输出契约：固定键结构 ==========


class SnapshotPhysique(BaseModel):
    """客观体质：本章身体/战力状态（原文直证 → fact）。"""

    health_status: str = Field(default="", description="健康/轻伤/重伤/中毒/濒死")
    power_level: str = Field(default="", description="当前修为战力等级")
    body_special: str = Field(default="", description="封印/蜕变/负面状态等特殊状态")


class SnapshotPsychology(BaseModel):
    """主观心理层（inner/change 属「合理推断」→ inference，必须带原文锚点）。"""

    surface_emotion: str = Field(default="", description="表面情绪（原文可证）")
    inner_emotion: str = Field(default="", description="内心真实情绪/隐秘想法（合理推断，需 source_fragment 支撑）")
    mental_change: str = Field(default="", description="本章认知/心态变化（相对上一章，需源支撑）")


class SnapshotAction(BaseModel):
    """本章行动与得失。"""

    key_behavior: str = Field(default="", description="本章关键行为/决策")
    key_line: str = Field(default="", description="本章关键台词（原文 verbatim 摘要）")
    gain_loss: str = Field(default="", description="本章得失（获得/失去/代价）")


class SnapshotAttributes(BaseModel):
    """attributes 固定键结构：体质 + 心理双层 + 行动得失 + 持有/技能 + 疑点 + 卷入冲突。

    全部可选：非人物实体（物品/势力等）只填适用键；题材专属附加键经 extra="allow" 容忍。
    """

    model_config = ConfigDict(extra="allow")

    physique: Optional[SnapshotPhysique] = None
    psychology: Optional[SnapshotPsychology] = None
    action: Optional[SnapshotAction] = None
    items: list[str] = Field(default_factory=list, description="持有核心物品名")
    skills: list[str] = Field(default_factory=list, description="掌握核心技能名")
    doubts: list[str] = Field(default_factory=list, description="本章新增疑点/反常行为（合理推断，需锚点）")
    conflicts: list[str] = Field(default_factory=list, description="当前卷入冲突名（合理推断，需锚点）")


class SnapshotItem(BaseModel):
    """单条实体快照：实体名 + 状态 + 结构化 attributes + 三态。"""

    entity_name: str = Field(min_length=1, description="实体名（引用已抽取的实体规范名，非空）")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；主观层必须有）")
    status_desc: str = Field(default="", description="本章内该实体的状态描述（原文直证）")
    attributes: SnapshotAttributes = Field(
        default_factory=SnapshotAttributes, description="结构化属性（固定键）"
    )
    three_state: Literal["fact", "inference", "review"] = Field(
        default="fact",
        description="三态：fact=原文直证 / inference=合理推断（主观层默认）/ review=不确定或弱锚点",
    )


class SnapshotOutput(BaseModel):
    """entity_snapshot 抽取的整体输出契约。"""

    snapshots: list[SnapshotItem] = Field(default_factory=list)


def build_prompt(
    scene_text: str,
    *,
    few_shot: bool = True,
    prev_snapshot_context: str | None = None,
) -> dict:
    """组装 entity_snapshot 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（缩窗重试时传入截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :param prev_snapshot_context: 历史已入库快照摘要（最新可用，因章节并行解构可能非紧邻上一章；
        增量提取用，非 None 时追加历史块；默认 None 保持向后兼容——不含增量块）
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取文本中**出现过**的实体的本章状态快照。\n",
        "输出 JSON 结构：",
        '{"snapshots": [{"entity_name": 实体名, "status_desc": 本章状态描述, '
        '"source_fragment": 支撑原文片段, "three_state": "fact|inference|review", '
        '"attributes": {固定键结构}}]}\n',
        "要求：\n",
        "  1. 实体名引用原文中出现的名称，不编造未出现实体；\n",
        "  2. attributes 用固定键结构（见下），原文没提的键省略或置空；\n",
        "  3. 只输出 JSON，不要任何额外文本。\n\n",
        "抽取规则补充：\n",
        "1. status_desc 仅填写本段文本中明确发生的状态变化（如受伤、升职、装备更新）；人物固有不变特征不要写入状态描述。\n",
        "2. attributes 固定键结构（人物示例）：\n",
        '   "physique":   {"health_status": "健康/轻伤/重伤", "power_level": "当前修为战力", "body_special": "封印/蜕变等"}\n',
        '   "psychology": {"surface_emotion": "表面情绪", "inner_emotion": "内心真实情绪", "mental_change": "本章心态变化"}\n',
        '   "action":     {"key_behavior": "本章关键行为", "key_line": "本章关键台词", "gain_loss": "本章得失"}\n',
        '   "items": ["持有物品名"], "skills": ["掌握技能名"], "doubts": ["本章新增疑点"], "conflicts": ["当前卷入冲突"]\n',
        "   非人物实体（物品/势力/技能等）只填适用键，其余省略；题材专属键（如物品的类型/作用）可附加。\n",
        "3. 主观层铁律：psychology.inner_emotion / doubts / conflicts 属「合理推断」——\n",
        "   必须有原文依据，并把支撑片段写入 source_fragment；原文未暗示 → 置空，禁止硬猜。\n",
        "4. three_state 标注：原文直接表述 → \"fact\"；据原文合理推断（内心想法/疑点/冲突）→ \"inference\"；\n",
        "   不确定或仅有弱暗示 → \"review\"。\n",
        "5. 仅输出本段文本明确出现的实体，禁止生成本段未提及实体的快照。\n",
        "6. 代词不生成独立快照，归并到已明确的实体；背景出场、无本章状态变化的实体不硬凑快照。\n\n",
    ]
    if prev_snapshot_context:
        # 增量参考块。口径说明：因章节并行解构，get_prev_snapshots 读到的是
        # 「最新已入库」状态，可能非紧邻上一章——因此强制"本章原文明确描述的必须完整输出"，
        # 摘要仅作背景参考，避免 LLM 因参考陈旧摘要而漏输出本章真实状态。
        parts.append(
            "【历史已入库快照摘要（最新可用，因章节并行解构可能非紧邻上一章；仅供了解该实体已入库状态）】\n"
        )
        parts.append(prev_snapshot_context)
        parts.append("\n")
        parts.append(
            "【增量约束】\n"
            "- 本章原文**明确描述的实体状态必须完整输出**，不得因参考摘要省略；\n"
            "- 仅当原文未再提及某实体时，才可依据摘要判断其未变化而不生成该实体快照；\n"
            "- 有变化/新增的字段照常输出。\n\n"
        )
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_SNAPSHOT_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
