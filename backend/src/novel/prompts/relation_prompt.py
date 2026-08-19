# -*- coding: utf-8 -*-
"""
relation 抽取专属 Prompt + JSON Schema（子任务 04）。

职责：从章节场景抽"实体间关系"——source/target 按名、relation_type（枚举）、weight、valid_period、desc。

设计（照 03 模板）：
  - `RelationOutput`/`RelationItem` 强校验（relation_type 在枚举内、weight 1/2、valid_period 枚举）；
  - `result_field = "relations"`；
  - source/target 用实体名（persist 期 name→entity_id 解析，且带时效 start/end_chapter）；
  - `build_prompt(scene_text, few_shot=True)` 默认追加 3-shot 跨题材极简示例（2正1错，覆盖玄幻修仙/科幻超能）。
"""
from pydantic import BaseModel, Field, field_validator

from novel.prompts.base import BASE_SYSTEM_PROMPT

# 关系类型枚举（与 002 entity_relation.relation_type 约定一致，勿改）
RELATION_TYPES = (
    "possess", "master", "contain", "restrain", "undertake",
    "belong_to", "host_bind", "alliance", "enmity", "family",
)
_VALID_PERIODS = ("permanent", "temporary", "reversed")
# 二阶段 03：关系趋势枚举（明暗关系的方向）
_RELATION_TRENDS = ("升温", "降温", "稳定", "破裂")

# ========== 3-shot 跨题材极简示例（2正1错，全部自包含） ==========
# 原则：
#   - 每个示例的【正确输出】全部可由【输入文本】直接推出，不依赖示例外的原著上下文；
#   - 两个正例分别取材《凡人修仙传·第一章》（玄幻古代修仙）与《超神机械师·001 初生》（科幻现代超能）；
#   - 一个反例以「错误→正确」极简格式集中展示全部常见关系抽取错误（二阶推理/方向颠倒/代词/编造/类型/时效/事件当关系/重复）。
_RELATION_FEW_SHOT = """\
【示例 1（正例·玄幻古代修仙）】
【输入文本】
三叔是韩立的亲三叔，在附近小城的酒楼当大掌柜，前不久正式成为七玄门的外门弟子，能推举适龄孩童去参加七玄门招收内门弟子的考验。韩父是韩立的父亲，一向老实巴交，最终答应让韩立去参加考核，坐在一边抽着旱烟杆。
【正确输出】
{"relations": [
  {"source": "三叔", "target": "韩立", "relation_type": "family", "weight": 1, "valid_period": "permanent", "desc": "韩立的亲三叔", "surface_relation": "亲三叔，推举其赴考", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "韩父", "target": "韩立", "relation_type": "family", "weight": 1, "valid_period": "permanent", "desc": "韩立的父亲", "surface_relation": "父子，应允其赴考", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "三叔", "target": "七玄门", "relation_type": "belong_to", "weight": 1, "valid_period": "temporary", "desc": "七玄门外门弟子", "surface_relation": "外门弟子", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "韩立", "target": "七玄门招收内门弟子的考验", "relation_type": "undertake", "weight": 1, "valid_period": "temporary", "desc": "被推举参加内门弟子考验", "surface_relation": "", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "韩父", "target": "旱烟杆", "relation_type": "possess", "weight": 2, "valid_period": "temporary", "desc": "使用的抽烟器具", "surface_relation": "", "inner_relation": "", "relation_trend": "稳定"}
]}

【示例 2（正例·科幻现代超能）】
【输入文本】
海拉是萌芽组织瓦尔基里实验室的主管，实验负责人林维贤是她的下属研究员。瓦尔基里溶液是萌芽组织的基因药剂，韩萧是被注射了瓦尔基里溶液的试验体，被关押在实验室。
【正确输出】
{"relations": [
  {"source": "海拉", "target": "萌芽组织", "relation_type": "belong_to", "weight": 1, "valid_period": "temporary", "desc": "瓦尔基里实验室主管", "surface_relation": "实验室主管", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "海拉", "target": "林维贤", "relation_type": "master", "weight": 1, "valid_period": "temporary", "desc": "林维贤是海拉的下属研究员", "surface_relation": "上下级，海拉为主管", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "萌芽组织", "target": "瓦尔基里溶液", "relation_type": "possess", "weight": 1, "valid_period": "temporary", "desc": "萌芽组织的基因药剂", "surface_relation": "组织研发的药剂", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "萌芽组织", "target": "韩萧", "relation_type": "restrain", "weight": 1, "valid_period": "temporary", "desc": "韩萧被关押在实验室", "surface_relation": "关押试验体", "inner_relation": "将韩萧视为研究对象/威胁（依据：被关押在实验室）", "relation_trend": "稳定"}
]}

【示例 3（反例·常见错误汇总，禁止照做）】
【输入文本】
韩立认识三叔，三叔是七玄门外门弟子，听说七玄门在招收内门弟子。韩父是韩立的父亲，坐在一边抽着旱烟杆。
【常见错误 → 正确做法（以下内容禁止照做）】
- 二阶间接推理："韩立认识三叔"+"三叔 belong_to 七玄门" → 推"韩立 belong_to 七玄门" ✗ 只抽直接写明的一阶关系
- 方向颠倒："七玄门 belong_to 三叔" ✗ 主动方为 source、被动方为 target
- 用代词作 source/target："他" ✗ 归并到已明确的实体
- 编造归属："韩立 possess 旱烟杆" ✗ 原文是"韩父…抽着旱烟杆"，应为 韩父 possess 旱烟杆
- 类型误判：把"韩父 韩立 父子"标成 alliance ✗ 血亲必须 family
- 时效误判："三叔 belong_to 七玄门"标 permanent ✗ 外门弟子身份可变化，应 temporary
- 事件当关系："韩立去参加考核" ✗ 事件/动作不是关系
- 重复输出：同一对实体同类关系输出多条 ✗ 每对每类只 1 条
- 明暗混写：把内心态度（"韩父担忧韩立前途"）写进 surface_relation ✗ surface 只放原文可证的公开关系，内心态度放 inner_relation
- inner 无锚点硬猜："三叔嫉妒韩父"（原文未提）✗ inner_relation 必须有原文依据，无依据填空串
- 趋势误判：无依据乱标"破裂" ✗ relation_trend 依本段互动判断，无法判断标"稳定"
【正确输出】
{"relations": [
  {"source": "韩父", "target": "韩立", "relation_type": "family", "weight": 1, "valid_period": "permanent", "desc": "父子", "surface_relation": "父子", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "三叔", "target": "七玄门", "relation_type": "belong_to", "weight": 1, "valid_period": "temporary", "desc": "七玄门外门弟子", "surface_relation": "外门弟子", "inner_relation": "", "relation_trend": "稳定"},
  {"source": "韩父", "target": "旱烟杆", "relation_type": "possess", "weight": 2, "valid_period": "temporary", "desc": "使用的抽烟器具", "surface_relation": "", "inner_relation": "", "relation_trend": "稳定"}
]}
"""


class RelationItem(BaseModel):
    """单条实体关系：source/target 按名 + 类型 + 权重 + 时效 + 描述 + 明暗两层与趋势。"""

    source: str = Field(min_length=1, description="主体实体名")
    source_fragment: str = Field(default="", description="支撑本条抽取的原文片段（verbatim；可选，空则跳过锚定校验）")
    target: str = Field(min_length=1, description="客体实体名")
    relation_type: str = Field(description=f"关系类型，选自枚举：{'/'.join(RELATION_TYPES)}")
    weight: int = Field(default=2, ge=1, le=2, description="关系强度：1 核心 / 2 次要")
    valid_period: str = Field(default="temporary", description="时效：permanent/temporary/reversed")
    desc: str = Field(default="", description="关系补充描述")
    # 二阶段 03：明暗两层 + 趋势（L4 关系质感）
    surface_relation: str = Field(default="", description="对外公开的表层关系（原文可证）")
    inner_relation: str = Field(default="", description="双方内心真实态度与隐情（合理推断，需 source_fragment 支撑）")
    relation_trend: str = Field(default="稳定", description=f"关系趋势：{'/'.join(_RELATION_TRENDS)}")

    @field_validator("relation_type")
    @classmethod
    def _type_must_be_in_enum(cls, v: str) -> str:
        if v not in RELATION_TYPES:
            raise ValueError(f"非法关系类型 {v!r}，可选 {'/'.join(RELATION_TYPES)}")
        return v

    @field_validator("valid_period")
    @classmethod
    def _period_must_be_in_enum(cls, v: str) -> str:
        if v not in _VALID_PERIODS:
            raise ValueError(f"非法时效 {v!r}，可选 {'/'.join(_VALID_PERIODS)}")
        return v

    @field_validator("relation_trend")
    @classmethod
    def _trend_must_be_in_enum(cls, v: str) -> str:
        if v not in _RELATION_TRENDS:
            raise ValueError(f"非法关系趋势 {v!r}，可选 {'/'.join(_RELATION_TRENDS)}")
        return v


class RelationOutput(BaseModel):
    """relation 抽取的整体输出契约。"""

    relations: list[RelationItem] = Field(default_factory=list)


def build_prompt(scene_text: str, *, few_shot: bool = True) -> dict:
    """组装 relation 抽取的 LLM 消息。

    :param scene_text: 章节场景原文（缩窗重试时传入截断后的前半文本）
    :param few_shot: 是否追加 3-shot 跨题材示例。默认开启（llm_runner 单参调用保持兼容）；
        对成本极度敏感时可首跑 few_shot=False、仅在缩窗重试时开启 few_shot=True。
    :return: {"system_prompt", "prompt"} 供 llm_runner / BaseLLMAdapter.invoke 使用
    """
    parts = [
        "任务：从以下文本中提取实体之间的**关系**。\n",
        "关系类型枚举：",
        f"{'/'.join(RELATION_TYPES)}\n",
        "输出 JSON 结构：",
        '{"relations": [{"source": 主体名, "target": 客体名, "relation_type": 类型, '
        '"weight": 1或2, "valid_period": "permanent|temporary|reversed", "desc": 描述, '
        '"surface_relation": 表层关系, "inner_relation": 内心态度, "relation_trend": 趋势}]}\n',
        "要求：\n",
        "  1. source/target 用文本中出现的实体名；\n",
        "  2. relation_type 必须选自枚举；weight 1=核心强关系、2=次要弱关系；\n",
        "  3. 原文没提的关系不要编造；只输出 JSON。\n\n",
        "明暗两层与趋势（关系质感）：\n",
        "- surface_relation：对外公开、原文可证的表层关系（如\"师徒，尊师重道\"）。\n",
        "- inner_relation：双方内心真实态度与隐情（如\"师父实则利用主角体质\"）——属合理推断，\n",
        "  必须有原文依据并把支撑片段写入 source_fragment；无依据填空串，禁止硬猜。\n",
        "- relation_trend：关系正在 升温/降温/稳定/破裂（依本段互动判断；无法判断填\"稳定\"）。\n\n",
        "抽取规则补充：\n",
        "关系类型通用语义说明：\n",
        "- possess：持有/拥有，适用于物品、资产、权力的归属\n",
        "- master：主从/上下级，适用于师徒、雇佣、统领、从属关系\n",
        "- contain：包含/组成，适用于势力成员、物品组件、集合包含\n",
        "- restrain：克制/约束，适用于能力克制、规则限制、人物牵制\n",
        "- undertake：承担/执行，适用于任务、职责、使命的承接\n",
        "- belong_to：隶属于，适用于人物归属势力、物品归属人物、机构从属\n",
        "- host_bind：宿主绑定，仅用于超自然共生、灵魂绑定类关系；现实题材禁用\n",
        "- alliance：联盟/合作，适用于朋友、同伴、合作、同盟、同事关系\n",
        "- enmity：敌对/矛盾，适用于敌人、竞争关系、仇恨对立\n",
        "- family：亲属/婚姻，适用于所有血缘、婚姻、家族关系\n\n",
        "强制约束：\n",
        "1. 仅抽取文本中直接写明的一阶关系，禁止通过推理生成二阶间接关系（如A认识B，B认识C，禁止生成A-C关系）。\n",
        "2. 关系方向严格对应语义：主动方为 source，被动方为 target，禁止主客颠倒。\n",
        "3. valid_period 规则：permanent 用于与生俱来、不可改变的关系（如血缘、出身）；temporary 用于剧情中可变化的关系；reversed 用于本段明确反转的原有关系。\n\n",
    ]
    if few_shot:
        parts.append("【Few-shot 示例】（输出格式参考；每个示例的【正确输出】均可直接由【输入文本】推出）：\n\n")
        parts.append(_RELATION_FEW_SHOT)
        parts.append("\n\n")
    parts.append(f"文本内容：\n{scene_text}")
    return {"system_prompt": BASE_SYSTEM_PROMPT, "prompt": "".join(parts)}
