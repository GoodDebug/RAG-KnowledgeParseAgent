# -*- coding: utf-8 -*-
"""
rule_check 抽取专属 Prompt + JSON Schema（子任务 05）。

职责：抽设定规则校验点——rule_name/rule_type(cap/cost/balance_lock/condition/other)/
rule_content/subject_entity_name/subject_ability/valid_from/to_chapter。
subject_entity_name 用实体名（persist 期解析 entity_id，06 跨章 resolver）。
"""
from pydantic import BaseModel, Field, field_validator

from novel.prompts.base import BASE_SYSTEM_PROMPT

_RULE_TYPES = ("cap", "cost", "balance_lock", "condition", "other")

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见规则抽取错误（特质当规则/类型误判/改写原文/编造/代词/重复）。
_RULE_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
三叔能够推举7岁到12岁的孩童去参加七玄门招收内门弟子的考验。五年一次的"七玄门"招收内门弟子测试，下个月就要开始了。
【正确输出】
{"rules": [
  {"rule_name": "七玄门招收内门弟子的年龄条件", "rule_type": "condition", "rule_content": "推举7岁到12岁的孩童去参加七玄门招收内门弟子的考验", "subject_entity_name": "七玄门", "subject_ability": "招收内门弟子", "valid_from_chapter": null, "valid_to_chapter": 0},
  {"rule_name": "七玄门招收内门弟子测试的周期", "rule_type": "condition", "rule_content": "五年一次的七玄门招收内门弟子测试", "subject_entity_name": "七玄门", "subject_ability": "招收内门弟子", "valid_from_chapter": null, "valid_to_chapter": 0}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
韩萧记得《星海》的痛觉调节上限最高是40%，超过40%就会对玩家的神经造成损伤，游戏舱有监控玩家体征的功能，发生这种故障按理说已经强制断线了。瓦尔基里溶液致死率高达百分之七十。
【正确输出】
{"rules": [
  {"rule_name": "《星海》痛觉调节上限", "rule_type": "cap", "rule_content": "痛觉调节上限最高是40%，超过40%会对玩家的神经造成损伤", "subject_entity_name": "韩萧", "subject_ability": "痛觉调节", "valid_from_chapter": null, "valid_to_chapter": 0},
  {"rule_name": "瓦尔基里溶液致死率", "rule_type": "other", "rule_content": "瓦尔基里溶液致死率高达百分之七十", "subject_entity_name": "瓦尔基里溶液", "subject_ability": "", "valid_from_chapter": null, "valid_to_chapter": 0}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
韩立是个聪明的孩子，村里人都这么夸他。三叔能推举7岁到12岁的孩童去参加七玄门的考验。
【常见错误 → 正确做法（以下内容禁止照做）】
- 人物特质当规则："韩立很聪明" ✗ 人物设定不是规则
- 类型误判：把"7岁到12岁"年龄条件标成 cap ✗ 是生效条件，应 condition
- 改写原文：把规则内容改成"只有七到十二岁的孩子才能参加" ✗ rule_content 尽量引用原文
- 编造规则："七玄门不收女弟子" ✗ 原文没提，禁止编造
- subject_entity_name 用代词："他" ✗ 归并到已明确的实体
- subject 用原文没出现的实体 ✗
- 重复输出：同一规则输出多条 ✗ 每条规则只 1 条
【正确输出】
{"rules": [
  {"rule_name": "七玄门招收内门弟子的年龄条件", "rule_type": "condition", "rule_content": "推举7岁到12岁的孩童去参加七玄门的考验", "subject_entity_name": "七玄门", "subject_ability": "招收内门弟子", "valid_from_chapter": null, "valid_to_chapter": 0}
]}
"""


class RuleItem(BaseModel):
    """单条设定规则。"""

    rule_name: str = Field(min_length=1, description="规则名")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；可选，空则跳过锚定校验）")
    rule_type: str = Field(default="other", description=f"规则类型：{'/'.join(_RULE_TYPES)}")
    rule_content: str = Field(min_length=1, description="能力上限/代价/平衡锁原文")
    subject_entity_name: str | None = Field(default=None, description="规则适用实体名（persist 期解析 entity_id）")
    subject_ability: str = Field(default="", description="适用能力/术式")
    valid_from_chapter: int | None = Field(default=None, ge=1, description="规则生效起始章节（由流水线决定，缺省=当前章）")
    valid_to_chapter: int = Field(ge=0, default=0, description="生效结束章节（0=永久有效）")

    @field_validator("rule_type")
    @classmethod
    def _rule_type_in_enum(cls, v: str) -> str:
        if v not in _RULE_TYPES:
            raise ValueError(f"非法 rule_type {v!r}，可选 {'/'.join(_RULE_TYPES)}")
        return v


class RuleOutput(BaseModel):
    """rule_check 抽取的整体输出契约。"""

    rules: list[RuleItem] = Field(default_factory=list)


def build_prompt(scene_text: str, *, few_shot: bool = True) -> dict:
    """组装 rule_check 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（缩窗重试时传入截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取**设定规则/校验点**（能力上限、代价、平衡锁、生效条件）。\n",
        "输出 JSON 结构：",
        '{"rules": [{"rule_name": 规则名, "rule_type": "cap", "rule_content": 原文, '
        '"subject_entity_name": 适用实体名, "subject_ability": 适用能力, '
        '"valid_from_chapter": 起章, "valid_to_chapter": 0}]}\n',
        "要求：\n",
        "  1. rule_type 选自 cap/cost/balance_lock/condition/other；\n",
        "  2. rule_content 尽量引用原文（能力上限/代价/平衡锁）；\n",
        "  3. 原文没提的字段填空串/null；只输出 JSON。\n\n",
        "抽取规则补充：\n",
        "规则类型通用说明：\n",
        "- cap：上限/天花板，能力、权力、规则的最高限制\n",
        "- cost：代价/消耗，执行能力、行动需要付出的成本\n",
        "- balance_lock：平衡约束，限制力量失衡的规则\n",
        "- condition：生效条件，触发规则、能力的前提\n",
        "- other：其他规则，包括社会规则、法律、制度、自然规律\n\n",
        "规则判定边界：\n",
        "1. 只抽**设定规则/校验点**：能力上限、代价、平衡锁、生效条件等。人物特质、外貌、普通事件不是规则。\n",
        "2. subject_entity_name 用原文出现的实体名；subject_ability 是该规则约束的能力/术式，无则空串。\n",
        "3. rule_content 尽量引用原文原句，避免改写。\n\n",
    ]
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_RULE_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
