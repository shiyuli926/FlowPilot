export async function api<T=any>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch('/api'+path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)})
  if(!response.ok){let message='请求失败';try{const r=await response.json();message=typeof r.detail==='string'?r.detail:r.detail?.map((x:any)=>x.msg).join('；')||message}catch{}throw new Error(message)}
  return response.json()
}
export function wallTime(iso:string,tz:string):string {
  const parts=new Intl.DateTimeFormat('en-CA',{timeZone:tz,year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',second:'2-digit',hourCycle:'h23'}).formatToParts(new Date(iso))
  const p=Object.fromEntries(parts.map(x=>[x.type,x.value]));return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`
}
export function toISO(wall:string,tz:string):string {
  const target=new Date(wall+'Z').getTime();if(!Number.isFinite(target))throw new Error('请输入有效日期时间')
  let instant=target
  for(let i=0;i<4;i++){const shown=new Date(wallTime(new Date(instant).toISOString(),tz)+'Z').getTime();const delta=target-shown;if(delta===0)return new Date(instant).toISOString();instant+=delta}
  throw new Error('此时间在所选时区不存在（夏令时切换），请调整')
}
export const hours=(m:number)=>`${+(m/60).toFixed(1)}h`
