import { describe,it,expect } from 'vitest'
import { wallTime,toISO,hours } from './api'
describe('timezone conversion',()=>{
 it('round-trips Shanghai independently of browser timezone',()=>{expect(toISO('2026-11-06T20:00','Asia/Shanghai')).toBe('2026-11-06T12:00:00.000Z');expect(wallTime('2026-11-06T12:00:00Z','Asia/Shanghai')).toBe('2026-11-06T20:00:00')})
 it('uses the correct winter and summer offsets',()=>{expect(toISO('2026-01-10T09:00','America/New_York')).toBe('2026-01-10T14:00:00.000Z');expect(toISO('2026-07-10T09:00','America/New_York')).toBe('2026-07-10T13:00:00.000Z')})
 it('rejects nonexistent DST wall time',()=>{expect(()=>toISO('2026-03-08T02:30','America/New_York')).toThrow('不存在')})
 it('formats effort without spurious decimals',()=>{expect(hours(90)).toBe('1.5h');expect(hours(120)).toBe('2h')})
})
