<script setup lang="ts">
import type { Recurrence } from '../types'
import { wallTime } from '../services/api'
const props=defineProps<{modelValue:Recurrence|null|undefined;startAt:string|null;timezone:string;disabled?:boolean}>()
const emit=defineEmits<{(e:'update:modelValue',value:Recurrence|null):void}>()
const days=['周一','周二','周三','周四','周五','周六','周日']
function choose(value:string){
 if(value==='none'){emit('update:modelValue',null);return}
 const local=props.startAt?wallTime(props.startAt,props.timezone).slice(0,10):''
 const weekday=local?(new Date(local+'T12:00:00Z').getUTCDay()+6)%7:0
 emit('update:modelValue',{frequency:value as 'daily'|'weekly',interval:1,weekdays:value==='weekly'?[weekday]:[],until:null,timezone:props.timezone})
}
function update(patch:Partial<Recurrence>){if(props.modelValue)emit('update:modelValue',{...props.modelValue,...patch})}
function toggle(day:number,checked:boolean){if(props.modelValue)update({weekdays:checked?[...props.modelValue.weekdays,day].sort():props.modelValue.weekdays.filter(d=>d!==day)})}
</script>
<template>
 <div class="recurrence-editor">
  <label>重复<select :value="modelValue?.frequency||'none'" :disabled="disabled" @change="choose(($event.target as HTMLSelectElement).value)"><option value="none">不重复</option><option value="daily">每天</option><option value="weekly">每周</option></select></label>
  <template v-if="modelValue">
   <div class="form-grid"><label>重复间隔<input type="number" min="1" max="12" :value="modelValue.interval" :disabled="disabled" required @input="update({interval:Number(($event.target as HTMLInputElement).value)})"/><small>每 {{modelValue.interval}} {{modelValue.frequency==='daily'?'天':'周'}}重复</small></label><label>重复截止日期<input type="date" :value="modelValue.until||''" :disabled="disabled" :min="startAt?wallTime(startAt,modelValue.timezone).slice(0,10):undefined" @input="update({until:($event.target as HTMLInputElement).value||null})"/><small>包含当天；留空表示持续重复</small></label></div>
   <div v-if="modelValue.frequency==='weekly'" class="repeat-weekdays" role="group" aria-label="重复星期"><label v-for="(label,index) in days" :key="index" class="checkbox-label"><input type="checkbox" :checked="modelValue.weekdays.includes(index)" :disabled="disabled" :required="!disabled&&!modelValue.weekdays.length&&index===0" @change="toggle(index,($event.target as HTMLInputElement).checked)"/>{{label}}</label></div>
   <small class="muted">按 {{modelValue.timezone}} 的当地时间重复。</small>
  </template>
 </div>
</template>
<style scoped>
.recurrence-editor{display:flex;flex-direction:column;gap:14px;border-top:1px solid var(--border);padding-top:15px}.recurrence-editor>label,.form-grid>label{display:flex;flex-direction:column;gap:8px;font-size:11px;color:#7e8d72}.repeat-weekdays{display:flex;gap:12px;flex-wrap:wrap}.repeat-weekdays label{display:flex;align-items:center;gap:5px;font-size:11px;color:#7e8d72}.repeat-weekdays input{accent-color:#678557;padding:0}.recurrence-editor small{font-size:10px;line-height:1.6}
</style>
