<script setup lang="ts">
import { computed } from 'vue'
import { Plus, Trash2 } from 'lucide-vue-next'
import type { Settings } from '../types'

const availability = defineModel<Settings['availability']>({ required: true })
const weekdays = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
const days = computed(() => weekdays.map((label, weekday) => ({
  label,
  weekday,
  ranges: availability.value.flatMap((rule, index) => rule.weekday === weekday ? [{ rule, index }] : []),
})))
function period(start: string) {
  const hour = Number(start.slice(0, 2))
  return hour < 12 ? '上午' : hour < 18 ? '下午' : '晚上'
}
function addRange(weekday: number) {
  const existing = availability.value.filter(rule => rule.weekday === weekday)
  const defaults = [
    { start_time: '09:00', end_time: '12:00' },
    { start_time: '14:00', end_time: '18:00' },
    { start_time: '19:00', end_time: '21:00' },
  ]
  const next = defaults.find(range => !existing.some(rule => period(rule.start_time) === period(range.start_time))) || defaults[0]!
  const updated = [...availability.value]
  const insertAt = updated.findIndex(rule => rule.weekday === weekday && rule.start_time > next.start_time)
  updated.splice(insertAt < 0 ? updated.length : insertAt, 0, { weekday, ...next })
  availability.value = updated
}
function removeRange(index: number) {
  availability.value = availability.value.filter((_, i) => i !== index)
}
</script>

<template>
  <div class="settings-section-title">
    <div>
      <h3>每周可用时段</h3>
      <p>每天一行，集中设置上午、下午和晚上的可用时间；课表与会议将自动扣除。</p>
    </div>
  </div>
  <div class="availability-week" aria-label="每周可用时段">
    <div v-for="day in days" :key="day.weekday" class="availability-day">
      <strong class="availability-weekday">{{ day.label }}</strong>
      <div class="availability-ranges">
        <div v-for="({ rule, index }, order) in day.ranges" :key="index" class="availability-range">
          <span class="availability-period">{{ period(rule.start_time) }}</span>
          <input type="time" v-model="rule.start_time" step="900" required :aria-label="`${day.label}时段${order + 1}开始时间`" />
          <span class="availability-separator">–</span>
          <input type="time" v-model="rule.end_time" step="900" required :aria-label="`${day.label}时段${order + 1}结束时间`" />
          <button type="button" class="icon-button" :aria-label="`删除${day.label}时段${order + 1}`" title="删除时段" @click="removeRange(index)"><Trash2 :size="13" /></button>
        </div>
        <span v-if="!day.ranges.length" class="availability-empty">未设置可用时间</span>
      </div>
      <button type="button" class="icon-button availability-add" :aria-label="`添加${day.label}时段`" title="添加时段" @click="addRange(day.weekday)"><Plus :size="15" /></button>
    </div>
  </div>
</template>
