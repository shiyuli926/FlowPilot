export interface Task { id:string; title:string; project_id:string|null; deadline:string|null; deadline_date:string|null; deadline_type:'hard'|'soft'|'none'; priority:number; estimated_minutes:number; remaining_minutes:number; status:'todo'|'in_progress'|'done'; earliest_start:string|null; splittable:boolean; min_block_minutes:number; max_block_minutes:number; preferred_period:'any'|'morning'|'afternoon'|'evening'; dependencies:string[]; notes:string; source_text?:string|null }
export interface CalendarEvent {id:string;title:string;start_at:string;end_at:string;color:string;source:string}
export interface Project {id:string;name:string;color:string}
export interface Block {id:string;task_id:string;start_at:string;end_at:string;is_locked:boolean;status:string;reason:string}
export interface Risk {task_id:string;title:string;level:string;scheduled_minutes:number;unscheduled_minutes:number;missing_before_deadline:number;deadline_needs_confirmation:boolean;message:string}
export interface Settings {timezone:string;horizon_days:number;engine:'heuristic'|'cp-sat';availability:{weekday:number;start_time:string;end_time:string}[]}
export interface Snapshot {tasks:Task[];events:CalendarEvent[];projects:Project[];settings:Settings;schedule:{blocks:Block[];risks:Risk[];run_id:string|null};revision:number;can_undo:boolean}
export interface Preview {id:string;blocks:Block[];risks:Risk[];engine:string;changes:{added:number;removed:number;retained:number}}
export interface Parsed {provider:string;notice:string;items:{task:Task;issues:string[];confidence:number}[]}
