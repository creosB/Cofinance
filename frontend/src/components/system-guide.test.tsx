import { describe,it,expect } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { SystemGuide } from './run-map'

describe('system guide',()=>{
 it('describes the current chat path: selected narrator for two roles, News Researcher in Python',()=>{
  const html=renderToStaticMarkup(<SystemGuide/>)
  expect(html).not.toContain('Base narrator')
  expect(html).toContain('The selected LFM3 narrator writes the Data Analyst report and the Team Lead answer')
  expect(html).toContain('The News Researcher is Python, no model call.')
 })
})
