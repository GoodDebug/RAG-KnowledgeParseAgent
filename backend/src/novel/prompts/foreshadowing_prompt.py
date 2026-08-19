# -*- coding: utf-8 -*-
"""
foreshadowing 抽取专属 Prompt + JSON Schema（子任务 05）。

职责：抽伏笔埋设/回收——title/desc/setup_chapter/setup_event_title/status/involved_entities[]。
setup_event_title 用事件标题（persist 期解析 event_id，06 跨章 resolver）。
"""
from pydantic import BaseModel, Field, field_validator

from novel.prompts.base import BASE_SYSTEM_PROMPT

_FS_STATUS = ("pending", "revealed", "abandoned")

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见伏笔抽取错误（静态/设定当伏笔/编造/状态误判/代词/重复）。
_FORESHADOWING_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
三叔说，五年一次的"七玄门"招收内门弟子测试下个月就要开始了，他已经推举了韩立。韩立从未想到，此次出去后，他竟然走上了一条与凡人不同的仙业大道，走出了自己的修仙之路。
【正确输出】
{"foreshadowings": [
  {"title": "七玄门招收内门弟子测试将至", "description": "三叔推举韩立参加七玄门招收内门弟子的测试", "setup_chapter": null, "setup_event_title": null, "status": "pending", "involved_entities": ["韩立", "七玄门", "三叔"], "foreshadowing_type": "剧情", "concealment_level": 3, "misleading_info": ""},
  {"title": "韩立踏上修仙之路", "description": "韩立此次出去后将走上与凡人不同的仙业大道", "setup_chapter": null, "setup_event_title": null, "status": "pending", "involved_entities": ["韩立"], "foreshadowing_type": "剧情", "concealment_level": 5, "misleading_info": ""}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
韩萧看着半透明的蓝色光幕：模板npc，1.0版本公测开启倒计时358天11小时03分钟。他心中暗想，距离公测还有一年，在玩家降临前，自己还有一年时间可以准备。瓦尔基里溶液致死率高达百分之七十，可在他身上却产生了异变。
【正确输出】
{"foreshadowings": [
  {"title": "公测开启玩家降临", "description": "距离公测还有一年，玩家降临前韩萧有准备时间", "setup_chapter": null, "setup_event_title": null, "status": "pending", "involved_entities": ["韩萧"], "foreshadowing_type": "剧情", "concealment_level": 4, "misleading_info": ""},
  {"title": "韩萧体内的溶液异变", "description": "致死率高达百分之七十的瓦尔基里溶液在韩萧身上产生异变", "setup_chapter": null, "setup_event_title": null, "status": "pending", "involved_entities": ["韩萧", "瓦尔基里溶液"], "foreshadowing_type": "能力", "concealment_level": 6, "misleading_info": ""}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
村里人都说七玄门招收内门弟子测试下个月开始，三叔已经推举了韩立。韩立是个聪明的孩子，长得黑黑的不起眼。
【常见错误 → 正确做法（以下内容禁止照做）】
- 静态特征当伏笔："韩立很聪明、长得黑黑的不起眼" ✗ 人物设定不是伏笔
- 背景设定当伏笔："村里人都说测试下月开始" 本身不是伏笔，而是"测试将至"埋设的载体 ✗ 只抽有明确指向的
- 编造伏笔："韩立会成为七玄门掌门" ✗ 原文没暗示，禁止凭原著脑补
- 状态误判：把 pending 标成 revealed ✗ 测试还没发生，属埋设
- involved_entities 用代词："他" ✗ 归并到已明确的实体
- 重复输出：同一伏笔输出多条 ✗ 每条伏笔只 1 条
- 类型误判：把人物设定标成"人物伏笔" ✗ foreshadowing_type 依指向分类（道具/人物/剧情/世界观/细节/能力/关系/冲突/时间线/规则等）
- concealment_level 越界（0 或 11）✗ 只能 1-10
- 误导无依据硬编 ✗ misleading_info 无原文误导线索填空串
【正确输出】
{"foreshadowings": [
  {"title": "七玄门招收内门弟子测试将至", "description": "三叔推举韩立，测试下月开始", "setup_chapter": null, "setup_event_title": null, "status": "pending", "involved_entities": ["韩立", "七玄门", "三叔"], "foreshadowing_type": "剧情", "concealment_level": 3, "misleading_info": ""}
]}
"""


class ForeshadowingItem(BaseModel):
    """单条伏笔。"""

    title: str = Field(min_length=1, description="伏笔名")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；可选，空则跳过锚定校验）")
    description: str = Field(default="", description="伏笔内容描述")
    setup_chapter: int | None = Field(default=None, ge=0, description="埋设章节（由流水线决定，缺省=当前章）")
    setup_event_title: str | None = Field(default=None, description="埋设事件标题（persist 期解析 event_id）")
    status: str = Field(default="pending", description="状态：pending/revealed/abandoned")
    involved_entities: list[str] = Field(default_factory=list, description="涉及实体名")
    # 二阶段 03：伏笔属性（L4 悬念感）
    foreshadowing_type: str = Field(default="", description="伏笔类型：道具/人物/剧情/世界观/细节/能力/关系/冲突/时间线/规则等（提示枚举）")
    concealment_level: int | None = Field(default=None, ge=1, le=10, description="埋设隐蔽度 1-10（1明显~10极隐蔽）")
    misleading_info: str = Field(default="", description="误导信息（迷惑读者的虚假线索；无则空串）")

    @field_validator("status")
    @classmethod
    def _status_in_enum(cls, v: str) -> str:
        if v not in _FS_STATUS:
            raise ValueError(f"非法伏笔状态 {v!r}，可选 {'/'.join(_FS_STATUS)}")
        return v


class ForeshadowingOutput(BaseModel):
    """foreshadowing 抽取的整体输出契约。"""

    foreshadowings: list[ForeshadowingItem] = Field(default_factory=list)


def build_prompt(scene_text: str, *, few_shot: bool = True) -> dict:
    """组装 foreshadowing 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（缩窗重试时传入截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取**伏笔**（埋设或回收）。\n",
        "输出 JSON 结构：",
        '{"foreshadowings": [{"title": 伏笔名, "description": 内容, "setup_chapter": 埋设章, '
        '"setup_event_title": 埋设事件标题, "status": "pending", "involved_entities": [实体名], '
        '"foreshadowing_type": 类型, "concealment_level": 隐蔽度, "misleading_info": 误导}]}\n',
        "要求：\n",
        "  1. status 选自 pending/revealed/abandoned；\n",
        "  2. setup_event_title 用文本中出现的剧情事件标题；\n",
        "  3. 原文没提的字段填空串/null；只输出 JSON。\n\n",
        "抽取规则补充：\n",
        "伏笔判定规则：\n",
        "1. 埋设：本段提到的细节，明显指向后续未发生的剧情\n",
        "2. revealed：本段回收了前文埋设的伏笔\n",
        "3. abandoned：本段明确废弃了之前的伏笔线索\n",
        "4. 仅提取有明确伏笔指向的内容，普通剧情铺垫、人物设定、背景描写不作为伏笔\n",
        "伏笔属性：\n",
        "5. foreshadowing_type：道具/人物/剧情/世界观/细节/能力/关系/冲突/时间线/规则 等分类；无法归类填空串。\n",
        "6. concealment_level：埋设隐蔽度 1-10（1=一眼看出，10=极隐蔽）；依埋设方式（自然提及/侧面暗示/误导）判断。\n",
        "7. misleading_info：原文为迷惑读者埋下的虚假线索（如\"看似是 A 原因，实为 B\"）；无则空串。\n\n",
    ]
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_FORESHADOWING_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
