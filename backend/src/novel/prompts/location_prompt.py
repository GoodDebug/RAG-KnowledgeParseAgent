# -*- coding: utf-8 -*-
"""
location 抽取专属 Prompt + JSON Schema（子任务 05）。

职责：抽地点层级（name/level 1-4/parent 按名/description）+ 本章状态（status_desc/special_rules）。
产出同时支撑 location（注册表，04 复用）与 location_snapshot 两张表。
"""
from pydantic import BaseModel, Field, field_validator

from novel.prompts.base import BASE_SYSTEM_PROMPT

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见地点抽取错误（势力当地点/层级错/编造上级/字段混淆/编规则/代词/重复）。
_LOCATION_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
二愣子姓韩名立，住在山边小村里的一间土屋。他背着半人高的木柴堆，从山里往家里赶。土屋的墙是黄泥糊成的，屋顶是茅草和烂泥。
【正确输出】
{"locations": [
  {"name": "山边小村", "level": 3, "parent_name": null, "description": "韩立居住的小村庄", "status_desc": "", "special_rules": ""},
  {"name": "土屋", "level": 4, "parent_name": "山边小村", "description": "黄泥墙茅草顶的土屋，韩立的家", "status_desc": "", "special_rules": ""},
  {"name": "山里", "level": 4, "parent_name": "山边小村", "description": "韩立进山拣柴的山地", "status_desc": "", "special_rules": ""}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
海蓝星的萌芽组织瓦尔基里实验室里，韩萧苏醒后撞开房门，被十几个警卫押进一间空的小黑屋，警卫锁死大门后尽数离开。
【正确输出】
{"locations": [
  {"name": "海蓝星", "level": 1, "parent_name": null, "description": "萌芽组织活动的星球", "status_desc": "", "special_rules": ""},
  {"name": "瓦尔基里实验室", "level": 4, "parent_name": "海蓝星", "description": "萌芽组织的人体实验场所", "status_desc": "因试验体韩萧苏醒进入警戒", "special_rules": ""},
  {"name": "小黑屋", "level": 4, "parent_name": "瓦尔基里实验室", "description": "空置的关押房间", "status_desc": "门已锁死，关押着韩萧", "special_rules": ""}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
韩萧被押进小黑屋，心里满是焦虑。他想起萌芽组织的瓦尔基里实验室，那地方有重症监护风格的房间。
【常见错误 → 正确做法（以下内容禁止照做）】
- 势力当地点："萌芽组织" ✗ 是势力，由实体模块处理，不抽地点
- 层级错：把"小黑屋"标 level 1 ✗ 具体房间应 level 4
- 编造上级：给"海蓝星"填 parent"银河系" ✗ 上级名须原文出现，没有则 null
- 字段混淆：把"被关押"状态写进 description ✗ 本章状态放 status_desc，description 放地点固有描述
- 编 special_rules："禁止进入" ✗ 原文没提不编
- 代词当地点名："那地方" ✗ 不生成独立地点
- 重复输出："实验室"与"瓦尔基里实验室"两条 ✗ 同一地点只输出规范名一条
【正确输出】
{"locations": [
  {"name": "小黑屋", "level": 4, "parent_name": null, "description": "空置的关押房间", "status_desc": "关押着韩萧", "special_rules": ""},
  {"name": "瓦尔基里实验室", "level": 4, "parent_name": null, "description": "萌芽组织的人体实验场所，有重症监护风格的房间", "status_desc": "", "special_rules": ""}
]}
"""


class LocationItem(BaseModel):
    """单条地点：层级信息 + 本章状态。"""

    name: str = Field(min_length=1, description="地点名")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；可选，空则跳过锚定校验）")
    level: int = Field(ge=1, le=4, description="层级：1世界/2大陆/3城池/4具体场景")
    parent_name: str | None = Field(default=None, description="上级地点名（层级树）；最高层为 null")
    description: str = Field(default="", description="地点描述")
    status_desc: str = Field(default="", description="本章地点状态（开启/封印/损毁/阵法激活）")
    special_rules: str = Field(default="", description="本章生效特殊规则")

    @field_validator("level")
    @classmethod
    def _level_in_range(cls, v: int) -> int:
        if not (1 <= v <= 4):
            raise ValueError(f"非法 location_level {v}，可选 1..4")
        return v


class LocationOutput(BaseModel):
    """location 抽取的整体输出契约。"""

    locations: list[LocationItem] = Field(default_factory=list)


def build_prompt(scene_text: str, *, few_shot: bool = True) -> dict:
    """组装 location 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（缩窗重试时传入截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取**地点**及其层级、本章状态。\n",
        "输出 JSON 结构：",
        '{"locations": [{"name": 地点名, "level": 1-4, "parent_name": 上级名或null, '
        '"description": 描述, "status_desc": 本章状态, "special_rules": 特殊规则}]}\n',
        "要求：\n",
        "  1. level 取 1世界/2大陆/3城池/4具体场景；\n",
        "  2. parent_name 用文本中出现的上级地点名；\n",
        "  3. 原文没提的字段填空串/null；只输出 JSON。\n\n",
        "抽取规则补充：\n",
        "地点层级通用标准：\n",
        "1 级：世界/星球/时代全域\n",
        "2 级：大陆/国家/星域/大区\n",
        "3 级：城市/省份/星域据点/大型区域\n",
        "4 级：具体建筑、场景、房间、地点细节\n",
        "parent_name 填写本段中出现的上级地点名称，最高层级填 null\n\n",
        "地点判定边界：\n",
        "1. 势力/组织（门派、公司、军队）不是地点，由实体模块处理；其所在场所才算地点。\n",
        "2. status_desc 只写本章明确的地点状态（开启/封印/损毁/戒备），description 写地点固有属性。\n",
        "3. special_rules 只写本章明确生效的特殊规则，原文没提填空串。\n\n",
    ]
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_LOCATION_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
