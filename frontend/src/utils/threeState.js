// 三态标注纯函数（子任务 05 展示层）——"可核验的饱满度"的前端落地。
// fact=原文直证 / inference=合理推断 / review=待复核。
// 映射到既有 cf-* 弱视觉：inference→琥珀(cf-low)、review→灰(cf-null)、fact→蓝(cf-ok)。
// 纯函数无副作用，node 可直接校验（前端无测试框架的兜底）。

/** 三态 → cf-badge 样式类。非法值/缺省一律回退 fact（蓝）。 */
export function threeStateClass(state) {
  if (state === 'inference') return 'cf-badge cf-low'    // 琥珀：合理推断（主观层）
  if (state === 'review') return 'cf-badge cf-null'     // 灰：待复核（无锚点/未确认）
  return 'cf-badge cf-ok'                               // 蓝：原文直证（含缺省 fact）
}

/** 三态 → 中文文案（徽标显示 + tooltip）。 */
export function threeStateText(state) {
  if (state === 'inference') return '合理推断'
  if (state === 'review') return '待复核'
  return '原文直证'
}

/** 三态 → tooltip 说明（悬停解释口径）。 */
export function threeStateTitle(state) {
  if (state === 'inference') return '三态：合理推断（主观层，需原文锚点）'
  if (state === 'review') return '三态：待复核（未确认/弱锚点）'
  return '三态：原文直证'
}
