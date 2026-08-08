/* updateInterval 纯函数单元测试：展示换算 / 输入转小时 / 单位切换。 */
import { describe, expect, it } from 'vitest'
import {
  convertUnit,
  displayToHours,
  intervalToDisplay,
  MAX_YEAR_HOURS,
} from './updateInterval'

describe('intervalToDisplay（后端小时 → 面板展示值+单位）', () => {
  it('整除按指定单位展示', () => {
    expect(intervalToDisplay(24, 'hour')).toEqual({ value: '24', unit: 'hour' })
    expect(intervalToDisplay(24, 'day')).toEqual({ value: '1', unit: 'day' })
    expect(intervalToDisplay(168, 'week')).toEqual({ value: '1', unit: 'week' })
    expect(intervalToDisplay(720, 'month')).toEqual({ value: '1', unit: 'month' })
  })
  it('无法整除回退小时单位', () => {
    expect(intervalToDisplay(36, 'day')).toEqual({ value: '36', unit: 'hour' })
  })
  it('0（旧版关闭值）回退默认 24 小时', () => {
    expect(intervalToDisplay(0, 'hour')).toEqual({ value: '24', unit: 'hour' })
  })
  it('未知单位回退小时', () => {
    expect(intervalToDisplay(24, 'minute')).toEqual({ value: '24', unit: 'hour' })
  })
  it('负值按 0 处理（回退 24 小时，单位保留）', () => {
    expect(intervalToDisplay(-1, 'day')).toEqual({ value: '1', unit: 'day' })
    expect(intervalToDisplay(-1, 'hour')).toEqual({ value: '24', unit: 'hour' })
  })
})

describe('displayToHours（面板输入 → 小时）', () => {
  it('各单位换算', () => {
    expect(displayToHours('1', 'day')).toBe(24)
    expect(displayToHours('2', 'week')).toBe(336)
    expect(displayToHours('0.5', 'month')).toBe(360)
    expect(displayToHours('24', 'hour')).toBe(24)
  })
  it('非法输入返回 -1', () => {
    expect(displayToHours('', 'hour')).toBe(-1)
    expect(displayToHours('abc', 'day')).toBe(-1)
    expect(displayToHours('-3', 'hour')).toBe(-1)
  })
  it('未知单位按小时', () => {
    expect(displayToHours('5', 'minute')).toBe(5)
  })
  it('空格容忍', () => {
    expect(displayToHours(' 24 ', 'hour')).toBe(24)
  })
})

describe('convertUnit（单位切换保持间隔）', () => {
  it('小时→日：24h 变 1 日', () => {
    expect(convertUnit('24', 'hour', 'day')).toBe('1')
  })
  it('日→小时：2 日变 48h', () => {
    expect(convertUnit('2', 'day', 'hour')).toBe('48')
  })
  it('月→星期：1 月 ≈ 4.29 星期（2 位小数）', () => {
    expect(convertUnit('1', 'month', 'week')).toBe('4.29')
  })
  it('星期→月 回程：4.29 星期 = 1 月（720.72h/720 舍入为 1）', () => {
    expect(convertUnit('4.29', 'week', 'month')).toBe('1')
  })
  it('非法输入原样返回', () => {
    expect(convertUnit('', 'hour', 'day')).toBe('')
    expect(convertUnit('abc', 'hour', 'day')).toBe('abc')
  })
})

describe('常量', () => {
  it('上限 = 365 天', () => {
    expect(MAX_YEAR_HOURS).toBe(8760)
  })
})
