import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '../services/api'
import type {Snapshot,Preview} from '../types'
export const usePlanner=defineStore('planner',()=>{
 const data=ref<Snapshot|null>(null),busy=ref(false),error=ref(''),toast=ref(''),dirty=ref(false),preview=ref<Preview|null>(null)
 let timer:ReturnType<typeof setTimeout>
 function notify(message:string){toast.value=message;clearTimeout(timer);timer=setTimeout(()=>toast.value='',4500)}
 async function load(){data.value=await api<Snapshot>('/bootstrap')}
 async function run<T>(action:()=>Promise<T>):Promise<T|undefined>{if(busy.value)return;busy.value=true;error.value='';try{return await action()}catch(e){error.value=e instanceof Error?e.message:'发生错误'}finally{busy.value=false}}
 async function mutate(path:string,method:string,body?:unknown){await api(path,method,body);dirty.value=true;preview.value=null;await load();notify('已保存，点击自动规划更新日程')}
 async function plan(){preview.value=await api<Preview>('/schedule/preview','POST',{})}
 async function commit(){if(!preview.value)return;await api('/schedule/commit','POST',{preview_id:preview.value.id});preview.value=null;dirty.value=false;await load();notify('新计划已发布')}
 return {data,busy,error,toast,dirty,preview,notify,load,run,mutate,plan,commit}
})
