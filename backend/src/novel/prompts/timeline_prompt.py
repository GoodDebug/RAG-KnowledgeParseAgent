# -*- coding: utf-8 -*-
"""
timeline 抽取专属 Prompt + JSON Schema（子任务 04）。

职责：从章节场景抽"剧情时间线"——stage/event 两级（事件可归属阶段）、global_sort、
start/end_chapter、time_desc、参与实体[]。产出同时支撑 timeline_event 与 timeline_event_entity 两张表。

设计（照 03 模板）：
  - `TimelineOutput`/`TimelineEvent` 强校验（event_level 二选一、global_sort ≥0）；
  - `result_field = "events"`；
  - parent 用阶段标题（persist 期解析 parent_event_id）、involved_entities 用实体名；
  - `build_prompt(scene_text, few_shot=True)` 默认追加 3-shot 跨题材极简示例（2正1错，覆盖玄幻修仙/科幻超能）。
"""
from pydantic import BaseModel, Field, field_validator

from novel.prompts.base import BASE_SYSTEM_PROMPT

_EVENT_LEVELS = ("stage", "event")

# 单章最大事件数上界（K，003 顶层计划外 P0-1）：
#  agent 输出的 global_sort 是"章内相对序号"（<K），07 persist 用
#  `chapter_index*K*S + scene_index*K + global_sort` 合成为全书全局序号（见 00 §4.b.2）。
_LOCAL_SORT_MAX = 1000

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见时间线抽取错误（静态描写/日常行为/想法当事件/编造/代词/层级/乱序）。
_TIMELINE_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
第二天中午时分，韩立背着半人高的木柴堆，从山里往家里赶。回家后见到三叔来访，三叔是七玄门外门弟子，能推举孩童参加七玄门招收内门弟子的考验。韩父最终答应让韩立参加考核，说一个月后三叔就来带韩立走。
【正确输出】
{"events": [
  {"event_level": "stage", "parent_title": null, "event_title": "离家赴考前的准备", "event_content": "韩立被推举参加七玄门内门弟子考验前的准备阶段", "time_desc": "三叔来访期间", "global_sort": 0, "start_chapter": 0, "end_chapter": 0, "involved_entities": ["韩立", "三叔", "韩父", "七玄门"], "narrative_type": "过渡", "plot_impact": "铺垫韩立拜入七玄门的起点"},
  {"event_level": "event", "parent_title": "离家赴考前的准备", "event_title": "韩立进山拣柴归来", "event_content": "韩立背着木柴从山里赶回家", "time_desc": "第二天中午", "global_sort": 1, "start_chapter": 0, "end_chapter": 0, "involved_entities": ["韩立"], "narrative_type": "过渡", "plot_impact": ""},
  {"event_level": "event", "parent_title": "离家赴考前的准备", "event_title": "三叔来访并获韩父应允", "event_content": "三叔推举韩立参加七玄门考核，韩父答应，约定一个月后启程", "time_desc": "三叔来访时", "global_sort": 2, "start_chapter": 0, "end_chapter": 0, "involved_entities": ["三叔", "韩父", "韩立", "七玄门"], "narrative_type": "转折", "plot_impact": "确立韩立赴考命运，推动拜师线"}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
二十四号试验样本，已注入瓦尔基里溶液，存活两分钟四十五秒，死亡时间，凌晨四点二十二分。韩萧豁然睁开双眼，"试验体活过来了！"白大褂呼叫警卫，韩萧挣脱束缚冲向门口，被警卫押进小黑屋。海拉随后前来查看试验体状态。
【正确输出】
{"events": [
  {"event_level": "stage", "parent_title": null, "event_title": "试验体苏醒与关押事件", "event_content": "韩萧从瓦尔基里溶液实验中苏醒，试图逃跑并被关押", "time_desc": "凌晨时分", "global_sort": 0, "start_chapter": 0, "end_chapter": 0, "involved_entities": ["韩萧", "海拉", "萌芽组织"], "narrative_type": "转折", "plot_impact": "试验体异变存活，开启主线"},
  {"event_level": "event", "parent_title": "试验体苏醒与关押事件", "event_title": "韩萧被注射瓦尔基里溶液", "event_content": "二十四号试验样本韩萧被注入瓦尔基里溶液", "time_desc": "苏醒前", "global_sort": 1, "start_chapter": 0, "end_chapter": 0, "involved_entities": ["韩萧", "瓦尔基里溶液"], "narrative_type": "", "plot_impact": ""},
  {"event_level": "event", "parent_title": "试验体苏醒与关押事件", "event_title": "韩萧苏醒并试图逃跑", "event_content": "韩萧苏醒，挣脱束缚冲向门口，被警卫电击制服", "time_desc": "凌晨四点二十二分", "global_sort": 2, "start_chapter": 0, "end_chapter": 0, "involved_entities": ["韩萧"], "narrative_type": "战斗", "plot_impact": "韩萧首次展现能力与处境"},
  {"event_level": "event", "parent_title": "试验体苏醒与关押事件", "event_title": "韩萧被押入小黑屋", "event_content": "韩萧被押进小黑屋关押，海拉前来查看试验体状态", "time_desc": "凌晨时分", "global_sort": 3, "start_chapter": 0, "end_chapter": 0, "involved_entities": ["韩萧", "海拉"], "narrative_type": "过渡", "plot_impact": "铺垫海拉与韩萧的初次接触"}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
韩立是村里一个普通的农家小孩，长得黑黑的不起眼。夜里他翻来覆去睡不着，想着明天要进山拣干柴。
【常见错误 → 正确做法（以下内容禁止照做）】
- 静态描写当事件："韩立是农家小孩" ✗ 设定/外貌不是剧情事件
- 普通日常当事件："翻来覆去睡不着" ✗ 日常行为不是剧情事件
- 内心想法当事件："想着明天进山拣干柴" ✗ 计划/想法未落地，不算事件
- 编造事件："韩立随三叔离开山村" ✗ 本段没发生，禁止凭原著脑补
- involved_entities 用代词："他" ✗ 归并到已明确的实体
- event_level 用枚举外值："scene" ✗ 只能 stage/event
- stage 的 parent_title 填了值 ✗ stage 自身 parent_title 应为 null
- global_sort 乱序 ✗ 按章内时间先后递增（global_sort 是章内相对序号）
- narrative_type 乱填（把"战斗"标成"升级"）✗ 依事件语义归类，无法归类填空串
- plot_impact 与事件无关（编造主线影响）✗ 无关或不明填空串
【正确输出】
{"events": []}
"""


class TimelineEvent(BaseModel):
    """单条时间线条目：阶段(stage)或事件(event)。"""

    event_level: str = Field(description="层级：stage（大阶段）/ event（具体事件）")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；可选，空则跳过锚定校验）")
    parent_title: str | None = Field(default=None, description="所属阶段标题；stage 自身为 null")
    event_title: str = Field(min_length=1, description="事件/阶段标题")
    event_content: str = Field(default="", description="事件详细内容")
    time_desc: str = Field(default="", description="文本内时间描述（怀玉篇/三年后）")
    global_sort: int = Field(ge=0, lt=_LOCAL_SORT_MAX, description="章内相对序号（本段事件 0 起递增；全书全局序号由 persist 层生成，禁止推算全书）")
    start_chapter: int | None = Field(default=None, ge=0, description="起始章节（由流水线决定，缺省=当前章）")
    end_chapter: int | None = Field(default=None, ge=0, description="结束章节（null/0=进行中）")
    involved_entities: list[str] = Field(default_factory=list, description="参与实体名列表")
    # 二阶段 03：叙事类型 + 剧情作用（L4 叙事功能）
    narrative_type: str = Field(default="", description="叙事类型：升级/打脸/揭秘/转折/战斗/过渡（提示枚举，可容错）")
    plot_impact: str = Field(default="", description="对主线剧情的影响（一句话）")

    @field_validator("event_level")
    @classmethod
    def _level_must_be_in_enum(cls, v: str) -> str:
        if v not in _EVENT_LEVELS:
            raise ValueError(f"非法 event_level {v!r}，可选 {'/'.join(_EVENT_LEVELS)}")
        return v


class TimelineOutput(BaseModel):
    """timeline 抽取的整体输出契约。"""

    events: list[TimelineEvent] = Field(default_factory=list)


def build_prompt(scene_text: str, *, few_shot: bool = True) -> dict:
    """组装 timeline 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（缩窗重试时传入截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取**剧情时间线**。\n",
        "输出 JSON 结构：",
        '{"events": [{"event_level": "stage|event", "parent_title": "所属阶段标题", '
        '"event_title": 标题, "event_content": 内容, "time_desc": 时间描述, '
        '"global_sort": 序号, "start_chapter": 起章, "end_chapter": 止章, '
        '"involved_entities": [实体名], "narrative_type": 叙事类型, "plot_impact": 剧情作用}]}\n',
        "要求：\n",
        "  1. event_level 二选一：stage=大阶段（parent_title 为 null），event=具体事件（parent_title 填所属阶段标题）；\n",
        "  2. global_sort 为本段事件**章内相对序号**（0 起递增，按文本出现先后）；**不要推算全书全局序号**（由 persist 层结合章节号生成）；\n",
        "  3. involved_entities 用文本中出现的实体名；只输出 JSON。\n\n",
        "抽取规则补充：\n",
        "1. 只抽**剧情事件**：推动情节/人物处境变化的事实。静态设定、外貌描写、普通日常行为、未落地的内心计划不是事件。\n",
        "2. stage 是若干相关事件的上位阶段，本段没有明显阶段时可省去 stage，只输出 event。\n",
        "3. 本段无有效剧情事件时返回空数组，禁止生成占位事件。\n",
        "4. time_desc 兼容所有题材：历史年号 / 干支 / 星历 / 相对时间（如\"三年后\"）均可直接填入。\n",
        "5. start_chapter / end_chapter 本段无法判断统一填 0，禁止猜测章节编号。\n",
        "6. parent_title 必须引用本段出现的 stage 标题，禁止编造上级阶段。\n",
        "7. involved_entities 仅填直接参与的实体，传闻提及未参与不填。\n",
        "8. narrative_type：本事件在网文叙事中的类型——升级（实力/境界提升）/ 打脸（反派被打脸）/ 揭秘（真相/身份揭示）/ 转折（剧情反转）/ 战斗 / 过渡；无法归类填空串。\n",
        "9. plot_impact：本事件对主线剧情的影响（一句话，如\"确立主角阵营归属，推动拜师线\"）；无关或不明填空串。\n\n",
    ]
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_TIMELINE_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
